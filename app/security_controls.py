import hashlib
import secrets
from datetime import datetime, timedelta

from flask import abort, flash, redirect, request, session, url_for
from flask_login import current_user, logout_user

from app.extensions import db
from app.helpers import audit
from app.models import (
    Client,
    Payment,
    PrintIncident,
    PrintItem,
    PrintOrder,
    Project,
    Quote,
    Renewal,
    Sale,
    Shipment,
    SupportTicket,
    SystemSetting,
    Task,
    User,
)


BUILTIN_ROLES = {
    "superadmin",
    "admin",
    "manager",
    "hr",
    "supervisor",
    "advisor",
    "development_coordinator",
    "production",
    "finance",
    "audit",
}

DATA_SCOPES = {
    "company": "Toda la empresa",
    "team": "Mi grupo",
    "own": "Propios",
    "assigned": "Propios o asignados",
    "hr": "Solo RR. HH.",
    "temporary": "Especial temporal",
}

POLICY_DEFAULTS = {
    "security_failed_attempt_limit": 5,
    "security_lock_minutes": 15,
    "security_session_timeout_minutes": 60,
    "security_allow_remember_me": 0,
    "security_allow_2fa": 1,
}


def _setting(key):
    return SystemSetting.query.filter_by(key=key).first()


def setting_text(key, default=None):
    row = _setting(key)
    if not row or row.value in (None, ""):
        return default
    return str(row.value).strip()


def setting_int(key, default, minimum=None, maximum=None):
    raw = setting_text(key)
    try:
        value = int(raw) if raw is not None else int(default)
    except (TypeError, ValueError):
        value = int(default)
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def setting_bool(key, default=False):
    raw = setting_text(key)
    if raw is None:
        return bool(default)
    return raw.lower() in {"1", "true", "yes", "si", "sí", "on"}


def security_policy():
    return {
        "failed_attempt_limit": setting_int(
            "security_failed_attempt_limit",
            POLICY_DEFAULTS["security_failed_attempt_limit"],
            minimum=3,
            maximum=20,
        ),
        "lock_minutes": setting_int(
            "security_lock_minutes",
            POLICY_DEFAULTS["security_lock_minutes"],
            minimum=1,
            maximum=1440,
        ),
        "session_timeout_minutes": setting_int(
            "security_session_timeout_minutes",
            POLICY_DEFAULTS["security_session_timeout_minutes"],
            minimum=5,
            maximum=1440,
        ),
        "allow_remember_me": setting_bool(
            "security_allow_remember_me",
            bool(POLICY_DEFAULTS["security_allow_remember_me"]),
        ),
        "allow_2fa": setting_bool(
            "security_allow_2fa",
            bool(POLICY_DEFAULTS["security_allow_2fa"]),
        ),
    }


def save_security_policy(values):
    definitions = {
        "security_failed_attempt_limit": (
            str(max(3, min(20, int(values.get("failed_attempt_limit", 5))))),
            "Intentos fallidos antes del bloqueo temporal.",
        ),
        "security_lock_minutes": (
            str(max(1, min(1440, int(values.get("lock_minutes", 15))))),
            "Duración del bloqueo temporal en minutos.",
        ),
        "security_session_timeout_minutes": (
            str(max(5, min(1440, int(values.get("session_timeout_minutes", 60))))),
            "Minutos de inactividad antes de cerrar la sesión.",
        ),
        "security_allow_remember_me": (
            "1" if values.get("allow_remember_me") else "0",
            "Permitir sesiones persistentes con Recordarme.",
        ),
        "security_allow_2fa": (
            "1" if values.get("allow_2fa") else "0",
            "Permitir autenticación de dos factores por correo.",
        ),
    }
    for key, (value, description) in definitions.items():
        row = _setting(key)
        if not row:
            row = SystemSetting(key=key)
            db.session.add(row)
        row.value = value
        row.description = description


def two_factor_enabled(user):
    if not user or not security_policy()["allow_2fa"]:
        return False
    return setting_bool(f"security_2fa_user:{user.id}", False)


def set_two_factor(user, enabled):
    key = f"security_2fa_user:{user.id}"
    row = _setting(key)
    if not row:
        row = SystemSetting(key=key)
        db.session.add(row)
    row.value = "1" if enabled else "0"
    row.description = f"2FA por correo para usuario {user.id}."


