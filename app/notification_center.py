import json
from collections import defaultdict
from datetime import date, datetime, time, timedelta

from flask_login import current_user

from app.extensions import db
from app.models import (
    AccountReceivable,
    AttendanceMark,
    Client,
    Collaborator,
    Interaction,
    LeaveRequest,
    Notification,
    PrintIncident,
    PrintItem,
    PrintOrder,
    Project,
    Role,
    Sale,
    Shipment,
    SupportTicket,
    SystemSetting,
    Task,
    User,
)


NOTIFICATION_CATEGORIES = {
    "commercial": "Seguimiento comercial",
    "payments": "Pagos y cobranza",
    "sales": "Nuevas ventas",
    "tasks": "Tareas",
    "hr": "RR. HH. y asistencia",
    "renewals": "Renovaciones",
    "support": "Soporte",
    "projects": "Proyectos",
    "printing": "Imprenta y logística",
    "general": "General",
}

PRIORITY_ORDER = {"urgente": 0, "alta": 1, "normal": 2, "baja": 3}
OPEN_TASK_STATES = {"pendiente", "en_proceso", "bloqueada", "en_revision"}
OPEN_TICKET_STATES = {"nuevo", "asignado", "en_proceso"}
ADMIN_ROLES = {"superadmin", "admin", "manager"}


def _setting(key):
    return SystemSetting.query.filter_by(key=key).first()


def _json_setting(key):
    row = _setting(key)
    if not row or not row.value:
        return {}
    try:
        value = json.loads(row.value)
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def _save_json_setting(key, value, description=None):
    row = _setting(key)
    if not row:
        row = SystemSetting(key=key)
        db.session.add(row)
    row.value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    if description:
        row.description = description
    return row


def default_preferences():
    return {key: True for key in NOTIFICATION_CATEGORIES}


def role_preferences(role_name):
    prefs = default_preferences()
    prefs.update(_json_setting(f"notification_role_pref:{role_name or 'none'}"))
    return {key: bool(prefs.get(key, True)) for key in NOTIFICATION_CATEGORIES}


def user_preferences(user):
    role_name = user.role.name if user and user.role else "none"
    prefs = role_preferences(role_name)
    prefs.update(_json_setting(f"notification_user_pref:{user.id}"))
    return {key: bool(prefs.get(key, True)) for key in NOTIFICATION_CATEGORIES}


def save_user_preferences(user, submitted_keys):
    values = {key: key in submitted_keys for key in NOTIFICATION_CATEGORIES}
    _save_json_setting(
        f"notification_user_pref:{user.id}",
        values,
        description=f"Preferencias de notificaciones del usuario {user.id}.",
    )
    return values


def save_role_preferences(role_name, submitted_keys):
    values = {key: key in submitted_keys for key in NOTIFICATION_CATEGORIES}
    _save_json_setting(
        f"notification_role_pref:{role_name}",
        values,
        description=f"Preferencias de notificaciones del rol {role_name}.",
    )
    return values


def notification_enabled(user, category):
    if not user or not user.active:
        return False
    return bool(user_preferences(user).get(category, True))


def notification_category(notification):
    link = (notification.link or "").lower()
    title = (notification.title or "").lower()

    if link.startswith("/renewals") or "renovaci" in title:
        return "renewals"
    if link.startswith("/crm") or "seguimiento" in title:
        return "commercial"
    if "cuota" in title or "pago" in title or "cobranza" in title or link.startswith("/finance"):
        return "payments"
    if link.startswith("/operations/tasks") or "tarea" in title:
        return "tasks"
    if link.startswith("/operations/projects") or "proyecto" in title:
        return "projects"
    if link.startswith("/hr") or "vacaci" in title or "asistencia" in title or "marcaci" in title:
        return "hr"
    if link.startswith("/support") or "ticket" in title:
        return "support"
    if link.startswith("/printing") or "imprenta" in title or "recepci" in title:
        return "printing"
    if link.startswith("/sales") or "venta" in title:
        return "sales"
    return "general"


def ensure_notification(
    user_id,
    title,
    message,
    *,
    link=None,
    priority="normal",
    category="general",
):
    user = db.session.get(User, user_id)
    if not user or not user.active or not notification_enabled(user, category):
        return None

    existing = Notification.query.filter_by(
        user_id=user_id,
        title=title,
        message=message,
        link=link,
    ).first()
    if existing:
        return existing

    row = Notification(
        user_id=user_id,
        title=title[:180],
        message=message,
        link=link,
        priority=priority if priority in PRIORITY_ORDER else "normal",
        read=False,
    )
    db.session.add(row)
    return row


