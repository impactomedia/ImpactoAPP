from datetime import datetime, timedelta
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user

from app.extensions import db
from app.helpers import audit, next_code
from app.models import SupportTicket, TicketComment, Client, Collaborator

bp = Blueprint("support", __name__, url_prefix="/support")


@bp.route("/", methods=["GET", "POST"])
@login_required
def index():
    clients = Client.query.filter_by(record_type="cliente").order_by(Client.business_name).all()
    collaborators = Collaborator.query.filter_by(status="activo").all()
    if request.method == "POST":
        priority = request.form.get("priority", "normal")
        first_hours = {"baja": 24, "normal": 8, "alta": 4, "urgente": 1}.get(priority, 8)
        resolve_hours = {"baja": 96, "normal": 48, "alta": 24, "urgente": 8}.get(priority, 48)
        t = SupportTicket(
            ticket_no=next_code("TCK", SupportTicket),
            client_id=request.form.get("client_id", type=int),
            ticket_type=request.form.get("ticket_type", "soporte"),
            channel=request.form.get("channel", "interno"),
            priority=priority,
            responsible_id=request.form.get("responsible_id", type=int),
            status="nuevo",
            subject=request.form.get("subject", "Solicitud"),
            description=request.form.get("description"),
            first_response_due=datetime.utcnow() + timedelta(hours=first_hours),
            resolution_due=datetime.utcnow() + timedelta(hours=resolve_hours),
        )
        db.session.add(t)
        db.session.flush()
        audit("crear_ticket", "SupportTicket", t.id, after={"ticket_no": t.ticket_no})
        db.session.commit()
        flash("Ticket creado.", "success")
        return redirect(url_for("support.detail", ticket_id=t.id))
    tickets = SupportTicket.query.order_by(SupportTicket.created_at.desc()).all()
    return render_template("support/index.html", tickets=tickets, clients=clients, collaborators=collaborators)


@bp.route("/<int:ticket_id>")
@login_required
def detail(ticket_id):
    ticket = db.get_or_404(SupportTicket, ticket_id)
    collaborators = Collaborator.query.filter_by(status="activo").all()
    return render_template("support/detail.html", ticket=ticket, collaborators=collaborators)


@bp.route("/<int:ticket_id>/update", methods=["POST"])
@login_required
def update(ticket_id):
    t = db.get_or_404(SupportTicket, ticket_id)
    before = {"status": t.status, "responsible_id": t.responsible_id}
    t.status = request.form.get("status", t.status)
    t.responsible_id = request.form.get("responsible_id", type=int) or t.responsible_id
    if t.status in {"resuelto", "cerrado"} and not t.resolved_at:
        t.resolved_at = datetime.utcnow()
    audit("actualizar_ticket", "SupportTicket", t.id, before=before, after={"status": t.status, "responsible_id": t.responsible_id})
    db.session.commit()
    flash("Ticket actualizado.", "success")
    return redirect(url_for("support.detail", ticket_id=t.id))


@bp.route("/<int:ticket_id>/comment", methods=["POST"])
@login_required
def comment(ticket_id):
    t = db.get_or_404(SupportTicket, ticket_id)
    body = request.form.get("body", "").strip()
    if body:
        db.session.add(TicketComment(ticket_id=t.id, user_id=current_user.id, body=body))
        db.session.commit()
    return redirect(url_for("support.detail", ticket_id=t.id))
