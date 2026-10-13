import csv
import io
import re
from datetime import date, datetime

from flask import Blueprint, Response, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError

from app.decorators import roles_required
from app.extensions import db
from app.helpers import audit
from app.models import AuditLog, Client, Collaborator, PasswordReset, Permission, Role, User
from app.security_controls import (
    BUILTIN_ROLES,
    DATA_SCOPES,
    default_permission_scope,
    list_temporary_grants,
    permission_scope,
    revoke_temporary_client_grant,
    save_permission_scope,
    save_security_policy,
    save_temporary_client_grant,
    security_policy,
    set_two_factor,
    two_factor_enabled,
)

bp = Blueprint("security_admin", __name__, url_prefix="/settings/security")


def _parse_date(value):
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _audit_query():
    query = AuditLog.query

    user_id = request.args.get("user_id", type=int)
    action = (request.args.get("action") or "").strip()
    entity = (request.args.get("entity") or "").strip()
    q = (request.args.get("q") or "").strip()
    starts = _parse_date(request.args.get("starts"))
    ends = _parse_date(request.args.get("ends"))

    if user_id:
        query = query.filter(AuditLog.user_id == user_id)
    if action:
        query = query.filter(AuditLog.action == action)
    if entity:
        query = query.filter(AuditLog.entity == entity)
    if starts:
        query = query.filter(
            AuditLog.created_at >= datetime.combine(starts, datetime.min.time())
        )
    if ends:
        query = query.filter(
            AuditLog.created_at <= datetime.combine(ends, datetime.max.time())
        )
    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(
                AuditLog.action.ilike(like),
                AuditLog.entity.ilike(like),
                AuditLog.entity_id.ilike(like),
                AuditLog.reason.ilike(like),
                AuditLog.ip_address.ilike(like),
                AuditLog.before_json.ilike(like),
                AuditLog.after_json.ilike(like),
            )
        )

    return query, {
        "user_id": user_id,
        "action": action,
        "entity": entity,
        "q": q,
        "starts": starts,
        "ends": ends,
    }


@bp.route("/")
@login_required
@roles_required("superadmin", "admin", "manager")
def index():
    roles = Role.query.order_by(Role.label).all()
    user_rows = [
        {
            "user": user,
            "two_factor": two_factor_enabled(user),
        }
        for user in User.query.order_by(User.active.desc(), User.name.asc()).all()
    ]
    permissions = Permission.query.order_by(Permission.module, Permission.label).all()

    scope_rows = []
    for role in roles:
        role_permissions = permissions if role.name == "superadmin" else sorted(
            role.permissions,
            key=lambda row: (row.module, row.label),
        )
        scope_rows.append(
            {
                "role": role,
                "builtin": role.name in BUILTIN_ROLES,
                "permissions": [
                    {
                        "permission": permission,
                        "scope": (
                            default_permission_scope(role.name, permission.code)
                            if role.name in BUILTIN_ROLES
                            else permission_scope(
                                type("ScopeUser", (), {"role": role})(),
                                permission.code,
                            )
                        ),
                    }
                    for permission in role_permissions
                ],
            }
        )

    grants = []
    for grant in list_temporary_grants():
        user = db.session.get(User, grant["user_id"])
        client = db.session.get(Client, grant["client_id"])
        grants.append(
            {
                **grant,
                "user": user,
                "client": client,
            }
        )

    return render_template(
        "security_admin/index.html",
        policy=security_policy(),
        user_rows=user_rows,
        scope_rows=scope_rows,
        data_scopes=DATA_SCOPES,
        temporary_grants=grants,
        temporary_clients=(
            Client.query
            .filter(Client.record_type == "cliente")
            .order_by(Client.business_name)
            .all()
        ),
    )


