from datetime import date, datetime, timedelta
from flask import Blueprint, render_template, redirect, request, url_for
from flask_login import login_required, current_user
from sqlalchemy import func, or_

from app.extensions import db
from app.models import Client, Collaborator, Sale, Payment, Task, Project, PrintOrder, Expense, Commission, AttendanceMark, Notification, Quote, SupportTicket
from app.services import refresh_overdue_receivables, refresh_expired_contracts, ensure_renewal_notifications

bp = Blueprint("dashboard", __name__, url_prefix="/dashboard")


@bp.route("/")
@login_required
def index():
    refresh_overdue_receivables()
    refresh_expired_contracts()
    ensure_renewal_notifications()
    db.session.commit()

    role = current_user.role.name if current_user.role else ""
    collaborator = current_user.collaborator
    client_query = Client.query
    if role == "advisor" and collaborator:
        client_query = client_query.filter_by(owner_id=collaborator.id)
    total_clients = client_query.filter_by(record_type="cliente").count()
    followups = client_query.filter_by(record_type="seguimiento").count()
    active_projects = Project.query.filter(Project.status.notin_(["completado", "cancelado"])).count()
    pending_tasks = Task.query.filter(Task.status.notin_(["completada", "cancelada"])).count()
    confirmed_income = db.session.query(func.coalesce(func.sum(Payment.amount), 0)).filter(Payment.status == "confirmado").scalar()
    recent_clients = client_query.order_by(Client.created_at.desc()).limit(6).all()
    recent_notifications = Notification.query.filter_by(user_id=current_user.id).order_by(Notification.created_at.desc()).limit(6).all()
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
    n = db.get_or_404(Notification, notification_id)
    if n.user_id == current_user.id or current_user.is_superadmin:
        n.read = True
        db.session.commit()
    return redirect(n.link or url_for("dashboard.index"))


@bp.route("/search")
@login_required
def search():
    q = request.args.get("q", "").strip()
    clients = quotes = sales = tickets = []
    if q:
        like = f"%{q}%"
        clients = Client.query.filter(or_(Client.business_name.ilike(like), Client.contact_name.ilike(like), Client.phone.ilike(like), Client.email.ilike(like), Client.code.ilike(like))).limit(20).all()
        quotes = Quote.query.filter(Quote.quote_no.ilike(like)).limit(10).all()
        sales = Sale.query.filter(Sale.sale_no.ilike(like)).limit(10).all()
        tickets = SupportTicket.query.filter(or_(SupportTicket.ticket_no.ilike(like), SupportTicket.subject.ilike(like))).limit(10).all()
    return render_template("dashboard/search.html", q=q, clients=clients, quotes=quotes, sales=sales, tickets=tickets)
