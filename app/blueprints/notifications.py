from datetime import date

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.helpers import audit
from app.models import Notification, Role
from app.notification_center import (
    NOTIFICATION_CATEGORIES,
    PRIORITY_ORDER,
    ensure_system_alerts,
    filtered_notifications,
    role_preferences,
    save_role_preferences,
    save_user_preferences,
    user_preferences,
)


bp = Blueprint("notifications", __name__, url_prefix="/notifications")


def _parse_date(value):
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _own_notification(notification_id):
    row = db.get_or_404(Notification, notification_id)
    if row.user_id != current_user.id:
        abort(403)
    return row


@bp.route("/")
@login_required
def index():
    ensure_system_alerts()
    db.session.commit()

    category = (request.args.get("category") or "").strip()
    priority = (request.args.get("priority") or "").strip()
    read_state = (request.args.get("state") or "").strip()
    start_date = _parse_date(request.args.get("starts"))
    end_date = _parse_date(request.args.get("ends"))

    rows = filtered_notifications(
        current_user,
        category=category or None,
        priority=priority or None,
        read_state=read_state or None,
        start_date=start_date,
        end_date=end_date,
    )

    unread = sum(1 for row in rows if not row.read)
    urgent = sum(1 for row in rows if row.priority == "urgente" and not row.read)

    role_name = current_user.role.name if current_user.role else ""
    editable_roles = (
        Role.query.filter_by(active=True).order_by(Role.label).all()
        if role_name in {"superadmin", "admin", "manager"}
        else []
    )

    return render_template(
        "notifications/index.html",
        rows=rows,
        categories=NOTIFICATION_CATEGORIES,
        priorities=PRIORITY_ORDER,
        current_category=category,
        current_priority=priority,
        current_state=read_state,
        start_date=start_date,
        end_date=end_date,
        unread=unread,
        urgent=urgent,
        preferences=user_preferences(current_user),
        editable_roles=editable_roles,
    )


@bp.route("/<int:notification_id>/open", methods=["POST"])
@login_required
def open_notification(notification_id):
    row = _own_notification(notification_id)
    row.read = True
    db.session.commit()
    return redirect(row.link or url_for("notifications.index"))


@bp.route("/<int:notification_id>/toggle", methods=["POST"])
@login_required
def toggle(notification_id):
    row = _own_notification(notification_id)
    row.read = not row.read
    audit(
        "cambiar_estado_notificacion",
        "Notification",
        row.id,
        after={"read": row.read},
    )
    db.session.commit()
    return redirect(request.referrer or url_for("notifications.index"))


@bp.route("/bulk", methods=["POST"])
@login_required
def bulk():
    action = request.form.get("action")
    if action not in {"read", "unread"}:
        abort(400)

    rows = Notification.query.filter_by(user_id=current_user.id).all()
    target = action == "read"
    for row in rows:
        row.read = target

    audit(
        "estado_masivo_notificaciones",
        "Notification",
        after={"action": action, "count": len(rows)},
    )
    db.session.commit()
    flash(
        "Todas tus notificaciones se marcaron como leídas."
        if target
        else "Todas tus notificaciones se marcaron como no leídas.",
        "success",
    )
    return redirect(url_for("notifications.index"))


@bp.route("/preferences", methods=["POST"])
@login_required
def preferences():
    submitted = set(request.form.getlist("category"))
    save_user_preferences(current_user, submitted)
    audit(
        "preferencias_notificacion_usuario",
        "User",
        current_user.id,
        after={"categories": sorted(submitted)},
    )
    db.session.commit()
    flash("Tus preferencias de notificaciones fueron actualizadas.", "success")
    return redirect(url_for("notifications.index", _anchor="preferences"))


@bp.route("/role-preferences", methods=["POST"])
@login_required
def role_preferences_update():
    role_name = current_user.role.name if current_user.role else ""
    if role_name not in {"superadmin", "admin", "manager"}:
        abort(403)

    target_role = (request.form.get("role_name") or "").strip()
    role = Role.query.filter_by(name=target_role, active=True).first()
    if not role:
        abort(404)

    submitted = set(request.form.getlist("category"))
    save_role_preferences(target_role, submitted)
    audit(
        "preferencias_notificacion_rol",
        "Role",
        role.id,
        after={"role": target_role, "categories": sorted(submitted)},
    )
    db.session.commit()
    flash(f"Preferencias por defecto de {role.label} actualizadas.", "success")
    return redirect(url_for("notifications.index", role=target_role, _anchor="role-preferences"))


@bp.route("/role-preferences/<role_name>")
@login_required
def role_preferences_data(role_name):
    own_role = current_user.role.name if current_user.role else ""
    if own_role not in {"superadmin", "admin", "manager"}:
        abort(403)

    role = Role.query.filter_by(name=role_name, active=True).first_or_404()
    return {
        "role": role.name,
        "label": role.label,
        "preferences": role_preferences(role.name),
    }