def _scope_key(role_name, permission_code):
    return f"permission_scope:{role_name}:{permission_code}"


def default_permission_scope(role_name, permission_code):
    if role_name in {"superadmin", "admin", "manager", "audit", "finance"}:
        return "company"
    if role_name == "supervisor":
        return "team"
    if role_name == "advisor":
        return "own"
    if role_name in {"production", "development_coordinator"}:
        return "assigned"
    if role_name == "hr":
        return "hr"
    return "own"


def permission_scope(user, permission_code):
    role_name = user.role.name if user and user.role else ""
    if not role_name:
        return "own"

    # Los roles base ya tienen filtros específicos de negocio probados por CI.
    if role_name in BUILTIN_ROLES:
        return default_permission_scope(role_name, permission_code)

    raw = setting_text(_scope_key(role_name, permission_code))
    return raw if raw in DATA_SCOPES else "own"


def save_permission_scope(role_name, permission_code, scope):
    if scope not in DATA_SCOPES:
        raise ValueError("Alcance de datos no válido.")
    row = _setting(_scope_key(role_name, permission_code))
    if not row:
        row = SystemSetting(key=_scope_key(role_name, permission_code))
        db.session.add(row)
    row.value = scope
    row.description = f"Alcance de {permission_code} para rol {role_name}."


def _temporary_prefix(user_id, permission_code):
    return f"temporary_scope:{user_id}:{permission_code}:"


def temporary_client_ids(user, permission_code):
    if not user:
        return set()

    prefix = _temporary_prefix(user.id, permission_code)
    now = datetime.utcnow()
    allowed = set()

    rows = SystemSetting.query.filter(
        SystemSetting.key.like(f"{prefix}%")
    ).all()
    for row in rows:
        try:
            client_id = int(row.key[len(prefix):])
            expires_at = datetime.fromisoformat(str(row.value))
        except (TypeError, ValueError):
            continue
        if expires_at > now:
            allowed.add(client_id)

    return allowed


def save_temporary_client_grant(user_id, permission_code, client_id, expires_at):
    if expires_at <= datetime.utcnow():
        raise ValueError("La expiración debe estar en el futuro.")

    key = f"{_temporary_prefix(user_id, permission_code)}{client_id}"
    row = _setting(key)
    if not row:
        row = SystemSetting(key=key)
        db.session.add(row)
    row.value = expires_at.isoformat()
    row.description = (
        f"Acceso temporal usuario {user_id}, permiso {permission_code}, cliente {client_id}."
    )
    return row


def revoke_temporary_client_grant(user_id, permission_code, client_id):
    key = f"{_temporary_prefix(user_id, permission_code)}{client_id}"
    row = _setting(key)
    if row:
        db.session.delete(row)
        return True
    return False


def list_temporary_grants():
    rows = SystemSetting.query.filter(
        SystemSetting.key.like("temporary_scope:%")
    ).order_by(SystemSetting.key).all()
    result = []
    now = datetime.utcnow()

    for row in rows:
        parts = row.key.split(":")
        if len(parts) != 4:
            continue
        try:
            user_id = int(parts[1])
            permission_code = parts[2]
            client_id = int(parts[3])
            expires_at = datetime.fromisoformat(str(row.value))
        except (TypeError, ValueError):
            continue

        result.append(
            {
                "user_id": user_id,
                "permission_code": permission_code,
                "client_id": client_id,
                "expires_at": expires_at,
                "active": expires_at > now,
            }
        )
    return result


def _allowed_collaborator_ids(scope):
    collaborator = current_user.collaborator
    if not collaborator:
        return set()
    if scope == "team":
        return {collaborator.id, *(row.id for row in collaborator.subordinates)}
    return {collaborator.id}


