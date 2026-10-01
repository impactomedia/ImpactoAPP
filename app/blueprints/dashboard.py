from flask import Blueprint, render_template, redirect, request, url_for
from flask_login import login_required, current_user
from sqlalchemy import func, or_

from app.extensions import db
from app.models import Client, Collaborator, Payment, Task, Project, Notification, Quote, Sale, SupportTicket
from app.services import refresh_overdue_receivables, refresh_expired_contracts, ensure_renewal_notifications

bp = Blueprint("dashboard", __name__, url_prefix="/dashboard")


def _client_scope(query):
    role = current_user.role.name if current_user.role else ""
    collaborator = current_user.collaborator

    if role == "advisor" and collaborator:
        return query.filter(Client.owner_id == collaborator.id)

    if role == "supervisor" and collaborator:
        allowed_ids = [collaborator.id] + [c.id for c in collaborator.subordinates]
        return query.filter(Client.owner_id.in_(allowed_ids))

    return query


def _task_scope(query):
    role = current_user.role.name if current_user.role else ""
    collaborator = current_user.collaborator

    if role == "supervisor" and collaborator:
        allowed_ids = [collaborator.id] + [c.id for c in collaborator.subordinates]
        return query.filter(
            (Task.assignee_id.in_(allowed_ids))
            | Task.collaborators.any(Collaborator.id.in_(allowed_ids))
        )

    if role not in {"superadmin", "admin", "manager"} and collaborator:
        return query.filter(
            (Task.assignee_id == collaborator.id)
            | Task.collaborators.any(id=collaborator.id)
        )
    return query


def _project_scope(query):
    role = current_user.role.name if current_user.role else ""
    collaborator = current_user.collaborator

    if role == "advisor" and collaborator:
        return query.filter(Project.client.has(owner_id=collaborator.id))

    if role == "supervisor" and collaborator:
        allowed_ids = [collaborator.id] + [c.id for c in collaborator.subordinates]
        return query.filter(Project.client.has(Client.owner_id.in_(allowed_ids)))

    if role == "production" and collaborator:
        return query.filter(
            (Project.coordinator_id == collaborator.id)
            | Project.members.any(id=collaborator.id)
        )
    return query


@bp.route("/")
@login_required
def index():
    refresh_overdue_receivables()
    refresh_expired_contracts()
    ensure_renewal_notifications()
    db.session.commit()

    can_clients = current_user.has_permission("clients.view")
    can_crm = current_user.has_permission("crm.view")
    can_projects = current_user.has_permission("projects.view")
    can_tasks = current_user.has_permission("tasks.view")
    can_finance = current_user.has_permission("finance.view")

    client_query = _client_scope(Client.query)

    total_clients = client_query.filter_by(record_type="cliente").count() if can_clients else None
    followups = client_query.filter_by(record_type="seguimiento").count() if can_crm else None

    active_projects = None
    if can_projects:
        active_projects = _project_scope(
            Project.query.filter(Project.status.notin_(["completado", "cancelado"]))
        ).count()

    pending_tasks = None
    if can_tasks:
        pending_tasks = _task_scope(
            Task.query.filter(Task.status.notin_(["completada", "cancelada"]))
        ).count()

    confirmed_income = None
    if can_finance:
        confirmed_income = db.session.query(
            func.coalesce(func.sum(Payment.amount), 0)
        ).filter(Payment.status == "confirmado").scalar()

    recent_clients = []
    if can_clients or can_crm:
        recent_query = client_query
        if can_clients and not can_crm:
            recent_query = recent_query.filter(Client.record_type == "cliente")
        elif can_crm and not can_clients:
            recent_query = recent_query.filter(Client.record_type == "seguimiento")
        recent_clients = recent_query.order_by(Client.created_at.desc()).limit(6).all()

    recent_notifications = (
        Notification.query
        .filter_by(user_id=current_user.id)
        .order_by(Notification.created_at.desc())
        .limit(6)
        .all()
    )

    return render_template(
        "dashboard/index.html",
        total_clients=total_clients,
        followups=followups,
        active_projects=active_projects,
        pending_tasks=pending_tasks,
        confirmed_income=confirmed_income,
        recent_clients=recent_clients,
        recent_notifications=recent_notifications,
    )


