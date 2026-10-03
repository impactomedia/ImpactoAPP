import csv
import io
from datetime import date, datetime

from flask import Blueprint, Response, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import or_

from app.decorators import roles_required
from app.extensions import db
from app.helpers import audit
from app.models import AuditLog, Client, Permission, Role, User
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