def _custom_role_record_scope_guard():
    if not current_user.is_authenticated or not current_user.role:
        return None

    role_name = current_user.role.name
    if role_name in BUILTIN_ROLES:
        return None

    if request.endpoint == "dashboard.search":
        for permission in current_user.role.permissions:
            if permission_scope(current_user, permission.code) != "company":
                abort(403)
        return None

    # Se reutiliza la resolución central de permisos sin duplicarla.
    from app.access_control import _required_permission

    permission = _required_permission(request.endpoint, request.method)
    if not permission:
        return None

    scope = permission_scope(current_user, permission)
    if scope == "company":
        return None
    if scope == "hr" and request.blueprint != "hr":
        abort(403)

    temporary_ids = (
        temporary_client_ids(current_user, permission)
        if scope == "temporary"
        else set()
    )
    collaborator_ids = _allowed_collaborator_ids(scope)
    args = request.view_args or {}

    client = None
    project = None
    task = None
    order = None
    ticket = None

    if "client_id" in args:
        client = db.session.get(Client, args["client_id"])
    elif "quote_id" in args:
        row = db.session.get(Quote, args["quote_id"])
        client = row.client if row else None
    elif "renewal_id" in args:
        row = db.session.get(Renewal, args["renewal_id"])
        client = row.client if row else None
    elif "sale_id" in args:
        row = db.session.get(Sale, args["sale_id"])
        client = row.client if row else None
    elif "payment_id" in args:
        row = db.session.get(Payment, args["payment_id"])
        client = row.client if row else None
    elif "project_id" in args:
        project = db.session.get(Project, args["project_id"])
    elif "task_id" in args:
        task = db.session.get(Task, args["task_id"])
    elif "order_id" in args:
        order = db.session.get(PrintOrder, args["order_id"])
    elif "ticket_id" in args:
        ticket = db.session.get(SupportTicket, args["ticket_id"])
    elif "item_id" in args:
        item = db.session.get(PrintItem, args["item_id"])
        order = item.order if item else None
    elif "shipment_id" in args:
        shipment = db.session.get(Shipment, args["shipment_id"])
        order = shipment.order if shipment else None
    elif "incident_id" in args:
        incident = db.session.get(PrintIncident, args["incident_id"])
        order = incident.order if incident else None

    if client is not None:
        allowed = (
            client.id in temporary_ids
            if scope == "temporary"
            else bool(collaborator_ids and client.owner_id in collaborator_ids)
        )
        if not allowed:
            abort(403)
        return None

    if project is not None:
        allowed = (
            project.client_id in temporary_ids
            if scope == "temporary"
            else (
                project.coordinator_id in collaborator_ids
                or any(member.id in collaborator_ids for member in project.members)
                or (project.client and project.client.owner_id in collaborator_ids)
            )
        )
        if not allowed:
            abort(403)
        return None

    if task is not None:
        allowed = (
            task.client_id in temporary_ids
            if scope == "temporary"
            else (
                task.assignee_id in collaborator_ids
                or any(member.id in collaborator_ids for member in task.collaborators)
                or (
                    task.project
                    and (
                        task.project.coordinator_id in collaborator_ids
                        or any(member.id in collaborator_ids for member in task.project.members)
                    )
                )
                or (task.client and task.client.owner_id in collaborator_ids)
            )
        )
        if not allowed:
            abort(403)
        return None

    if order is not None:
        allowed = (
            order.client_id in temporary_ids
            if scope == "temporary"
            else (
                order.responsible_id in collaborator_ids
                or (order.client and order.client.owner_id in collaborator_ids)
            )
        )
        if not allowed:
            abort(403)
        return None

    if ticket is not None:
        allowed = (
            ticket.client_id in temporary_ids
            if scope == "temporary"
            else (
                ticket.responsible_id in collaborator_ids
                or (ticket.client and ticket.client.owner_id in collaborator_ids)
            )
        )
        if not allowed:
            abort(403)
        return None

    # Seguridad conservadora: mientras un listado no tenga filtro dinámico para un
    # rol personalizado, jamás se abre como listado global si su scope no es company.
    sensitive_list_endpoints = {
        "crm.prospects",
        "crm.pipeline",
        "clients.index",
        "sales.index",
        "sales.renewals",
        "operations.projects",
        "operations.tasks",
        "operations.task_kanban",
        "printing.index",
        "support.index",
        "reports.index",
        "reports.export_csv",
        "reports.printable",
    }
    if request.endpoint in sensitive_list_endpoints and scope != "company":
        abort(403)

    return None


