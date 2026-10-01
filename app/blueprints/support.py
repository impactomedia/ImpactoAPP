from datetime import datetime, timedelta

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.helpers import audit, next_code
from app.models import Client, Collaborator, SupportTicket, TicketComment

bp = Blueprint("support", __name__, url_prefix="/support")

PRIORITIES = {"baja", "normal", "alta", "urgente"}
TICKET_TYPES = {"soporte", "website", "redes", "pago", "acceso", "diseño", "impresión", "otro"}
TICKET_STATUSES = {"nuevo", "asignado", "en_proceso", "esperando_cliente", "resuelto", "cerrado"}


def _client_scope(query):
    role = current_user.role.name if current_user.role else ""
    collaborator = current_user.collaborator

    if role == "advisor" and collaborator:
        return query.filter(Client.owner_id == collaborator.id)

    if role == "supervisor" and collaborator:
        allowed_ids = [collaborator.id] + [row.id for row in collaborator.subordinates]
        return query.filter(Client.owner_id.in_(allowed_ids))

    return query


def _active_collaborators():
    return Collaborator.query.filter_by(status="activo").order_by(Collaborator.id).all()


@bp.route("/", methods=["GET", "POST"])
@login_required
def index():
    clients = (
        _client_scope(Client.query)
        .filter(Client.record_type == "cliente", Client.client_status != "archivado")
        .order_by(Client.business_name)
        .all()
    )
    visible_client_ids = {client.id for client in clients}
    collaborators = _active_collaborators()
    collaborator_ids = {row.id for row in collaborators}

    if request.method == "POST":
        client_id = request.form.get("client_id", type=int)
        client = db.session.get(Client, client_id) if client_id else None
        if not client or client.id not in visible_client_ids:
            flash("Selecciona un cliente válido dentro de tu cartera autorizada.", "danger")
            return redirect(url_for("support.index"))

        responsible_id = request.form.get("responsible_id", type=int)
        if responsible_id and responsible_id not in collaborator_ids:
            flash("Selecciona un responsable activo válido.", "danger")
            return redirect(url_for("support.index"))

        priority = request.form.get("priority", "normal")
        if priority not in PRIORITIES:
            priority = "normal"

        ticket_type = request.form.get("ticket_type", "soporte")
        if ticket_type not in TICKET_TYPES:
            ticket_type = "otro"

        subject = (request.form.get("subject") or "").strip() or "Solicitud"
        description = (request.form.get("description") or "").strip() or None
        first_hours = {"baja": 24, "normal": 8, "alta": 4, "urgente": 1}[priority]
        resolve_hours = {"baja": 96, "normal": 48, "alta": 24, "urgente": 8}[priority]

        ticket = SupportTicket(
            ticket_no=next_code("TCK", SupportTicket),
            client_id=client.id,
            ticket_type=ticket_type,
            channel=(request.form.get("channel") or "interno").strip(),
            priority=priority,
            responsible_id=responsible_id,
            status="nuevo",
            subject=subject,
            description=description,
            first_response_due=datetime.utcnow() + timedelta(hours=first_hours),
            resolution_due=datetime.utcnow() + timedelta(hours=resolve_hours),
        )
        db.session.add(ticket)
        db.session.flush()
        audit("crear_ticket", "SupportTicket", ticket.id, after={"ticket_no": ticket.ticket_no})
        db.session.commit()
        flash("Ticket creado.", "success")
        return redirect(url_for("support.detail", ticket_id=ticket.id))

    ticket_query = SupportTicket.query
    role = current_user.role.name if current_user.role else ""
    collaborator = current_user.collaborator
    if role == "advisor" and collaborator:
        ticket_query = ticket_query.filter(SupportTicket.client.has(owner_id=collaborator.id))
    elif role == "supervisor" and collaborator:
        allowed_ids = [collaborator.id] + [row.id for row in collaborator.subordinates]
        ticket_query = ticket_query.filter(SupportTicket.client.has(Client.owner_id.in_(allowed_ids)))

    tickets = ticket_query.order_by(SupportTicket.created_at.desc()).all()
    return render_template(
        "support/index.html",
        tickets=tickets,
        clients=clients,
        collaborators=collaborators,
        now=datetime.utcnow(),
    )


@bp.route("/<int:ticket_id>")
@login_required
def detail(ticket_id):
    ticket = db.get_or_404(SupportTicket, ticket_id)
    return render_template(
        "support/detail.html",
        ticket=ticket,
        collaborators=_active_collaborators(),
        now=datetime.utcnow(),
    )


@bp.route("/<int:ticket_id>/update", methods=["POST"])
@login_required
def update(ticket_id):
    ticket = db.get_or_404(SupportTicket, ticket_id)
    new_status = request.form.get("status", ticket.status)
    if new_status not in TICKET_STATUSES:
        flash("Estado de ticket no válido.", "danger")
        return redirect(url_for("support.detail", ticket_id=ticket.id))

    responsible_id = request.form.get("responsible_id", type=int)
    if responsible_id:
        responsible = db.session.get(Collaborator, responsible_id)
        if not responsible or responsible.status != "activo":
            flash("El responsable seleccionado no existe o está inactivo.", "danger")
            return redirect(url_for("support.detail", ticket_id=ticket.id))

    before = {"status": ticket.status, "responsible_id": ticket.responsible_id}
    ticket.status = new_status
    if responsible_id:
        ticket.responsible_id = responsible_id

    if ticket.status in {"resuelto", "cerrado"} and not ticket.resolved_at:
        ticket.resolved_at = datetime.utcnow()
    elif ticket.status not in {"resuelto", "cerrado"}:
        ticket.resolved_at = None

    audit(
        "actualizar_ticket",
        "SupportTicket",
        ticket.id,
        before=before,
        after={"status": ticket.status, "responsible_id": ticket.responsible_id},
    )
    db.session.commit()
    flash("Ticket actualizado.", "success")
    return redirect(url_for("support.detail", ticket_id=ticket.id))


@bp.route("/<int:ticket_id>/comment", methods=["POST"])
@login_required
def comment(ticket_id):
    ticket = db.get_or_404(SupportTicket, ticket_id)
    body = (request.form.get("body") or "").strip()
    if not body:
        flash("Escribe un comentario antes de guardar.", "warning")
        return redirect(url_for("support.detail", ticket_id=ticket.id))

    db.session.add(TicketComment(ticket_id=ticket.id, user_id=current_user.id, body=body))
    audit("comentario_ticket", "SupportTicket", ticket.id)
    db.session.commit()
    flash("Comentario agregado.", "success")
    return redirect(url_for("support.detail", ticket_id=ticket.id))