@bp.route("/policy", methods=["POST"])
@login_required
@roles_required("superadmin", "admin", "manager")
def policy_update():
    try:
        save_security_policy(
            {
                "failed_attempt_limit": request.form.get("failed_attempt_limit", type=int),
                "lock_minutes": request.form.get("lock_minutes", type=int),
                "session_timeout_minutes": request.form.get("session_timeout_minutes", type=int),
                "allow_remember_me": bool(request.form.get("allow_remember_me")),
                "allow_2fa": bool(request.form.get("allow_2fa")),
            }
        )
    except (TypeError, ValueError):
        db.session.rollback()
        flash("Revisa los valores de la política de seguridad.", "danger")
        return redirect(url_for("security_admin.index"))

    audit(
        "actualizar_politica_seguridad",
        "SystemSetting",
        after=security_policy(),
    )
    db.session.commit()
    flash("Política de seguridad actualizada.", "success")
    return redirect(url_for("security_admin.index") + "#policy")


@bp.route("/users/<int:user_id>/status", methods=["POST"])
@login_required
@roles_required("superadmin", "admin", "manager")
def user_status(user_id):
    user = db.get_or_404(User, user_id)
    desired = (request.form.get("status") or "").strip()
    reason = (request.form.get("reason") or "").strip()

    if user.id == current_user.id and desired == "inactive":
        flash("No puedes desactivar tu propio usuario.", "danger")
        return redirect(url_for("security_admin.index") + "#users")

    if user.is_superadmin and not current_user.is_superadmin:
        abort(403)

    if desired not in {"active", "inactive"}:
        abort(400)

    before = {"active": user.active}
    if desired == "inactive":
        if not reason:
            flash("El motivo es obligatorio para desactivar un usuario.", "danger")
            return redirect(url_for("security_admin.index") + "#users")
        user.active = False
        user.failed_attempts = 0
        user.locked_until = None
        action = "desactivar_usuario"
    else:
        user.active = True
        action = "reactivar_usuario"

    audit(
        action,
        "User",
        user.id,
        before=before,
        after={"active": user.active},
        reason=reason or "Reactivación administrativa",
    )
    db.session.commit()
    flash(
        "Usuario desactivado. Su sesión será revocada en la próxima solicitud."
        if not user.active
        else "Usuario reactivado.",
        "success",
    )
    return redirect(url_for("security_admin.index") + "#users")


@bp.route("/users/<int:user_id>/unlock", methods=["POST"])
@login_required
@roles_required("superadmin", "admin", "manager")
def unlock_user(user_id):
    user = db.get_or_404(User, user_id)
    if user.is_superadmin and not current_user.is_superadmin:
        abort(403)

    before = {
        "failed_attempts": user.failed_attempts,
        "locked_until": user.locked_until,
    }
    user.failed_attempts = 0
    user.locked_until = None
    audit(
        "desbloquear_usuario",
        "User",
        user.id,
        before=before,
        after={"failed_attempts": 0, "locked_until": None},
    )
    db.session.commit()
    flash("Bloqueo del usuario restablecido.", "success")
    return redirect(url_for("security_admin.index") + "#users")



# Administración de identidad: confirmaciones verificadas SIEMPRE en backend.
_ADMIN_EMAIL_PATTERN = re.compile(r"^[^\\s@]+@[^\\s@]+\\.[^\\s@]+$")


def _can_manage_user_identity(target):
    if target.id == current_user.id:
        return False, "Por seguridad no puedes gestionar tu propia cuenta desde aquí."
    if target.is_superadmin:
        return False, "Las cuentas Superadministrador están protegidas."
    if target.role and target.role.name in {"admin", "manager"} and not current_user.is_superadmin:
        return False, "Solo el Superadministrador puede gestionar administradores y gerentes."
    return True, None


def _administrative_password_valid():
    password = request.form.get("admin_password") or ""
    return bool(password and current_user.check_password(password))