def _require_sensitive_reason():
    if request.method != "POST":
        return None
    if request.endpoint == "sales.reverse_payment":
        if not (request.form.get("reason") or "").strip():
            abort(400, description="El motivo es obligatorio para reversar un pago confirmado.")
    return None


def begin_two_factor_challenge(user, remember=False, destination=None):
    code = f"{100000 + secrets.randbelow(900000):06d}"
    from flask import current_app

    digest = hashlib.sha256(
        f"{current_app.config['SECRET_KEY']}:{user.id}:{code}".encode()
    ).hexdigest()

    session["pending_2fa_user_id"] = user.id
    session["pending_2fa_hash"] = digest
    session["pending_2fa_expires"] = (datetime.utcnow() + timedelta(minutes=10)).isoformat()
    session["pending_2fa_attempts"] = 0
    session["pending_2fa_remember"] = bool(remember)
    session["pending_2fa_destination"] = destination or ""
    return code


def verify_two_factor_code(code):
    from flask import current_app

    user_id = session.get("pending_2fa_user_id")
    expected = session.get("pending_2fa_hash")
    expires_raw = session.get("pending_2fa_expires")
    attempts = int(session.get("pending_2fa_attempts", 0))

    if not user_id or not expected or not expires_raw:
        return None, "No hay una verificación pendiente."

    try:
        expires = datetime.fromisoformat(expires_raw)
    except ValueError:
        return None, "La verificación no es válida."

    if expires < datetime.utcnow():
        clear_two_factor_challenge()
        return None, "El código expiró. Inicia sesión nuevamente."

    if attempts >= 5:
        clear_two_factor_challenge()
        return None, "Se superó el número de intentos. Inicia sesión nuevamente."

    digest = hashlib.sha256(
        f"{current_app.config['SECRET_KEY']}:{user_id}:{(code or '').strip()}".encode()
    ).hexdigest()

    if not secrets.compare_digest(digest, expected):
        session["pending_2fa_attempts"] = attempts + 1
        return None, "Código incorrecto."

    user = db.session.get(User, int(user_id))
    if not user or not user.active:
        clear_two_factor_challenge()
        return None, "La cuenta ya no está activa."

    return user, None


def pending_two_factor_options():
    return {
        "remember": bool(session.get("pending_2fa_remember")),
        "destination": session.get("pending_2fa_destination") or "",
    }


def clear_two_factor_challenge():
    for key in [
        "pending_2fa_user_id",
        "pending_2fa_hash",
        "pending_2fa_expires",
        "pending_2fa_attempts",
        "pending_2fa_remember",
        "pending_2fa_destination",
    ]:
        session.pop(key, None)


def _session_security_guard():
    if not current_user.is_authenticated:
        return None

    if not current_user.active:
        user_id = current_user.id
        audit("sesion_revocada_usuario_inactivo", "User", user_id)
        db.session.commit()
        logout_user()
        session.clear()
        flash("Tu usuario fue desactivado. Contacta a un administrador.", "danger")
        return redirect(url_for("auth.login"))

    policy = security_policy()
    timeout_seconds = policy["session_timeout_minutes"] * 60
    now_ts = datetime.utcnow().timestamp()
    last_raw = session.get("security_last_activity")

    try:
        last_ts = float(last_raw) if last_raw is not None else None
    except (TypeError, ValueError):
        last_ts = None

    if last_ts is not None and now_ts - last_ts > timeout_seconds:
        user_id = current_user.id
        audit(
            "session_timeout",
            "User",
            user_id,
            reason=f"Inactividad superior a {policy['session_timeout_minutes']} minutos",
        )
        db.session.commit()
        logout_user()
        session.clear()
        flash("Tu sesión expiró por inactividad.", "warning")
        return redirect(url_for("auth.login"))

    session["security_last_activity"] = now_ts
    session.permanent = False
    return None


def init_security_controls(app):
    @app.before_request
    def enforce_session_security():
        return _session_security_guard()

    @app.before_request
    def enforce_sensitive_reasons():
        return _require_sensitive_reason()

    @app.before_request
    def enforce_custom_role_data_scope():
        return _custom_role_record_scope_guard()