def _active_admin_users():
    return (
        User.query
        .join(Role, User.role_id == Role.id)
        .filter(
            User.active.is_(True),
            Role.name.in_(ADMIN_ROLES),
        )
        .all()
    )


def _users_with_permission(code):
    users = User.query.filter_by(active=True).all()
    return [user for user in users if user.has_permission(code)]


def _unique_users(*groups):
    result = {}
    for group in groups:
        for user in group or []:
            if user and user.active:
                result[user.id] = user
    return list(result.values())


def _collaborator_users(collaborators):
    return [
        row.user
        for row in collaborators
        if row and row.user and row.user.active
    ]


def _notify_users(users, title, message, *, link, priority, category):
    for user in _unique_users(users):
        ensure_notification(
            user.id,
            title,
            message,
            link=link,
            priority=priority,
            category=category,
        )


def _latest_followups():
    rows = (
        Interaction.query
        .filter(Interaction.next_followup_at.isnot(None))
        .order_by(Interaction.client_id.asc(), Interaction.occurred_at.desc(), Interaction.id.desc())
        .all()
    )
    latest = {}
    for row in rows:
        latest.setdefault(row.client_id, row)
    return list(latest.values())


def _commercial_alerts(now):
    for row in _latest_followups():
        if row.next_followup_at >= now:
            continue
        client = row.client
        owner = client.owner if client else None
        if not owner or not owner.user or not owner.user.active:
            continue

        due_label = row.next_followup_at.strftime("%d/%m/%Y %H:%M")
        title = f"Seguimiento vencido: {client.business_name}"
        message = f"El seguimiento programado para {due_label} está vencido."
        recipients = [owner.user]
        if owner.supervisor and owner.supervisor.user:
            recipients.append(owner.supervisor.user)

        _notify_users(
            recipients,
            title,
            message,
            link=f"/crm/{client.id}",
            priority="alta",
            category="commercial",
        )


def _payment_alerts(today):
    for row in AccountReceivable.query.filter(
        AccountReceivable.status.notin_(["pagado", "cancelado"])
    ).all():
        if not row.due_date:
            continue
        delta = (row.due_date - today).days
        if delta > 7:
            continue

        client = row.client
        sale = row.sale
        if delta < 0:
            title = f"Pago vencido: {client.business_name}"
            message = (
                f"{sale.sale_no if sale else 'Venta'} tiene saldo pendiente y venció "
                f"el {row.due_date}."
            )
            priority = "urgente"
        else:
            title = f"Pago próximo: {client.business_name}"
            message = (
                f"{sale.sale_no if sale else 'Venta'} tiene saldo pendiente y vence "
                f"en {delta} día(s), el {row.due_date}."
            )
            priority = "alta" if delta <= 2 else "normal"

        recipients = list(_active_admin_users())
        recipients.extend(_users_with_permission("finance.view"))
        if client and client.owner and client.owner.user:
            recipients.append(client.owner.user)

        _notify_users(
            recipients,
            title,
            message,
            link=f"/sales/{row.sale_id}" if row.sale_id else "/sales/",
            priority=priority,
            category="payments",
        )


def _new_sale_alerts(now):
    cutoff = now - timedelta(days=7)
    sales = Sale.query.filter(Sale.created_at >= cutoff).all()
    admins = _active_admin_users()

    for sale in sales:
        title = f"Nueva venta: {sale.sale_no}"
        message = f"{sale.client.business_name} · USD {sale.total or 0}."
        recipients = list(admins)

        for project in Project.query.filter_by(sale_id=sale.id).all():
            if project.coordinator and project.coordinator.user:
                recipients.append(project.coordinator.user)
            recipients.extend(_collaborator_users(project.members))

        _notify_users(
            recipients,
            title,
            message,
            link=f"/sales/{sale.id}",
            priority="normal",
            category="sales",
        )