def _referenced_in_business_tables(target):
    """No borrar usuarios o colaboradores con referencias: preserva el historial.

    Comprueba todas las FK registradas por SQLAlchemy, incluyendo tablas de
    asociaciones. Solo se permite la FK del registro de colaborador y
    los tokens de recuperación, que se eliminan explícitamente.
    """
    collaborator = Collaborator.query.filter_by(user_id=target.id).first()
    linked_ids = {"users": target.id}
    if collaborator:
        linked_ids["collaborators"] = collaborator.id

    for table in db.metadata.tables.values():
        for col in table.columns:
            for fk in col.foreign_keys:
                referenced_table = fk.column.table.name
                if referenced_table not in linked_ids:
                    continue
                if referenced_table == "users":
                    if table.name == "collaborators" and col.name == "user_id":
                        continue
                    if table.name == "password_resets" and col.name == "user_id":
                        continue
                exists = (
                    db.session.query(db.literal(1))
                    .select_from(table)
                    .filter(col == linked_ids[referenced_table])
                    .limit(1)
                    .first()
                )
                if exists:
                    return True
    return False


@bp.route("/users/<int:user_id>/email", methods=["POST"])
@login_required
@roles_required("superadmin", "admin")
def change_user_email(user_id):
    target = db.get_or_404(User, user_id)
    allowed, message = _can_manage_user_identity(target)
    if not allowed:
        flash(message, "danger")
        return redirect(url_for("security_admin.index") + "#users")

    if not _administrative_password_valid():
        flash("La contraseña del administrador no es correcta.", "danger")
        return redirect(url_for("security_admin.index") + "#users")

    new_email = (request.form.get("new_email") or "").strip().lower()
    if len(new_email) > 190 or not _ADMIN_EMAIL_PATTERN.fullmatch(new_email):
        flash("Introduce un correo electrónico válido (máximo 190 caracteres).", "danger")
        return redirect(url_for("security_admin.index") + "#users")

    if new_email == target.email.lower():
        flash("El correo ingresado es el mismo que ya tiene la cuenta.", "info")
        return redirect(url_for("security_admin.index") + "#users")

    duplicated = User.query.filter(
        db.func.lower(User.email) == new_email, User.id != target.id
    ).first()
    if duplicated:
        flash("Ese correo ya pertenece a otro usuario.", "danger")
        return redirect(url_for("security_admin.index") + "#users")

    old_email = target.email
    target.email = new_email
    collaborator = Collaborator.query.filter_by(user_id=target.id).first()
    if collaborator and (collaborator.corporate_email or "").strip().lower() == old_email.lower():
        collaborator.corporate_email = new_email

    # Los enlaces enviados al antiguo correo dejan de ser válidos.
    PasswordReset.query.filter_by(user_id=target.id, used_at=None).update(
        {"used_at": datetime.utcnow()}, synchronize_session=False
    )
    audit(
        "editar_correo_usuario", "User", target.id,
        before={"email": old_email}, after={"email": new_email},
        reason=(request.form.get("reason") or "Corrección administrativa de correo").strip()[:255],
    )
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash("No se pudo guardar: verifica que el correo no esté registrado.", "danger")
        return redirect(url_for("security_admin.index") + "#users")
    flash("Correo de acceso actualizado. El usuario ya puede usar el correo correcto.", "success")
    return redirect(url_for("security_admin.index") + "#users")


