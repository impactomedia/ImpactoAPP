from datetime import date, datetime, time

from flask_login import current_user
from sqlalchemy import or_

from app.extensions import db
from app.helpers import notify
from app.models import Attachment, Client, ClientCollaborator, Collaborator, Notification
from app.access_control import _project_allowed, _task_allowed
from app.client_v2_models import ClientTeamAssignment


ACTIVE_TEAM_STATUS = "activa"
FINAL_TEAM_STATUS = "finalizada"
SUSPENDED_TEAM_STATUS = "suspendida"


def _as_datetime(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, time.min)
    return None


def _user_role():
    return current_user.role.name if current_user.is_authenticated and current_user.role else ""


def _current_team_ids():
    collaborator = current_user.collaborator if current_user.is_authenticated else None
    if not collaborator:
        return []
    return [collaborator.id] + [row.id for row in collaborator.subordinates]


def _assignment_client_ids(collaborator_ids):
    if not collaborator_ids:
        return []
    rows = (
        db.session.query(ClientTeamAssignment.client_id)
        .filter(
            ClientTeamAssignment.collaborator_id.in_(collaborator_ids),
            ClientTeamAssignment.status == ACTIVE_TEAM_STATUS,
        )
        .distinct()
        .all()
    )
    return [row[0] for row in rows]


def my_clients_query():
    query = Client.query.filter(
        Client.record_type == "cliente",
        Client.client_status != "archivado",
    )
    role = _user_role()

    if role in {"superadmin", "admin", "manager"}:
        return query

    collaborator = current_user.collaborator
    if not collaborator:
        return query.filter(Client.id == -1)

    if role == "supervisor":
        ids = _current_team_ids()
        assigned = _assignment_client_ids(ids)
        conditions = [Client.owner_id.in_(ids)]
        if assigned:
            conditions.append(Client.id.in_(assigned))
        return query.filter(or_(*conditions))

    assigned = _assignment_client_ids([collaborator.id])
    conditions = []
    if role == "advisor":
        conditions.append(Client.owner_id == collaborator.id)
    if assigned:
        conditions.append(Client.id.in_(assigned))

    if not conditions:
        return query.filter(Client.id == -1)
    return query.filter(or_(*conditions))


def client_coordination_allowed(client):
    role = _user_role()
    if role in {"superadmin", "admin", "manager", "finance"}:
        return True

    collaborator = current_user.collaborator
    if not collaborator:
        return False

    if role == "advisor":
        return (
            client.owner_id == collaborator.id
            or ClientTeamAssignment.query.filter_by(
                client_id=client.id,
                collaborator_id=collaborator.id,
                status=ACTIVE_TEAM_STATUS,
            ).first()
            is not None
        )

    if role == "supervisor":
        team_ids = _current_team_ids()
        return (
            client.owner_id in team_ids
            or ClientTeamAssignment.query.filter(
                ClientTeamAssignment.client_id == client.id,
                ClientTeamAssignment.collaborator_id.in_(team_ids),
                ClientTeamAssignment.status == ACTIVE_TEAM_STATUS,
            ).first()
            is not None
        )

    if role == "production":
        assigned = ClientTeamAssignment.query.filter_by(
            client_id=client.id,
            collaborator_id=collaborator.id,
            status=ACTIVE_TEAM_STATUS,
        ).first()
        if assigned:
            return True
        return any(_project_allowed(project) for project in client.projects)

    return current_user.has_permission("clients.view")


def sync_legacy_team_assignments():
    """Migra filas legacy y mantiene un resumen compatible para asignaciones V3 activas."""
    today = date.today()
    legacy_rows = ClientCollaborator.query.all()

    for legacy in legacy_rows:
        existing = (
            ClientTeamAssignment.query
            .filter_by(legacy_assignment_id=legacy.id)
            .order_by(ClientTeamAssignment.id)
            .first()
        )
        if existing:
            continue

        role = (legacy.role_in_client or "").strip()
        if not role and legacy.collaborator:
            role = legacy.collaborator.job_title or "Colaborador"

        db.session.add(
            ClientTeamAssignment(
                client_id=legacy.client_id,
                collaborator_id=legacy.collaborator_id,
                role_in_client=role or "Colaborador",
                starts_on=(legacy.created_at.date() if legacy.created_at else today),
                status=ACTIVE_TEAM_STATUS,
                primary=bool(legacy.primary),
                legacy_assignment_id=legacy.id,
                notes="Migrado automáticamente desde la asignación anterior.",
            )
        )

    db.session.flush()

    active_pairs = (
        db.session.query(
            ClientTeamAssignment.client_id,
            ClientTeamAssignment.collaborator_id,
        )
        .filter(ClientTeamAssignment.status == ACTIVE_TEAM_STATUS)
        .distinct()
        .all()
    )
    for client_id, collaborator_id in active_pairs:
        sync_legacy_summary_for_pair(client_id, collaborator_id)