def _task_alerts(now):
    recent_cutoff = now - timedelta(days=7)
    tasks = Task.query.filter(Task.status.in_(OPEN_TASK_STATES)).all()

    for task in tasks:
        if not task.assignee or not task.assignee.user:
            continue

        recipients = [task.assignee.user]
        if task.assignee.supervisor and task.assignee.supervisor.user:
            recipients.append(task.assignee.supervisor.user)

        if task.created_at >= recent_cutoff or task.updated_at >= recent_cutoff:
            due_text = (
                f" Vence {task.due_at.strftime('%d/%m/%Y %H:%M')}."
                if task.due_at else ""
            )
            _notify_users(
                recipients,
                f"Tarea asignada: {task.title}",
                f"Tienes una tarea activa.{due_text}",
                link=f"/operations/tasks/{task.id}",
                priority="alta" if task.priority in {"alta", "urgente"} else "normal",
                category="tasks",
            )

        if task.due_at and task.due_at < now:
            _notify_users(
                recipients,
                f"Tarea vencida: {task.title}",
                f"La tarea venció el {task.due_at.strftime('%d/%m/%Y %H:%M')} y sigue {task.status}.",
                link=f"/operations/tasks/{task.id}",
                priority="urgente",
                category="tasks",
            )


def _leave_alerts():
    approvers = _users_with_permission("leave.approve")

    for row in LeaveRequest.query.all():
        collaborator = row.collaborator
        employee_user = collaborator.user if collaborator and collaborator.user else None

        if row.status == "pendiente":
            recipients = []
            if row.approver and row.approver.active:
                recipients.append(row.approver)
            else:
                if (
                    collaborator
                    and collaborator.supervisor
                    and collaborator.supervisor.user
                    and collaborator.supervisor.user.has_permission("leave.approve")
                ):
                    recipients.append(collaborator.supervisor.user)
                recipients.extend(
                    user
                    for user in approvers
                    if user.role and user.role.name in {"superadmin", "admin", "manager", "hr"}
                )

            _notify_users(
                recipients,
                f"Solicitud de {row.leave_type}: {collaborator.user.name if employee_user else 'Colaborador'}",
                f"Solicitud del {row.start_date} al {row.end_date} pendiente de decisión.",
                link="/hr/leave/admin",
                priority="normal",
                category="hr",
            )
        elif row.status in {"aprobada", "rechazada"} and employee_user:
            _notify_users(
                [employee_user],
                f"Solicitud {row.status}: {row.leave_type}",
                f"Tu solicitud del {row.start_date} al {row.end_date} fue {row.status}.",
                link="/hr/leave",
                priority="normal" if row.status == "aprobada" else "alta",
                category="hr",
            )


def _attendance_alerts(today):
    target = today - timedelta(days=1)
    start = datetime.combine(target, time.min)
    end = datetime.combine(target, time.max)

    rows = AttendanceMark.query.filter(
        AttendanceMark.marked_at >= start,
        AttendanceMark.marked_at <= end,
    ).order_by(AttendanceMark.marked_at.asc()).all()

    by_collaborator = defaultdict(list)
    for row in rows:
        by_collaborator[row.collaborator_id].append(row)

    for collaborator_id, marks in by_collaborator.items():
        types = [row.mark_type for row in marks]
        if "entrada" not in types or "salida" in types:
            continue

        collaborator = db.session.get(Collaborator, collaborator_id)
        if not collaborator or not collaborator.user:
            continue
        recipients = [collaborator.user]
        if collaborator.supervisor and collaborator.supervisor.user:
            recipients.append(collaborator.supervisor.user)

        _notify_users(
            recipients,
            f"Marcación incompleta: {collaborator.user.name}",
            f"El {target} se registró entrada pero no salida.",
            link="/hr/my-day",
            priority="alta",
            category="hr",
        )


def _support_alerts():
    rows = SupportTicket.query.filter(
        SupportTicket.status.in_(OPEN_TICKET_STATES),
        SupportTicket.priority.in_(["alta", "urgente"]),
    ).all()

    for ticket in rows:
        recipients = []
        if ticket.responsible and ticket.responsible.user:
            recipients.append(ticket.responsible.user)
            if ticket.responsible.supervisor and ticket.responsible.supervisor.user:
                recipients.append(ticket.responsible.supervisor.user)
        elif ticket.client and ticket.client.owner and ticket.client.owner.user:
            recipients.append(ticket.client.owner.user)

        _notify_users(
            recipients,
            f"Ticket urgente: {ticket.ticket_no}",
            f"{ticket.subject} · prioridad {ticket.priority}.",
            link=f"/support/{ticket.id}",
            priority="urgente" if ticket.priority == "urgente" else "alta",
            category="support",
        )