@bp.route("/notifications/<int:notification_id>/read", methods=["POST"])
@login_required
def read_notification(notification_id):
    notification = db.get_or_404(Notification, notification_id)
    if notification.user_id == current_user.id or current_user.is_superadmin:
        notification.read = True
        db.session.commit()
    return redirect(notification.link or url_for("dashboard.index"))


@bp.route("/search")
@login_required
def search():
    q = request.args.get("q", "").strip()
    clients = []
    quotes = []
    sales = []
    tickets = []

    if q:
        like = f"%{q}%"

        if current_user.has_permission("clients.view") or current_user.has_permission("crm.view"):
            client_query = _client_scope(
                Client.query.filter(
                    or_(
                        Client.business_name.ilike(like),
                        Client.contact_name.ilike(like),
                        Client.phone.ilike(like),
                        Client.email.ilike(like),
                        Client.code.ilike(like),
                    )
                )
            )
            if current_user.has_permission("clients.view") and not current_user.has_permission("crm.view"):
                client_query = client_query.filter(Client.record_type == "cliente")
            elif current_user.has_permission("crm.view") and not current_user.has_permission("clients.view"):
                client_query = client_query.filter(Client.record_type == "seguimiento")
            clients = client_query.limit(20).all()

        if current_user.has_permission("crm.view"):
            quote_query = Quote.query.filter(Quote.quote_no.ilike(like))
            role = current_user.role.name if current_user.role else ""
            collaborator = current_user.collaborator
            if role == "advisor" and collaborator:
                quote_query = quote_query.filter(
                    (Quote.advisor_id == collaborator.id)
                    | Quote.client.has(owner_id=collaborator.id)
                )
            elif role == "supervisor" and collaborator:
                allowed_ids = [collaborator.id] + [c.id for c in collaborator.subordinates]
                quote_query = quote_query.filter(Quote.client.has(Client.owner_id.in_(allowed_ids)))
            quotes = quote_query.limit(10).all()

        if current_user.has_permission("sales.view"):
            sale_query = Sale.query.filter(Sale.sale_no.ilike(like))
            role = current_user.role.name if current_user.role else ""
            collaborator = current_user.collaborator
            if role == "advisor" and collaborator:
                sale_query = sale_query.filter(
                    (Sale.advisor_id == collaborator.id)
                    | Sale.client.has(owner_id=collaborator.id)
                )
            elif role == "supervisor" and collaborator:
                allowed_ids = [collaborator.id] + [c.id for c in collaborator.subordinates]
                sale_query = sale_query.filter(
                    (Sale.advisor_id.in_(allowed_ids))
                    | Sale.client.has(Client.owner_id.in_(allowed_ids))
                )
            sales = sale_query.limit(10).all()

        if current_user.has_permission("support.view"):
            ticket_query = SupportTicket.query.filter(
                or_(SupportTicket.ticket_no.ilike(like), SupportTicket.subject.ilike(like))
            )
            role = current_user.role.name if current_user.role else ""
            collaborator = current_user.collaborator
            if role == "advisor" and collaborator:
                ticket_query = ticket_query.filter(SupportTicket.client.has(owner_id=collaborator.id))
            elif role == "supervisor" and collaborator:
                allowed_ids = [collaborator.id] + [c.id for c in collaborator.subordinates]
                ticket_query = ticket_query.filter(SupportTicket.client.has(Client.owner_id.in_(allowed_ids)))
            tickets = ticket_query.limit(10).all()

    return render_template(
        "dashboard/search.html",
        q=q,
        clients=clients,
        quotes=quotes,
        sales=sales,
        tickets=tickets,
    )