def sync_legacy_summary_for_pair(client_id, collaborator_id):
    active = (
        ClientTeamAssignment.query
        .filter_by(
            client_id=client_id,
            collaborator_id=collaborator_id,
            status=ACTIVE_TEAM_STATUS,
        )
        .order_by(ClientTeamAssignment.primary.desc(), ClientTeamAssignment.id)
        .all()
    )
    legacy = ClientCollaborator.query.filter_by(
        client_id=client_id,
        collaborator_id=collaborator_id,
    ).first()

    if not active:
        if legacy:
            db.session.delete(legacy)
        return None

    representative = active[0]
    if not legacy:
        legacy = ClientCollaborator(
            client_id=client_id,
            collaborator_id=collaborator_id,
            role_in_client=representative.role_in_client,
            primary=any(row.primary for row in active),
        )
        db.session.add(legacy)
        db.session.flush()
    else:
        legacy.role_in_client = representative.role_in_client
        legacy.primary = any(row.primary for row in active)

    for row in active:
        row.legacy_assignment_id = legacy.id
    return legacy


def build_client_alerts(client, can_financial=False, visible_tasks=None, visible_tickets=None, horizon_days=60):
    today = date.today()
    alerts = []

    def add(kind, title, due_date, detail="", force=False):
        if not due_date:
            return
        if isinstance(due_date, datetime):
            due_date = due_date.date()
        days = (due_date - today).days
        if not force and days > horizon_days:
            return
        if days < 0:
            severity = "danger"
            timing = f"Vencido hace {abs(days)} día(s)"
        elif days <= 7:
            severity = "danger"
            timing = "Hoy" if days == 0 else f"En {days} día(s)"
        elif days <= 15:
            severity = "warning"
            timing = f"En {days} día(s)"
        elif days <= 30:
            severity = "info"
            timing = f"En {days} día(s)"
        else:
            severity = "muted"
            timing = f"En {days} día(s)"
        alerts.append({
            "kind": kind,
            "title": title,
            "due_date": due_date,
            "days": days,
            "severity": severity,
            "timing": timing,
            "detail": detail or "",
        })

    for renewal in client.renewals:
        if renewal.status in {"renovado", "cancelado", "no_renueva"}:
            continue
        add("Renovación", renewal.renewal_type, renewal.due_date, renewal.status)

    for contract in client.contracts:
        if contract.status not in {"activo", "caducado"} or not contract.ends_on:
            continue
        title = contract.product.name if contract.product else "Servicio"
        add("Servicio", f"Vencimiento de {title}", contract.ends_on, contract.status)

    profile = getattr(client, "operational_profile", None)
    if profile:
        add("Dominio", "Renovación de dominio", profile.domain_renews_on, profile.domain_notes or "")
        add("Hosting", "Renovación de hosting", profile.hosting_renews_on, profile.domain_notes or "")

    if can_financial:
        for installment in getattr(client, "installments", []) or []:
            if installment.status == "pagada":
                continue
            sale_no = installment.sale.sale_no if installment.sale else "Venta"
            add(
                "Cuota",
                f"{sale_no} · Cuota #{installment.sequence}",
                installment.due_date,
                f"Saldo de cuota: {float((installment.amount or 0) - (installment.paid_amount or 0)):,.2f}",
            )

        for note in getattr(client, "collection_notes", []) or []:
            if note.note_type != "promesa" or note.status in {"cumplida", "cancelada"}:
                continue
            add(
                "Promesa de pago",
                note.sale.sale_no if note.sale else "Compromiso de pago",
                note.promise_date,
                note.body,
                force=note.status == "incumplida",
            )

    for task in visible_tasks or []:
        if task.status in {"completada", "cancelada"} or not task.due_at:
            continue
        add("Tarea", task.title, task.due_at, task.status)

    for ticket in visible_tickets or []:
        if ticket.status in {"resuelto", "cerrado"}:
            continue
        due = ticket.resolution_due or ticket.first_response_due
        add("Ticket", f"{ticket.ticket_no} · {ticket.subject}", due, ticket.status)

    return sorted(alerts, key=lambda row: (row["due_date"], row["title"]))