def _project_alerts():
    rows = Project.query.filter(
        Project.status == "esperando_cliente"
    ).all()

    for project in rows:
        recipients = []
        if project.coordinator and project.coordinator.user:
            recipients.append(project.coordinator.user)

        _notify_users(
            recipients,
            f"Proyecto bloqueado: {project.project_no}",
            f"{project.name} está esperando información o respuesta del cliente.",
            link=f"/operations/projects/{project.id}",
            priority="alta",
            category="projects",
        )


def _printing_recipients(order):
    recipients = []
    if order.responsible and order.responsible.user:
        recipients.append(order.responsible.user)
    if order.client and order.client.owner and order.client.owner.user:
        recipients.append(order.client.owner.user)

    for user in _users_with_permission("printing.approve"):
        role = user.role.name if user.role else ""
        collaborator = user.collaborator
        department = (collaborator.department or "").strip().lower() if collaborator else ""
        if role in ADMIN_ROLES or department in {
            "imprenta",
            "logística",
            "logistica",
            "diseño",
            "diseno",
        }:
            recipients.append(user)

    return _unique_users(recipients)


def _printing_alerts(today):
    items = PrintItem.query.all()
    for item in items:
        order = item.order
        if item.design_status == "revision" and not item.design_approved_at:
            _notify_users(
                _printing_recipients(order),
                f"Imprenta pendiente de aprobación: {order.order_no}",
                f"El diseño de {item.name} está en revisión y requiere aprobación.",
                link=f"/printing/{order.id}",
                priority="alta",
                category="printing",
            )
        elif item.design_approved_at and not item.shipping_approved_at and order.status not in {"recibido", "entregado"}:
            _notify_users(
                _printing_recipients(order),
                f"Envío pendiente de aprobación: {order.order_no}",
                f"{item.name} tiene diseño aprobado y el envío todavía no está aprobado.",
                link=f"/printing/{order.id}",
                priority="alta",
                category="printing",
            )

    shipments = Shipment.query.filter(
        Shipment.estimated_delivery.isnot(None),
        Shipment.estimated_delivery < today,
        Shipment.status.notin_(["recibido"]),
    ).all()
    for shipment in shipments:
        order = shipment.order
        _notify_users(
            _printing_recipients(order) + _active_admin_users(),
            f"Recepción vencida: {order.order_no}",
            f"El envío debía recibirse el {shipment.estimated_delivery} y continúa {shipment.status}.",
            link=f"/printing/{order.id}",
            priority="urgente",
            category="printing",
        )

    incidents = PrintIncident.query.filter_by(status="abierta").all()
    for incident in incidents:
        order = incident.order
        _notify_users(
            _printing_recipients(order) + _active_admin_users(),
            f"Incidencia de imprenta: {order.order_no}",
            f"{incident.incident_type}: {incident.description}",
            link=f"/printing/{order.id}",
            priority="urgente",
            category="printing",
        )


def ensure_system_alerts():
    # Import local para evitar ciclo: renewal_services usa ensure_notification().
    from app.renewal_services import ensure_renewal_alerts

    ensure_renewal_alerts()

    now = datetime.utcnow()
    today = date.today()

    _commercial_alerts(now)
    _payment_alerts(today)
    _new_sale_alerts(now)
    _task_alerts(now)
    _leave_alerts()
    _attendance_alerts(today)
    _support_alerts()
    _project_alerts()
    _printing_alerts(today)


def filtered_notifications(
    user,
    *,
    category=None,
    priority=None,
    read_state=None,
    start_date=None,
    end_date=None,
):
    query = Notification.query.filter_by(user_id=user.id)

    if priority in PRIORITY_ORDER:
        query = query.filter(Notification.priority == priority)
    if read_state == "unread":
        query = query.filter(Notification.read.is_(False))
    elif read_state == "read":
        query = query.filter(Notification.read.is_(True))

    if start_date:
        query = query.filter(Notification.created_at >= datetime.combine(start_date, time.min))
    if end_date:
        query = query.filter(Notification.created_at <= datetime.combine(end_date, time.max))

    rows = query.order_by(Notification.created_at.desc()).all()
    if category and category in NOTIFICATION_CATEGORIES:
        rows = [row for row in rows if notification_category(row) == category]

    return sorted(
        rows,
        key=lambda row: (
            PRIORITY_ORDER.get(row.priority or "normal", 9),
            -(row.created_at.timestamp() if row.created_at else 0),
        ),
    )


def unread_count_for_user(user):
    preferences = user_preferences(user)
    rows = Notification.query.filter_by(user_id=user.id, read=False).all()
    return sum(
        1
        for row in rows
        if preferences.get(notification_category(row), True)
    )