@bp.route("/users/<int:user_id>/delete", methods=["POST"])
@login_required
@roles_required("superadmin", "admin")
def delete_user_safely(user_id):
    target = db.get_or_404(User, user_id)
    allowed, message = _can_manage_user_identity(target)
    if not allowed:
        flash(message, "danger")
        return redirect(url_for("security_admin.index") + "#users")

    # Triple confirmación, identidad y motivo, siempre verificadas en servidor.
    if not _administrative_password_valid():
        flash("Contraseña administrativa incorrecta; eliminación cancelada.", "danger")
        return redirect(url_for("security_admin.index") + "#users")
    if request.form.get("confirm_word", "").strip() != "ELIMINAR":
        flash('Escribe exactamente ELIMINAR para confirmar.', "danger")
        return redirect(url_for("security_admin.index") + "#users")
    if request.form.get("confirm_email", "").strip().lower() != target.email.lower():
        flash("El correo de confirmación no coincide con el del usuario.", "danger")
        return redirect(url_for("security_admin.index") + "#users")
    if request.form.get("understand_history") != "yes":
        flash("Confirma que comprendes que esta operación es irreversible.", "danger")
        return redirect(url_for("security_admin.index") + "#users")
    reason = (request.form.get("reason") or "").strip()
    if len(reason) < 10:
        flash("Escribe el motivo de eliminación (al menos 10 caracteres).", "danger")
        return redirect(url_for("security_admin.index") + "#users")

    # Cuentas utilizadas o con relaciones no se eliminan físicamente.
    # Se preservan ventas, asignaciones, auditoría y cualquier historial.
    if target.last_login_at is not None or _referenced_in_business_tables(target):
        flash(
            "No es seguro eliminar este usuario porque ya tiene accesos o registros asociados. "
            "Puedes desactivarlo; su historial quedará intacto.",
            "warning",
        )
        return redirect(url_for("security_admin.index") + "#users")

    actor_id = current_user.id
    old_info = {"id": target.id, "name": target.name, "email": target.email}
    collaborator = Collaborator.query.filter_by(user_id=target.id).first()

    # Los tokens de recuperación no son historial comercial.
    PasswordReset.query.filter_by(user_id=target.id).delete(synchronize_session=False)
    if collaborator:
        db.session.delete(collaborator)
        db.session.flush()
    db.session.delete(target)
    audit(
        "eliminar_usuario_sin_historial", "User", user_id,
        before=old_info, after={"deleted": True, "performed_by": actor_id},
        reason=reason[:255],
    )
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash(
            "El usuario tiene relaciones adicionales en la base de datos. "
            "No se eliminó ningún registro: utiliza Desactivar.",
            "danger",
        )
        return redirect(url_for("security_admin.index") + "#users")

    flash("Usuario sin historial eliminado. La acción quedó registrada en auditoría.", "success")
    return redirect(url_for("security_admin.index") + "#users")


@bp.route("/users/<int:user_id>/disable-2fa", methods=["POST"])
@login_required
@roles_required("superadmin", "admin", "manager")
def disable_user_2fa(user_id):
    user = db.get_or_404(User, user_id)
    if user.is_superadmin and not current_user.is_superadmin:
        abort(403)

    if two_factor_enabled(user):
        set_two_factor(user, False)
        audit(
            "desactivar_2fa_administrativo",
            "User",
            user.id,
            reason=(request.form.get("reason") or "").strip() or "Recuperación administrativa",
        )
        db.session.commit()
        flash("2FA desactivado para el usuario.", "success")
    else:
        flash("El usuario no tiene 2FA activo.", "info")
    return redirect(url_for("security_admin.index") + "#users")


@bp.route("/scope/<int:role_id>/<int:permission_id>", methods=["POST"])
@login_required
@roles_required("superadmin", "admin")
def scope_update(role_id, permission_id):
    role = db.get_or_404(Role, role_id)
    permission = db.get_or_404(Permission, permission_id)

    if role.name in BUILTIN_ROLES:
        flash(
            "Los roles base usan alcances protegidos por la lógica del sistema y no se editan aquí.",
            "info",
        )
        return redirect(url_for("security_admin.index") + "#scopes")

    if permission not in role.permissions:
        abort(400)

    scope = (request.form.get("scope") or "").strip()
    try:
        save_permission_scope(role.name, permission.code, scope)
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("security_admin.index") + "#scopes")

    audit(
        "editar_alcance_permiso",
        "Role",
        role.id,
        after={
            "permission": permission.code,
            "scope": scope,
        },
    )
    db.session.commit()
    flash("Alcance de datos actualizado.", "success")
    return redirect(url_for("security_admin.index") + "#scopes")