def build_client_timeline(client, can_financial=False):
    events = []

    def add(when, kind, title, body=""):
        dt = _as_datetime(when)
        if not dt:
            return
        events.append({
            "when": dt,
            "kind": kind,
            "title": title,
            "body": body or "",
        })

    for interaction in client.interactions:
        add(
            interaction.occurred_at or interaction.created_at,
            "Interacción",
            interaction.subject or interaction.interaction_type,
            interaction.notes or interaction.result or "",
        )

    for comment in client.comments:
        add(
            comment.created_at,
            "Comentario interno",
            f"{comment.user.name if comment.user else 'Usuario'} · {comment.process_type}",
            comment.body,
        )

    for contract in client.contracts:
        product_name = contract.product.name if contract.product else "Servicio"
        add(
            contract.created_at,
            "Servicio",
            product_name,
            f"{contract.status} · {contract.starts_on} a {contract.ends_on or 'sin vencimiento'}",
        )

    for assignment in client.team_assignments_v3:
        label = assignment.service_label or assignment.role_in_client
        add(
            assignment.created_at,
            "Equipo",
            f"{assignment.collaborator.user.name if assignment.collaborator and assignment.collaborator.user else 'Colaborador'} asignado",
            f"{assignment.role_in_client} · {label}",
        )
        if assignment.ends_on:
            add(
                assignment.ends_on,
                "Equipo",
                f"Asignación finalizada: {assignment.collaborator.user.name if assignment.collaborator and assignment.collaborator.user else 'Colaborador'}",
                assignment.end_reason or assignment.role_in_client,
            )

    for history in client.ownership_history:
        previous = db.session.get(Collaborator, history.previous_owner_id) if history.previous_owner_id else None
        new = db.session.get(Collaborator, history.new_owner_id) if history.new_owner_id else None
        prev_name = previous.user.name if previous and previous.user else "Sin responsable"
        new_name = new.user.name if new and new.user else "Sin responsable"
        add(history.changed_at, "Responsable comercial", f"{prev_name} → {new_name}", history.reason or "")

    if current_user.has_permission("projects.view"):
        for project in client.projects:
            if _project_allowed(project):
                add(project.created_at, "Proyecto", project.name, f"{project.status} · {project.progress}%")

    if current_user.has_permission("tasks.view"):
        for task in client.tasks:
            if _task_allowed(task):
                add(task.created_at, "Tarea", task.title, task.status)

    if current_user.has_permission("support.view"):
        for ticket in client.tickets:
            add(ticket.created_at, "Ticket", f"{ticket.ticket_no} · {ticket.subject}", ticket.status)

    for renewal in client.renewals:
        add(renewal.created_at, "Renovación", renewal.renewal_type, f"Vence {renewal.due_date} · {renewal.status}")

    attachments = (
        Attachment.query
        .filter_by(entity_type="Client", entity_id=client.id)
        .order_by(Attachment.created_at)
        .all()
    )
    for attachment in attachments:
        meta = getattr(attachment, "client_document_meta", None)
        category = meta.category if meta else "otros"
        add(attachment.created_at, "Archivo", attachment.file_name, category)

    if can_financial:
        for sale in client.sales:
            add(sale.created_at, "Venta", sale.sale_no, f"{sale.currency} {float(sale.total or 0):,.2f}")
        for payment in client.payments:
            add(
                payment.created_at,
                "Pago",
                payment.sale.sale_no if payment.sale else "Pago",
                f"{payment.currency} {float(payment.amount or 0):,.2f} · {payment.status}",
            )
        for note in getattr(client, "collection_notes", []) or []:
            add(note.created_at, "Cobranza", note.note_type, note.body)

    return sorted(events, key=lambda row: row["when"], reverse=True)


def _notification_exists(user_id, title, message):
    return (
        Notification.query
        .filter_by(user_id=user_id, title=title, message=message, read=False)
        .first()
        is not None
    )


def _active_team_user_ids(client_id):
    rows = ClientTeamAssignment.query.filter_by(
        client_id=client_id,
        status=ACTIVE_TEAM_STATUS,
    ).all()
    user_ids = set()
    for row in rows:
        collaborator = row.collaborator
        if collaborator and collaborator.user and collaborator.user.active:
            user_ids.add(collaborator.user.id)
    return user_ids


def ensure_client_v3_notifications(days=(30, 15, 7, 0)):
    today = date.today()
    from app.models import Renewal

    for renewal in Renewal.query.filter_by(status="pendiente").all():
        delta = (renewal.due_date - today).days
        if delta not in days:
            continue

        owner_user_id = renewal.client.owner.user_id if renewal.client.owner else None
        team_user_ids = _active_team_user_ids(renewal.client_id)
        if owner_user_id:
            team_user_ids.discard(owner_user_id)

        title = f"Equipo · Renovación: {renewal.client.business_name}"
        message = f"{renewal.renewal_type} vence en {delta} día(s) ({renewal.due_date})."
        for user_id in team_user_ids:
            if not _notification_exists(user_id, title, message):
                notify(
                    user_id,
                    title,
                    message,
                    link=f"/clients/{renewal.client_id}/coordination",
                    priority="alta" if delta <= 7 else "normal",
                )

    clients = Client.query.filter(Client.record_type == "cliente").all()
    for client in clients:
        profile = getattr(client, "operational_profile", None)
        if not profile:
            continue

        targets = _active_team_user_ids(client.id)
        if client.owner and client.owner.user_id:
            targets.add(client.owner.user_id)

        for label, due_date in [
            ("Dominio", profile.domain_renews_on),
            ("Hosting", profile.hosting_renews_on),
        ]:
            if not due_date:
                continue
            delta = (due_date - today).days
            if delta not in days:
                continue
            title = f"{label}: {client.business_name}"
            message = f"{label} vence en {delta} día(s) ({due_date})."
            for user_id in targets:
                if not _notification_exists(user_id, title, message):
                    notify(
                        user_id,
                        title,
                        message,
                        link=f"/clients/{client.id}/coordination",
                        priority="alta" if delta <= 7 else "normal",
                    )