@bp.route("/temporary-grant", methods=["POST"])
@login_required
@roles_required("superadmin", "admin")
def temporary_grant():
    user_id = request.form.get("user_id", type=int)
    client_id = request.form.get("client_id", type=int)
    permission_code = (request.form.get("permission_code") or "").strip()
    expires_raw = (request.form.get("expires_at") or "").strip()

    user = db.session.get(User, user_id) if user_id else None
    client = db.session.get(Client, client_id) if client_id else None
    permission = Permission.query.filter_by(code=permission_code).first()

    if not user or not client or not permission:
        flash("Usuario, cliente o permiso no válido.", "danger")
        return redirect(url_for("security_admin.index") + "#temporary")

    if not user.role or permission not in user.role.permissions:
        flash("El permiso no está asignado al rol de ese usuario.", "danger")
        return redirect(url_for("security_admin.index") + "#temporary")

    if permission_scope(user, permission.code) != "temporary":
        flash(
            "El permiso seleccionado debe tener alcance Especial temporal.",
            "warning",
        )
        return redirect(url_for("security_admin.index") + "#temporary")

    try:
        expires_at = datetime.fromisoformat(expires_raw)
        save_temporary_client_grant(
            user.id,
            permission.code,
            client.id,
            expires_at,
        )
    except (TypeError, ValueError):
        flash("La fecha de expiración debe ser válida y futura.", "danger")
        return redirect(url_for("security_admin.index") + "#temporary")

    audit(
        "crear_acceso_temporal",
        "User",
        user.id,
        after={
            "permission": permission.code,
            "client_id": client.id,
            "expires_at": expires_at,
        },
    )
    db.session.commit()
    flash("Acceso temporal creado.", "success")
    return redirect(url_for("security_admin.index") + "#temporary")


@bp.route("/temporary-grant/revoke", methods=["POST"])
@login_required
@roles_required("superadmin", "admin")
def temporary_grant_revoke():
    user_id = request.form.get("user_id", type=int)
    client_id = request.form.get("client_id", type=int)
    permission_code = (request.form.get("permission_code") or "").strip()

    if not user_id or not client_id or not permission_code:
        abort(400)

    removed = revoke_temporary_client_grant(
        user_id,
        permission_code,
        client_id,
    )
    if removed:
        audit(
            "revocar_acceso_temporal",
            "User",
            user_id,
            before={
                "permission": permission_code,
                "client_id": client_id,
            },
        )
        db.session.commit()
        flash("Acceso temporal revocado.", "success")
    else:
        flash("El acceso temporal ya no existe.", "info")

    return redirect(url_for("security_admin.index") + "#temporary")


@bp.route("/audit")
@login_required
@roles_required("superadmin", "admin", "manager", "audit")
def audit_log():
    query, filters = _audit_query()
    rows = query.order_by(AuditLog.created_at.desc()).limit(1000).all()

    return render_template(
        "security_admin/audit.html",
        rows=rows,
        filters=filters,
        users=User.query.order_by(User.name).all(),
        actions=[
            row[0]
            for row in db.session.query(AuditLog.action)
            .distinct()
            .order_by(AuditLog.action)
            .all()
            if row[0]
        ],
        entities=[
            row[0]
            for row in db.session.query(AuditLog.entity)
            .distinct()
            .order_by(AuditLog.entity)
            .all()
            if row[0]
        ],
    )


@bp.route("/audit.csv")
@login_required
@roles_required("superadmin", "admin", "manager", "audit")
def audit_csv():
    query, filters = _audit_query()
    rows = query.order_by(AuditLog.created_at.desc()).limit(5000).all()

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "Fecha",
            "Usuario",
            "Acción",
            "Entidad",
            "ID",
            "IP",
            "Motivo",
            "Antes",
            "Después",
        ]
    )
    for row in rows:
        writer.writerow(
            [
                row.created_at,
                row.user.email if row.user else "Sistema",
                row.action,
                row.entity,
                row.entity_id,
                row.ip_address,
                row.reason,
                row.before_json,
                row.after_json,
            ]
        )

    audit(
        "exportar_auditoria",
        "AuditLog",
        after={
            key: (
                value.isoformat()
                if hasattr(value, "isoformat")
                else value
            )
            for key, value in filters.items()
        },
    )
    db.session.commit()

    return Response(
        "\ufeff" + buffer.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="auditoria_{date.today().isoformat()}.csv"'
            )
        },
    )
