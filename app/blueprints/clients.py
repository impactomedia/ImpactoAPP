import csv
import io
import json
import unicodedata
from pathlib import Path
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from flask import Blueprint, render_template, request, redirect, url_for, flash, Response, current_app, send_from_directory, abort
from flask_login import login_required, current_user
from sqlalchemy import or_

from app.extensions import db
from app.decorators import permission_required, roles_required
from app.access_control import _commercial_client_allowed, _project_allowed, _task_allowed
from app.helpers import audit, next_code, save_upload
from app.services import recalc_sale, active_principal_contract
from app.models import (
    Client,
    Collaborator,
    ClientCollaborator,
    ClientContract,
    ProductService,
    Attachment,
    ClientComment,
    AdvisorProject,
    User,
    OwnershipHistory,
    Interaction,
    Quote,
    Sale,
    Payment,
    AccountReceivable,
    Project,
    Task,
    PrintOrder,
    Renewal,
    SupportTicket,
    TaskComment,
    ChangeRequest,
    TicketComment,
    PrintItem,
    DesignVersion,
    Shipment,
    PrintIncident,
    AuditLog,
)

from app.client_v2_models import (
    ClientOperationalProfile,
    ClientPlatform,
    ClientContractDetail,
    ClientInstallment,
    ClientCollectionNote,
    ClientDocumentMeta,
)

bp = Blueprint("clients", __name__, url_prefix="/clients")

CLIENT_STATUSES = {"activo", "inactivo", "caducado", "archivado"}
CONTRACT_STATUSES = {"activo", "inactivo", "caducado"}
PLATFORM_STATUSES = {"activo", "pendiente", "inactivo", "no_aplica"}
PLATFORM_DEFINITIONS = {
    "google_business": "Google Business Profile / Maps",
    "youtube": "YouTube",
    "tiktok": "TikTok",
    "vimeo": "Vimeo",
    "pinterest": "Pinterest",
    "linkedin": "LinkedIn",
    "manta": "Manta",
    "houzz": "Houzz",
    "porch": "Porch",
    "buildzoom": "BuildZoom",
    "merchantcircle": "MerchantCircle",
    "mapquest": "MapQuest",
    "yelp": "Yelp",
    "yellow_pages": "Yellow Pages",
}

DOCUMENT_CATEGORIES = {
    "administrativo": "Administrativo",
    "contrato": "Contrato / acuerdo",
    "branding": "Branding / logotipo",
    "website": "Website / dominio",
    "redes_sociales": "Redes sociales",
    "seo": "SEO / Google",
    "imprenta": "Imprenta",
    "comprobante": "Comprobante de pago",
    "otros": "Otros",
}
COLLECTION_NOTE_TYPES = {"cobranza", "promesa", "acuerdo", "seguimiento", "interno"}
COLLECTION_NOTE_STATUSES = {"abierta", "cumplida", "incumplida", "cancelada"}


def _normalize_text(value):
    value = str(value or "").strip().lower()
    value = "".join(
        char for char in unicodedata.normalize("NFKD", value)
        if not unicodedata.combining(char)
    )
    return " ".join(value.split())


def _normalize_key(value):
    value = _normalize_text(value)
    for char in (" ", "-", "/", ".", "#", "(", ")"):
        value = value.replace(char, "_")
    while "__" in value:
        value = value.replace("__", "_")
    return value.strip("_")


def _normalized_row(row):
    return {_normalize_key(key): (value or "").strip() for key, value in row.items() if key is not None}


def _pick(row, *keys):
    for key in keys:
        value = row.get(_normalize_key(key), "")
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _decimal_or_none(value):
    raw = str(value or "").strip().replace("$", "").replace(",", "")
    if not raw:
        return None
    try:
        return Decimal(raw)
    except (InvalidOperation, ValueError):
        return None


def _date_or_none(value):
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _clean_url(value):
    raw = str(value or "").strip()
    if not raw:
        return None
    if not raw.lower().startswith(("http://", "https://")):
        raw = f"https://{raw}"
    return raw[:500]


def _visible_client_query(query=None):
    if query is None:
        query = Client.query
    role = current_user.role.name if current_user.role else ""
    collaborator = current_user.collaborator

    if role == "advisor" and collaborator:
        return query.filter(Client.owner_id == collaborator.id)

    if role == "supervisor" and collaborator:
        allowed_ids = [collaborator.id] + [c.id for c in collaborator.subordinates]
        return query.filter(Client.owner_id.in_(allowed_ids))

    return query


def _active_collaborators():
    query = (
        Collaborator.query
        .join(User, Collaborator.user_id == User.id)
        .filter(Collaborator.status == "activo")
    )
    role = current_user.role.name if current_user.role else ""
    collaborator = current_user.collaborator

    if role == "advisor" and collaborator:
        query = query.filter(Collaborator.id == collaborator.id)
    elif role == "supervisor" and collaborator:
        allowed_ids = [collaborator.id] + [c.id for c in collaborator.subordinates]
        query = query.filter(Collaborator.id.in_(allowed_ids))

    return query.order_by(User.name).all()


def _owner_aliases(collaborators):
    aliases = {}
    for collaborator in collaborators:
        if not collaborator.user:
            continue
        full_name = _normalize_text(collaborator.user.name)
        if full_name:
            aliases.setdefault(full_name, collaborator.id)
            aliases.setdefault(full_name.split()[0], collaborator.id)
        if collaborator.code:
            aliases.setdefault(_normalize_text(collaborator.code), collaborator.id)
    return aliases


def _find_duplicate(business_name, phone=None, email=None, exclude_id=None):
    conditions = []
    if business_name:
        conditions.append(Client.business_name == business_name)
    if phone:
        conditions.append(Client.phone == phone)
    if email:
        conditions.append(Client.email == email)
    if not conditions:
        return None
    query = Client.query.filter(or_(*conditions))
    if exclude_id:
        query = query.filter(Client.id != exclude_id)
    return query.first()


def _apply_client_form(client):
    text_fields = [
        "business_name",
        "contact_name",
        "phone",
        "other_phones",
        "email",
        "preferred_channel",
        "industry",
        "services_offered",
        "website",
        "facebook",
        "instagram",
        "other_social",
        "address",
        "city",
        "state",
        "country",
        "timezone",
        "source",
        "priority",
        "main_interest",
        "notes",
    ]
    for field in text_fields:
        if field in request.form:
            value = request.form.get(field)
            if field == "email":
                value = (value or "").strip().lower()
            setattr(client, field, value.strip() if isinstance(value, str) else value)

    client.country = "USA"

    # Cambiar el responsable comercial es una acción separada de editar la ficha.
    # Solo quien tenga crm.transfer puede escoger otro responsable.
    if current_user.has_permission("crm.transfer"):
        client.owner_id = request.form.get("owner_id", type=int) or None
    elif current_user.collaborator and not client.owner_id:
        client.owner_id = current_user.collaborator.id

    client.client_status = request.form.get("client_status", client.client_status or "activo")
    if client.client_status not in CLIENT_STATUSES:
        client.client_status = "activo"
    client.estimated_budget = _decimal_or_none(request.form.get("estimated_budget"))
    client.record_type = "cliente"
    client.pipeline_stage = "venta_cerrada"


def _apply_operational_profile_form(client, profile=None):
    profile = profile or ClientOperationalProfile.query.filter_by(
        client_id=client.id
    ).first()
    if not profile:
        profile = ClientOperationalProfile(client_id=client.id)
        db.session.add(profile)

    text_fields = [
        "attention_days",
        "business_hours",
        "experience_text",
        "coverage_text",
        "payment_methods",
        "estimate_policy",
        "languages",
        "whatsapp_phone",
        "postal_code",
        "services_to_promote",
        "logo_status",
        "brand_colors",
        "domain_name",
        "domain_provider",
        "hosting_provider",
        "domain_notes",
        "operational_notes",
    ]
    for field in text_fields:
        if field in request.form:
            value = (request.form.get(field) or "").strip()
            setattr(profile, field, value or None)

    for field in [
        "operational_email",
        "corporate_email",
        "hosting_account_email",
    ]:
        if field in request.form:
            value = (request.form.get(field) or "").strip().lower()
            setattr(profile, field, value or None)

    for field in [
        "domain_activated_on",
        "domain_renews_on",
        "hosting_activated_on",
        "hosting_renews_on",
    ]:
        if field in request.form:
            setattr(profile, field, _date_or_none(request.form.get(field)))

    return profile



def _timeline_datetime(value):
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    return None


def _timeline_event(events, when, category, kind, title, body=None, link=None, actor=None):
    dt = _timeline_datetime(when)
    if not dt:
        return
    events.append({
        "dt": dt,
        "category": category,
        "kind": kind,
        "title": title,
        "body": body or "",
        "link": link,
        "actor": actor,
    })


def _json_dict(raw):
    if not raw:
        return {}
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def _audit_change_text(log):
    before = _json_dict(log.before_json)
    after = _json_dict(log.after_json)
    parts = []

    if "status" in before or "status" in after:
        previous = before.get("status")
        current = after.get("status")
        if previous != current:
            if previous and current:
                parts.append(
                    f"Estado: {str(previous).replace('_', ' ').title()} → "
                    f"{str(current).replace('_', ' ').title()}"
                )
            elif current:
                parts.append(f"Estado: {str(current).replace('_', ' ').title()}")

    if "progress" in before or "progress" in after:
        previous = before.get("progress")
        current = after.get("progress")
        if previous != current and current is not None:
            parts.append(f"Progreso: {current}%")

    if "decision" in after:
        parts.append(f"Decisión: {str(after['decision']).replace('_', ' ').title()}")
    if "tracking" in after and after.get("tracking"):
        parts.append(f"Tracking: {after['tracking']}")
    if "incident" in after and after.get("incident"):
        parts.append(f"Incidencia: {str(after['incident']).replace('_', ' ').title()}")
    if "resolution" in after and after.get("resolution"):
        parts.append(f"Resolución: {after['resolution']}")

    if not parts and log.reason:
        parts.append(log.reason)

    return " · ".join(parts) or "Registro actualizado."


def _build_client_timeline(
    client,
    *,
    attachments,
    visible_projects,
    visible_tasks,
    can_financial,
):
    """Construye un Timeline único leyendo los módulos reales, sin duplicar datos."""
    events = []

    can_crm = current_user.has_permission("crm.view")
    can_sales = current_user.has_permission("sales.view")
    can_projects = current_user.has_permission("projects.view")
    can_tasks = current_user.has_permission("tasks.view")
    can_support = current_user.has_permission("support.view")
    can_printing = current_user.has_permission("printing.view")

    # CRM / seguimiento preservado.
    if can_crm:
        for interaction in client.interactions:
            _timeline_event(
                events,
                interaction.occurred_at or interaction.created_at,
                "crm",
                "Interacción",
                interaction.subject or interaction.interaction_type.replace("_", " ").title(),
                interaction.notes or interaction.result,
                actor=interaction.user.name if interaction.user else None,
            )

        for quote in client.quotes:
            body = f"Estado: {quote.status.replace('_', ' ').title()}"
            if can_financial:
                body += f" · Total USD {quote.total}"
            _timeline_event(
                events,
                quote.created_at,
                "comercial",
                "Cotización",
                quote.quote_no,
                body,
            )

    # Servicio principal / contratos: visible como contexto operativo, sin exponer saldo.
    for contract in client.contracts:
        product_name = contract.product.name if contract.product else "Servicio"
        kind = "Servicio principal" if contract.principal else "Servicio"
        body = f"Estado: {contract.status.replace('_', ' ').title()}"
        if contract.starts_on:
            body += f" · Inicio: {contract.starts_on}"
        if contract.ends_on:
            body += f" · Fin: {contract.ends_on}"
        _timeline_event(
            events,
            contract.created_at,
            "comercial",
            kind,
            product_name,
            body,
        )

    # Ventas, upgrades, compras y pagos solo con permiso comercial/financiero.
    if can_financial:
        for sale in client.sales:
            operation_type = (
                sale.operation_meta.operation_type
                if sale.operation_meta
                else "additional_purchase"
            )
            sale_label = {
                "principal_service": "Servicio principal",
                "principal_upgrade": "Upgrade",
                "additional_purchase": "Compra adicional",
            }.get(operation_type, "Venta")
            _timeline_event(
                events,
                sale.created_at,
                "comercial",
                sale_label,
                sale.sale_no,
                f"Total USD {sale.total} · Estado: {sale.status.replace('_', ' ').title()}",
                link=url_for("sales.detail", sale_id=sale.id),
            )

        for payment in client.payments:
            _timeline_event(
                events,
                payment.created_at,
                "finanzas",
                "Pago",
                f"USD {payment.amount}",
                (
                    f"{payment.method or 'Método no indicado'}"
                    + (f" · Ref. {payment.reference}" if payment.reference else "")
                    + f" · {payment.status.replace('_', ' ').title()}"
                ),
                link=url_for("sales.detail", sale_id=payment.sale_id),
            )

        for note in client.collection_notes:
            detail = note.body
            if note.promise_date:
                detail = f"{detail} · Compromiso {note.promise_date}"
            _timeline_event(
                events,
                note.created_at,
                "finanzas",
                "Cobranza",
                note.note_type.replace("_", " ").title(),
                detail,
            )

    # Comentarios internos forman parte del expediente, no se duplican manualmente.
    comments = (
        ClientComment.query
        .filter_by(client_id=client.id)
        .order_by(ClientComment.created_at.desc())
        .all()
    )
    for comment in comments:
        detail = comment.process_detail or comment.department_snapshot or ""
        _timeline_event(
            events,
            comment.created_at,
            "interno",
            "Comentario interno",
            f"{comment.user.name} · {comment.process_type}",
            f"{detail} — {comment.body}" if detail else comment.body,
            actor=comment.user.name if comment.user else None,
        )

    # Desarrollo / proyectos / tareas.
    project_ids = {row.id for row in visible_projects}
    task_ids = {row.id for row in visible_tasks}

    if can_projects:
        for project in visible_projects:
            body = (
                f"Estado: {project.status.replace('_', ' ').title()} · "
                f"Progreso: {project.progress or 0}%"
            )
            _timeline_event(
                events,
                project.created_at,
                "desarrollo",
                "Proyecto",
                project.name,
                body,
                link=url_for("operations.project_detail", project_id=project.id),
            )
        if project_ids:
            for change in (
                ChangeRequest.query
                .filter(ChangeRequest.project_id.in_(project_ids))
                .order_by(ChangeRequest.created_at.desc())
                .all()
            ):
                _timeline_event(
                    events,
                    change.created_at,
                    "desarrollo",
                    "Solicitud de cambio",
                    change.title,
                    (
                        f"{change.scope_class.replace('_', ' ').title()} · "
                        f"{change.status.replace('_', ' ').title()}"
                        + (f" · {change.description}" if change.description else "")
                    ),
                    link=url_for("operations.project_detail", project_id=change.project_id),
                )

    if can_tasks:
        for task in visible_tasks:
            _timeline_event(
                events,
                task.created_at,
                "desarrollo",
                "Tarea",
                task.title,
                f"Estado: {task.status.replace('_', ' ').title()} · Prioridad: {task.priority.title()}",
                link=url_for("operations.task_detail", task_id=task.id),
            )
        if task_ids:
            for comment in (
                TaskComment.query
                .filter(TaskComment.task_id.in_(task_ids))
                .order_by(TaskComment.created_at.desc())
                .all()
            ):
                _timeline_event(
                    events,
                    comment.created_at,
                    "desarrollo",
                    "Comentario de tarea",
                    comment.task.title if comment.task else "Tarea",
                    comment.body,
                    link=url_for("operations.task_detail", task_id=comment.task_id),
                    actor=comment.user.name if comment.user else None,
                )

    # Soporte.
    tickets = []
    if can_support:
        tickets = list(client.tickets)
        for ticket in tickets:
            _timeline_event(
                events,
                ticket.created_at,
                "soporte",
                "Ticket",
                f"{ticket.ticket_no} · {ticket.subject}",
                (
                    f"Estado: {ticket.status.replace('_', ' ').title()} · "
                    f"Prioridad: {ticket.priority.title()}"
                ),
                link=url_for("support.detail", ticket_id=ticket.id),
            )
        ticket_ids = {row.id for row in tickets}
        if ticket_ids:
            for comment in (
                TicketComment.query
                .filter(TicketComment.ticket_id.in_(ticket_ids))
                .order_by(TicketComment.created_at.desc())
                .all()
            ):
                _timeline_event(
                    events,
                    comment.created_at,
                    "soporte",
                    "Comentario de soporte",
                    comment.ticket.subject if comment.ticket else "Ticket",
                    comment.body,
                    link=url_for("support.detail", ticket_id=comment.ticket_id),
                    actor=comment.user.name if comment.user else None,
                )

    # Imprenta / evidencias / envíos.
    orders = []
    print_items = []
    shipments = []
    incidents = []
    if can_printing:
        orders = list(client.print_orders)
        order_ids = {row.id for row in orders}

        for order in orders:
            _timeline_event(
                events,
                order.created_at,
                "imprenta",
                "Orden de imprenta",
                order.order_no,
                f"Estado: {order.status.replace('_', ' ').title()}",
                link=url_for("printing.detail", order_id=order.id),
            )

        if order_ids:
            print_items = (
                PrintItem.query
                .filter(PrintItem.order_id.in_(order_ids))
                .all()
            )
            item_ids = {row.id for row in print_items}

            if item_ids:
                for version in (
                    DesignVersion.query
                    .filter(DesignVersion.print_item_id.in_(item_ids))
                    .order_by(DesignVersion.created_at.desc())
                    .all()
                ):
                    _timeline_event(
                        events,
                        version.created_at,
                        "imprenta",
                        "Diseño cargado",
                        f"{version.print_item.name} · v{version.version}",
                        (
                            f"Estado: {version.status.replace('_', ' ').title()}"
                            + (f" · {version.comment}" if version.comment else "")
                        ),
                        link=url_for("printing.detail", order_id=version.print_item.order_id),
                    )

            shipments = (
                Shipment.query
                .filter(Shipment.order_id.in_(order_ids))
                .all()
            )
            for shipment in shipments:
                order = shipment.order
                title = order.order_no if order else "Envío"
                if shipment.shipped_at:
                    _timeline_event(
                        events,
                        shipment.shipped_at,
                        "imprenta",
                        "Envío",
                        title,
                        (
                            f"{shipment.carrier or 'Entrega local'}"
                            + (f" · Tracking {shipment.tracking_number}" if shipment.tracking_number else "")
                            + f" · {shipment.status.replace('_', ' ').title()}"
                        ),
                        link=url_for("printing.detail", order_id=shipment.order_id),
                    )
                if shipment.received_at:
                    _timeline_event(
                        events,
                        shipment.received_at,
                        "imprenta",
                        "Recepción",
                        title,
                        "Envío marcado como recibido.",
                        link=url_for("printing.detail", order_id=shipment.order_id),
                    )

            incidents = (
                PrintIncident.query
                .filter(PrintIncident.order_id.in_(order_ids))
                .all()
            )
            for incident in incidents:
                _timeline_event(
                    events,
                    incident.created_at,
                    "imprenta",
                    "Incidencia de imprenta",
                    incident.incident_type.replace("_", " ").title(),
                    incident.description,
                    link=url_for("printing.detail", order_id=incident.order_id),
                )
                if incident.status == "resuelta" and incident.updated_at:
                    _timeline_event(
                        events,
                        incident.updated_at,
                        "imprenta",
                        "Incidencia resuelta",
                        incident.incident_type.replace("_", " ").title(),
                        incident.resolution or "Incidencia resuelta.",
                        link=url_for("printing.detail", order_id=incident.order_id),
                    )

    # Archivos del expediente.
    for attachment in attachments:
        _timeline_event(
            events,
            attachment.created_at,
            "archivos",
            "Archivo adjunto",
            attachment.file_name,
            "Documento agregado al expediente del cliente.",
            link=url_for(
                "clients.download_attachment",
                client_id=client.id,
                attachment_id=attachment.id,
            ),
        )

    # Renovaciones.
    if can_sales:
        for renewal in client.renewals:
            _timeline_event(
                events,
                renewal.created_at,
                "renovaciones",
                "Renovación programada",
                renewal.renewal_type,
                (
                    f"Vence: {renewal.due_date} · "
                    f"Estado: {renewal.status.replace('_', ' ').title()}"
                ),
            )
            if renewal.last_contact_at:
                _timeline_event(
                    events,
                    renewal.last_contact_at,
                    "renovaciones",
                    "Contacto de renovación",
                    renewal.renewal_type,
                    renewal.notes or "Seguimiento de renovación.",
                )

    # Cambios de responsable comercial.
    for row in client.ownership_history:
        previous = (
            db.session.get(Collaborator, row.previous_owner_id)
            if row.previous_owner_id
            else None
        )
        new_owner = (
            db.session.get(Collaborator, row.new_owner_id)
            if row.new_owner_id
            else None
        )
        previous_name = previous.user.name if previous and previous.user else "Sin asignar"
        new_name = new_owner.user.name if new_owner and new_owner.user else "Sin asignar"
        _timeline_event(
            events,
            row.changed_at,
            "interno",
            "Cambio de responsable",
            f"{previous_name} → {new_name}",
            row.reason or "Transferencia de cartera.",
        )

    # Cambios de estado históricos tomados de AuditLog.
    audit_scopes = {
        "Project": project_ids if can_projects else set(),
        "Task": task_ids if can_tasks else set(),
        "SupportTicket": {row.id for row in tickets},
        "PrintOrder": {row.id for row in orders},
        "PrintItem": {row.id for row in print_items},
        "Shipment": {row.id for row in shipments},
        "PrintIncident": {row.id for row in incidents},
    }
    audit_kind = {
        "Project": ("desarrollo", "Cambio de proyecto"),
        "Task": ("desarrollo", "Cambio de tarea"),
        "SupportTicket": ("soporte", "Cambio de ticket"),
        "PrintOrder": ("imprenta", "Cambio de imprenta"),
        "PrintItem": ("imprenta", "Cambio de diseño"),
        "Shipment": ("imprenta", "Cambio de envío"),
        "PrintIncident": ("imprenta", "Cambio de incidencia"),
    }
    skipped_actions = {
        "crear_proyecto",
        "crear_tarea",
        "crear_ticket",
        "crear_orden_imprenta",
        "comentario_tarea",
        "comentario_ticket",
        "cargar_diseno",
        "producto_enviado",
        "producto_recibido",
        "incidencia_imprenta",
        "resolver_incidencia_imprenta",
    }

    for entity, ids in audit_scopes.items():
        if not ids:
            continue
        logs = (
            AuditLog.query
            .filter(
                AuditLog.entity == entity,
                AuditLog.entity_id.in_([str(value) for value in ids]),
            )
            .order_by(AuditLog.created_at.desc())
            .all()
        )
        category, kind = audit_kind[entity]
        for log in logs:
            if log.action in skipped_actions:
                continue
            _timeline_event(
                events,
                log.created_at,
                category,
                kind,
                log.action.replace("_", " ").title(),
                _audit_change_text(log),
                actor=log.user.name if log.user else None,
            )

    # Ediciones importantes de la ficha, sin exponer los valores modificados.
    for log in (
        AuditLog.query
        .filter(
            AuditLog.entity == "Client",
            AuditLog.entity_id == str(client.id),
            AuditLog.action.in_([
                "editar_cliente",
                "actualizar_perfil_operativo",
                "actualizar_plataforma",
            ]),
        )
        .order_by(AuditLog.created_at.desc())
        .all()
    ):
        _timeline_event(
            events,
            log.created_at,
            "interno",
            "Ficha actualizada",
            log.action.replace("_", " ").title(),
            "Información del expediente actualizada.",
            actor=log.user.name if log.user else None,
        )

    events.sort(key=lambda item: item["dt"], reverse=True)
    return events

def _client_has_history(client_id):
    checks = [
        ("interacciones", Interaction.query.filter_by(client_id=client_id).first()),
        ("comentarios", ClientComment.query.filter_by(client_id=client_id).first()),
        ("asignaciones", ClientCollaborator.query.filter_by(client_id=client_id).first()),
        ("contratos", ClientContract.query.filter_by(client_id=client_id).first()),
        ("cotizaciones", Quote.query.filter_by(client_id=client_id).first()),
        ("ventas", Sale.query.filter_by(client_id=client_id).first()),
        ("pagos", Payment.query.filter_by(client_id=client_id).first()),
        ("cuentas por cobrar", AccountReceivable.query.filter_by(client_id=client_id).first()),
        ("proyectos", Project.query.filter_by(client_id=client_id).first()),
        ("tareas", Task.query.filter_by(client_id=client_id).first()),
        ("órdenes de imprenta", PrintOrder.query.filter_by(client_id=client_id).first()),
        ("renovaciones", Renewal.query.filter_by(client_id=client_id).first()),
        ("tickets", SupportTicket.query.filter_by(client_id=client_id).first()),
        ("archivos", Attachment.query.filter_by(entity_type="Client", entity_id=client_id).first()),
        ("historial de responsable", OwnershipHistory.query.filter_by(client_id=client_id).first()),
        ("perfil operativo", ClientOperationalProfile.query.filter_by(client_id=client_id).first()),
        ("plataformas", ClientPlatform.query.filter_by(client_id=client_id).first()),
    ]
    return [label for label, row in checks if row is not None]


@bp.route("/")
@login_required
def index():
    q = _visible_client_query(Client.query).filter(Client.record_type == "cliente")
    search = request.args.get("q", "").strip()
    status = request.args.get("status", "").strip()
    advisor_project_id = request.args.get("advisor_project_id", type=int)

    if search:
        like = f"%{search}%"
        q = q.filter(or_(
            Client.business_name.ilike(like),
            Client.contact_name.ilike(like),
            Client.phone.ilike(like),
            Client.email.ilike(like),
            Client.code.ilike(like),
        ))

    if status:
        q = q.filter_by(client_status=status)
    else:
        q = q.filter(Client.client_status != "archivado")

    if advisor_project_id:
        q = q.join(Collaborator, Client.owner_id == Collaborator.id).filter(
            Collaborator.advisor_project_id == advisor_project_id
        )

    clients = q.order_by(Client.updated_at.desc()).all()
    advisor_projects = AdvisorProject.query.filter_by(active=True).order_by(AdvisorProject.name).all()
    pending_followups = 0
    if current_user.has_permission("crm.view"):
        pending_followups = _visible_client_query(Client.query).filter_by(record_type="seguimiento").count()

    return render_template(
        "clients/index.html",
        clients=clients,
        search=search,
        status=status,
        advisor_project_id=advisor_project_id,
        advisor_projects=advisor_projects,
        pending_followups=pending_followups,
    )


@bp.route("/new", methods=["GET", "POST"])
@login_required
@permission_required("clients.create")
def new_client():
    flash(
        "En Impacto Nexora todo registro nuevo inicia como Seguimiento. "
        "Pasará a Cliente automáticamente cuando confirme su primera compra.",
        "info",
    )
    return redirect(url_for("crm.new_prospect"))

@bp.route("/<int:client_id>")
@login_required
def detail(client_id):
    client = db.get_or_404(Client, client_id)
    if client.record_type != "cliente":
        return redirect(url_for("crm.detail", client_id=client.id))

    collaborators = []
    if current_user.has_permission("clients.assign"):
        collaborators = (
            Collaborator.query
            .filter_by(status="activo")
            .order_by(Collaborator.job_title)
            .all()
        )

    products = []
    if current_user.has_permission("clients.edit"):
        products = (
            ProductService.query
            .filter_by(active=True)
            .order_by(ProductService.name)
            .all()
        )

    can_financial = (
        current_user.has_permission("sales.view")
        or current_user.has_permission("finance.view")
    )

    attachments = (
        Attachment.query
        .filter_by(entity_type="Client", entity_id=client.id)
        .order_by(Attachment.created_at.desc())
        .all()
    )
    comments = (
        ClientComment.query
        .filter_by(client_id=client.id)
        .order_by(ClientComment.created_at.desc())
        .all()
    )
    operational_profile = (
        ClientOperationalProfile.query
        .filter_by(client_id=client.id)
        .first()
    )
    platforms = (
        ClientPlatform.query
        .filter_by(client_id=client.id)
        .order_by(ClientPlatform.label)
        .all()
    )

    upcoming_renewals = sorted(
        [
            row
            for row in client.renewals
            if row.status not in {"renovado", "cancelado"}
        ],
        key=lambda row: row.due_date or date.max,
    )[:8]

    visible_projects = []
    if current_user.has_permission("projects.view"):
        visible_projects = [
            row for row in client.projects
            if _project_allowed(row)
        ]

    visible_tasks = []
    if current_user.has_permission("tasks.view"):
        visible_tasks = [
            row for row in client.tasks
            if _task_allowed(row)
        ]

    open_tasks = sorted(
        [
            row
            for row in visible_tasks
            if row.status not in {"completada", "cancelada"}
        ],
        key=lambda row: row.due_at or datetime.max,
    )[:10]

    open_tickets = (
        [
            row
            for row in client.tickets
            if row.status not in {"resuelto", "cerrado"}
        ]
        if current_user.has_permission("support.view")
        else []
    )

    timeline = _build_client_timeline(
        client,
        attachments=attachments,
        visible_projects=visible_projects,
        visible_tasks=visible_tasks,
        can_financial=can_financial,
    )

    principal_contract = active_principal_contract(client)
    total_invested = sum(
        (
            Decimal(str(row.amount or 0))
            for row in client.payments
            if row.status == "confirmado"
        ),
        Decimal("0"),
    )
    additional_purchase_count = sum(
        1
        for sale in client.sales
        if sale.operation_meta
        and sale.operation_meta.operation_type == "additional_purchase"
    )

    return render_template(
        "clients/detail.html",
        client=client,
        collaborators=collaborators,
        products=products,
        timeline=timeline,
        timeline_count=len(timeline),
        attachments=attachments,
        comments=comments,
        operational_profile=operational_profile,
        platforms=platforms,
        platform_definitions=PLATFORM_DEFINITIONS,
        upcoming_renewals=upcoming_renewals,
        visible_projects=visible_projects,
        open_tasks=open_tasks,
        open_tickets=open_tickets,
        principal_contract=principal_contract,
        total_invested=total_invested,
        additional_purchase_count=additional_purchase_count,
        can_financial=can_financial,
        today=date.today(),
    )


@bp.route("/<int:client_id>/edit", methods=["GET", "POST"])
@login_required
@permission_required("clients.edit")
def edit(client_id):
    client = db.get_or_404(Client, client_id)
    if client.record_type != "cliente":
        return redirect(url_for("crm.detail", client_id=client.id))
    collaborators = _active_collaborators()
    operational_profile = ClientOperationalProfile.query.filter_by(
        client_id=client.id
    ).first()

    if request.method == "POST":
        business_name = request.form.get("business_name", "").strip()
        contact_name = request.form.get("contact_name", "").strip()
        phone = request.form.get("phone", "").strip()
        email = request.form.get("email", "").strip().lower()

        if not business_name or not contact_name:
            flash("Negocio y contacto son obligatorios.", "danger")
            return render_template(
                "clients/form.html",
                client=client,
                collaborators=collaborators,
                operational_profile=operational_profile,
                mode="edit",
            )

        duplicate = _find_duplicate(business_name, phone, email, exclude_id=client.id)
        if duplicate:
            flash(f"Existe otro registro parecido: {duplicate.business_name}.", "warning")
            return render_template(
                "clients/form.html",
                client=client,
                collaborators=collaborators,
                operational_profile=operational_profile,
                mode="edit",
            )

        before = {
            "business_name": client.business_name,
            "contact_name": client.contact_name,
            "status": client.client_status,
            "owner_id": client.owner_id,
        }
        _apply_client_form(client)
        operational_profile = _apply_operational_profile_form(
            client,
            operational_profile,
        )
        audit(
            "editar_cliente",
            "Client",
            client.id,
            before=before,
            after={
                "business_name": client.business_name,
                "contact_name": client.contact_name,
                "status": client.client_status,
                "owner_id": client.owner_id,
                "operational_profile_updated": True,
            },
        )
        db.session.commit()
        flash("Ficha actualizada.", "success")
        return redirect(url_for("clients.detail", client_id=client.id))

    return render_template(
        "clients/form.html",
        client=client,
        collaborators=collaborators,
        operational_profile=operational_profile,
        mode="edit",
    )


@bp.route("/<int:client_id>/convert", methods=["POST"])
@login_required
@permission_required("clients.edit")
def convert_to_client(client_id):
    client = db.get_or_404(Client, client_id)
    if client.record_type == "cliente":
        flash("Este registro ya es un cliente.", "info")
        return redirect(url_for("clients.detail", client_id=client.id))

    confirmed_sale = (
        Sale.query
        .filter_by(client_id=client.id)
        .filter(Sale.status == "confirmada")
        .first()
    )
    if not confirmed_sale:
        flash(
            "Un Seguimiento pasa a Cliente únicamente cuando confirma una compra. "
            "Registra la compra para continuar.",
            "info",
        )
        return redirect(url_for("sales.new_sale", client_id=client.id))

    before = {"record_type": client.record_type, "pipeline_stage": client.pipeline_stage}
    client.record_type = "cliente"
    client.pipeline_stage = "venta_cerrada"
    client.client_status = "activo"
    client.country = "USA"
    if not client.code:
        client.code = next_code("CLI", Client)
    audit(
        "convertir_registro_cliente",
        "Client",
        client.id,
        before=before,
        after={"record_type": client.record_type, "pipeline_stage": client.pipeline_stage},
    )
    db.session.commit()
    flash("Seguimiento convertido en cliente por compra confirmada.", "success")
    return redirect(url_for("clients.detail", client_id=client.id))

@bp.route("/<int:client_id>/archive", methods=["POST"])
@login_required
@permission_required("clients.edit")
def archive(client_id):
    client = db.get_or_404(Client, client_id)
    before = client.client_status
    client.client_status = "archivado"
    audit("archivar_cliente", "Client", client.id, before={"status": before}, after={"status": "archivado"})
    db.session.commit()
    flash("Cliente archivado. Su historial se conserva.", "success")
    return redirect(url_for("clients.index"))


@bp.route("/<int:client_id>/restore", methods=["POST"])
@login_required
@permission_required("clients.edit")
def restore(client_id):
    client = db.get_or_404(Client, client_id)
    before = client.client_status
    client.client_status = "activo"
    client.record_type = "cliente"
    audit("restaurar_cliente", "Client", client.id, before={"status": before}, after={"status": "activo"})
    db.session.commit()
    flash("Cliente restaurado.", "success")
    return redirect(url_for("clients.detail", client_id=client.id))


@bp.route("/<int:client_id>/delete", methods=["POST"])
@login_required
@roles_required("superadmin", "admin")
def delete_client(client_id):
    client = db.get_or_404(Client, client_id)
    history = _client_has_history(client.id)

    if history:
        client.client_status = "archivado"
        audit(
            "intento_eliminar_cliente_con_historial",
            "Client",
            client.id,
            after={"archived": True, "related": history},
        )
        db.session.commit()
        flash(
            "El cliente tiene historial relacionado y no puede eliminarse definitivamente. "
            "Se archivó para proteger ventas, pagos y trazabilidad.",
            "warning",
        )
        return redirect(url_for("clients.index"))

    snapshot = {
        "business_name": client.business_name,
        "contact_name": client.contact_name,
        "phone": client.phone,
        "email": client.email,
    }
    client_id_value = client.id
    db.session.delete(client)
    audit("eliminar_cliente", "Client", client_id_value, before=snapshot, reason="Eliminación definitiva sin historial relacionado")
    db.session.commit()
    flash("Cliente eliminado definitivamente.", "success")
    return redirect(url_for("clients.index"))


@bp.route("/<int:client_id>/assign", methods=["POST"])
@login_required
def assign_collaborator(client_id):
    client = db.get_or_404(Client, client_id)
    collaborator_id = request.form.get("collaborator_id", type=int)
    if collaborator_id and not ClientCollaborator.query.filter_by(client_id=client.id, collaborator_id=collaborator_id).first():
        assignment = ClientCollaborator(
            client_id=client.id,
            collaborator_id=collaborator_id,
            role_in_client=request.form.get("role_in_client"),
            primary=bool(request.form.get("primary")),
        )
        db.session.add(assignment)
        audit("asignar_colaborador", "Client", client.id, after={"collaborator_id": collaborator_id})
        db.session.commit()
        flash("Colaborador asignado.", "success")
    return redirect(url_for("clients.detail", client_id=client.id))


@bp.route("/<int:client_id>/assign/<int:assignment_id>/remove", methods=["POST"])
@login_required
def remove_assignment(client_id, assignment_id):
    assignment = db.get_or_404(ClientCollaborator, assignment_id)
    if assignment.client_id == client_id:
        db.session.delete(assignment)
        audit("quitar_colaborador", "Client", client_id, before={"collaborator_id": assignment.collaborator_id})
        db.session.commit()
    return redirect(url_for("clients.detail", client_id=client_id))


@bp.route("/<int:client_id>/contracts", methods=["POST"])
@login_required
def add_contract(client_id):
    client = db.get_or_404(Client, client_id)
    product_id = request.form.get("product_id", type=int)
    product = db.session.get(ProductService, product_id) if product_id else None
    if not product or not product.active:
        flash("Selecciona un producto o paquete activo.", "danger")
        return redirect(url_for("clients.detail", client_id=client.id))
    if product.category == "paquete":
        flash(
            "Los paquetes principales se registran desde Servicio principal / Upgrade para mantener precio, pagos y saldo sincronizados.",
            "info",
        )
        return redirect(url_for("sales.principal_service", client_id=client.id))

    status = request.form.get("status", "activo")
    if status not in CONTRACT_STATUSES:
        status = "activo"

    starts_on = _date_or_none(request.form.get("starts_on")) or date.today()
    ends_on = _date_or_none(request.form.get("ends_on"))
    if ends_on and ends_on < starts_on:
        flash("La fecha de vencimiento no puede ser anterior a la activación.", "danger")
        return redirect(url_for("clients.detail", client_id=client.id))

    agreed_price = _decimal_or_none(request.form.get("agreed_price"))
    if agreed_price is not None and agreed_price < 0:
        flash("El precio acordado no puede ser negativo.", "danger")
        return redirect(url_for("clients.detail", client_id=client.id))

    status_reason = (request.form.get("status_reason") or "").strip() or None
    if status == "inactivo" and not status_reason:
        flash("Indica el motivo cuando un paquete se registra como inactivo.", "danger")
        return redirect(url_for("clients.detail", client_id=client.id))

    principal = bool(request.form.get("principal"))
    if principal:
        ClientContract.query.filter_by(client_id=client.id, principal=True).update({"principal": False})

    contract = ClientContract(
        client_id=client.id,
        product_id=product.id,
        status=status,
        starts_on=starts_on,
        ends_on=ends_on,
        agreed_price=agreed_price,
        principal=principal,
        notes=(request.form.get("notes") or "").strip() or None,
    )
    db.session.add(contract)
    db.session.flush()

    detail = ClientContractDetail(
        contract_id=contract.id,
        modality_snapshot=(request.form.get("modality_snapshot") or product.modality or "").strip() or None,
        maintenance_snapshot=(request.form.get("maintenance_snapshot") or product.maintenance or "").strip() or None,
        benefits_snapshot=(request.form.get("benefits_snapshot") or product.components or "").strip() or None,
        courtesies_snapshot=(request.form.get("courtesies_snapshot") or "").strip() or None,
        status_reason=status_reason,
    )
    db.session.add(detail)

    if ends_on:
        db.session.add(
            Renewal(
                client_id=client.id,
                contract=contract,
                renewal_type=product.name,
                due_date=ends_on,
                status="pendiente",
            )
        )

    audit(
        "asignar_paquete",
        "Client",
        client.id,
        after={"product_id": product.id, "contract_id": contract.id, "status": contract.status},
    )
    db.session.commit()
    flash("Paquete/servicio agregado al historial del cliente.", "success")
    return redirect(url_for("clients.detail", client_id=client.id, _anchor="servicios"))


@bp.route("/<int:client_id>/contracts/<int:contract_id>/update", methods=["POST"])
@login_required
def update_contract(client_id, contract_id):
    client = db.get_or_404(Client, client_id)
    contract = db.get_or_404(ClientContract, contract_id)
    if contract.client_id != client.id:
        return redirect(url_for("clients.detail", client_id=client.id))

    status = request.form.get("status", contract.status)
    if status not in CONTRACT_STATUSES:
        flash("Estado de paquete no válido.", "danger")
        return redirect(url_for("clients.detail", client_id=client.id, _anchor="servicios"))

    starts_on = _date_or_none(request.form.get("starts_on")) or contract.starts_on
    ends_on = _date_or_none(request.form.get("ends_on"))
    if ends_on and ends_on < starts_on:
        flash("La fecha de vencimiento no puede ser anterior a la activación.", "danger")
        return redirect(url_for("clients.detail", client_id=client.id, _anchor="servicios"))

    agreed_price = _decimal_or_none(request.form.get("agreed_price"))
    if agreed_price is not None and agreed_price < 0:
        flash("El precio acordado no puede ser negativo.", "danger")
        return redirect(url_for("clients.detail", client_id=client.id, _anchor="servicios"))
    if contract.product and contract.product.category == "paquete" and contract.product.base_price is not None:
        agreed_price = Decimal(str(contract.product.base_price))

    detail = contract.v2_detail or ClientContractDetail(contract_id=contract.id)
    if not contract.v2_detail:
        db.session.add(detail)

    status_reason = (request.form.get("status_reason") or "").strip() or None
    if status == "inactivo" and not status_reason:
        flash("Indica el motivo de inactivación del paquete.", "danger")
        return redirect(url_for("clients.detail", client_id=client.id, _anchor="servicios"))

    before = {"status": contract.status, "starts_on": str(contract.starts_on), "ends_on": str(contract.ends_on)}
    contract.status = status
    contract.starts_on = starts_on
    contract.ends_on = ends_on
    if agreed_price is not None:
        contract.agreed_price = agreed_price
    contract.notes = (request.form.get("notes") or "").strip() or None

    principal = bool(request.form.get("principal"))
    if principal:
        ClientContract.query.filter(
            ClientContract.client_id == client.id,
            ClientContract.id != contract.id,
            ClientContract.principal.is_(True),
        ).update({"principal": False}, synchronize_session=False)
    contract.principal = principal

    detail.modality_snapshot = (request.form.get("modality_snapshot") or "").strip() or contract.product.modality
    detail.maintenance_snapshot = (request.form.get("maintenance_snapshot") or "").strip() or contract.product.maintenance
    detail.benefits_snapshot = (request.form.get("benefits_snapshot") or "").strip() or None
    detail.courtesies_snapshot = (request.form.get("courtesies_snapshot") or "").strip() or None
    detail.status_reason = status_reason

    renewal = Renewal.query.filter_by(contract_id=contract.id).first()
    if ends_on:
        if not renewal:
            renewal = Renewal(
                client_id=client.id,
                contract=contract,
                renewal_type=contract.product.name,
                due_date=ends_on,
                status="pendiente",
            )
            db.session.add(renewal)
        else:
            renewal.due_date = ends_on
            renewal.renewal_type = contract.product.name
            if renewal.status == "cancelado" and status == "activo":
                renewal.status = "pendiente"
    elif renewal and status in {"inactivo", "caducado"}:
        renewal.status = "cancelado"

    audit(
        "actualizar_paquete_cliente",
        "ClientContract",
        contract.id,
        before=before,
        after={"status": contract.status, "starts_on": str(contract.starts_on), "ends_on": str(contract.ends_on)},
    )
    db.session.commit()
    flash("Paquete actualizado.", "success")
    return redirect(url_for("clients.detail", client_id=client.id, _anchor="servicios"))

@bp.route("/<int:client_id>/comment", methods=["POST"])
@login_required
def add_comment(client_id):
    client = db.get_or_404(Client, client_id)
    body = (request.form.get("body") or "").strip()
    if not body:
        flash("Escribe un comentario antes de guardar.", "warning")
        return redirect(url_for("clients.detail", client_id=client.id))

    collaborator = current_user.collaborator
    department = collaborator.department if collaborator else (current_user.role.label if current_user.role else None)
    advisor_project = collaborator.advisor_project.name if collaborator and collaborator.advisor_project else None
    comment = ClientComment(
        client_id=client.id,
        user_id=current_user.id,
        process_type=(request.form.get("process_type") or "Seguimiento").strip(),
        process_detail=(request.form.get("process_detail") or "").strip() or None,
        department_snapshot=department,
        advisor_project_snapshot=advisor_project,
        body=body,
    )
    db.session.add(comment)
    audit("comentario_cliente", "Client", client.id, after={"process_type": comment.process_type})
    db.session.commit()
    flash("Comentario agregado al historial del cliente.", "success")
    return redirect(url_for("clients.detail", client_id=client.id))


@bp.route("/export.csv")
@login_required
def export_csv():
    clients = (
        _visible_client_query(Client.query)
        .filter_by(record_type="cliente")
        .order_by(Client.id)
        .all()
    )
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "code",
        "business_name",
        "contact_name",
        "phone",
        "other_phones",
        "email",
        "preferred_channel",
        "industry",
        "services_offered",
        "website",
        "facebook",
        "instagram",
        "other_social",
        "address",
        "city",
        "state",
        "country",
        "timezone",
        "source",
        "priority",
        "main_interest",
        "estimated_budget",
        "client_status",
        "advisor",
    ])
    for client in clients:
        writer.writerow([
            client.code,
            client.business_name,
            client.contact_name,
            client.phone,
            client.other_phones,
            client.email,
            client.preferred_channel,
            client.industry,
            client.services_offered,
            client.website,
            client.facebook,
            client.instagram,
            client.other_social,
            client.address,
            client.city,
            client.state,
            client.country,
            client.timezone,
            client.source,
            client.priority,
            client.main_interest,
            client.estimated_budget,
            client.client_status,
            client.owner.user.name if client.owner and client.owner.user else "",
        ])
    audit("exportar", "Client")
    db.session.commit()
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=clientes.csv"},
    )


@bp.route("/<int:client_id>/operational-profile", methods=["POST"])
@login_required
@permission_required("clients.edit")
def update_operational_profile(client_id):
    client = db.get_or_404(Client, client_id)
    _apply_operational_profile_form(client)
    audit("actualizar_perfil_operativo", "Client", client.id)
    db.session.commit()
    flash("Perfil operativo actualizado.", "success")
    return redirect(url_for("clients.detail", client_id=client.id, _anchor="datos"))


@bp.route("/<int:client_id>/platforms", methods=["POST"])
@login_required
def save_platform(client_id):
    client = db.get_or_404(Client, client_id)
    platform_id = request.form.get("platform_id", type=int)

    if platform_id:
        row = db.get_or_404(ClientPlatform, platform_id)
        if row.client_id != client.id:
            flash("La plataforma no pertenece a este cliente.", "danger")
            return redirect(url_for("clients.detail", client_id=client.id, _anchor="plataformas"))
        if row.platform_key.startswith("otro_"):
            label = (request.form.get("custom_label") or row.label).strip()[:120] or row.label
        else:
            label = PLATFORM_DEFINITIONS.get(row.platform_key, row.label)
    else:
        platform_key = (request.form.get("platform_key") or "").strip()
        custom_label = (request.form.get("custom_label") or "").strip()

        if platform_key == "otro":
            if not custom_label:
                flash("Escribe el nombre de la plataforma adicional.", "danger")
                return redirect(url_for("clients.detail", client_id=client.id, _anchor="plataformas"))
            platform_key = f"otro_{_normalize_key(custom_label)[:45]}"
            label = custom_label[:120]
        elif platform_key in PLATFORM_DEFINITIONS:
            label = PLATFORM_DEFINITIONS[platform_key]
        else:
            flash("Plataforma no válida.", "danger")
            return redirect(url_for("clients.detail", client_id=client.id, _anchor="plataformas"))

        row = ClientPlatform.query.filter_by(client_id=client.id, platform_key=platform_key).first()
        if not row:
            row = ClientPlatform(client_id=client.id, platform_key=platform_key, label=label)
            db.session.add(row)

    status = request.form.get("status", "activo")
    if status not in PLATFORM_STATUSES:
        status = "activo"

    row.label = label
    row.url = _clean_url(request.form.get("url"))
    row.status = status
    row.notes = (request.form.get("notes") or "").strip() or None

    audit("guardar_plataforma_cliente", "Client", client.id, after={"platform": row.label, "status": row.status})
    db.session.commit()
    flash("Plataforma guardada.", "success")
    return redirect(url_for("clients.detail", client_id=client.id, _anchor="plataformas"))

@bp.route("/<int:client_id>/platforms/<int:platform_id>/delete", methods=["POST"])
@login_required
def delete_platform(client_id, platform_id):
    client = db.get_or_404(Client, client_id)
    row = db.get_or_404(ClientPlatform, platform_id)
    if row.client_id != client.id:
        flash("La plataforma no pertenece a este cliente.", "danger")
        return redirect(url_for("clients.detail", client_id=client.id, _anchor="plataformas"))
    audit("eliminar_plataforma_cliente", "Client", client.id, before={"platform": row.label})
    db.session.delete(row)
    db.session.commit()
    flash("Plataforma eliminada.", "success")
    return redirect(url_for("clients.detail", client_id=client.id, _anchor="plataformas"))


@bp.route("/import", methods=["GET", "POST"])
@login_required
@permission_required("clients.create")
def import_csv():
    if request.method == "POST":
        uploaded = request.files.get("file")
        if not uploaded:
            flash("Selecciona un CSV.", "danger")
            return redirect(url_for("clients.import_csv"))

        try:
            content = uploaded.read().decode("utf-8-sig")
        except UnicodeDecodeError:
            uploaded.stream.seek(0)
            content = uploaded.read().decode("latin-1")

        reader = csv.DictReader(io.StringIO(content))
        if not reader.fieldnames:
            flash("El archivo CSV no contiene encabezados válidos.", "danger")
            return redirect(url_for("clients.import_csv"))

        collaborators = _active_collaborators()
        owner_aliases = _owner_aliases(collaborators)

        created = 0
        updated = 0
        rejected = []

        for line_number, original_row in enumerate(reader, start=2):
            row = _normalized_row(original_row)

            business = _pick(row, "business_name", "negocio", "empresa", "compania", "compañia", "nombre_de_la_empresa")
            contact = _pick(row, "contact_name", "contacto", "nombre_contacto", "nombre_del_contacto") or business
            phone = _pick(row, "phone", "telefono", "contacto_ppal", "contacto_principal", "telefono_principal")
            other_phones = _pick(row, "other_phones", "telefono_secundario", "numero_secundario", "secundario")
            email = _pick(row, "email", "correo", "correo_electronico").lower()

            if not business:
                rejected.append((line_number, "Falta business_name / empresa / compañía"))
                continue

            advisor_name = _pick(row, "advisor", "asesor", "responsable", "owner")
            owner_id = owner_aliases.get(_normalize_text(advisor_name)) if advisor_name else None
            if not current_user.has_permission("crm.transfer") and current_user.collaborator:
                owner_id = current_user.collaborator.id

            values = {
                "business_name": business,
                "contact_name": contact,
                "phone": phone,
                "other_phones": other_phones,
                "email": email,
                "preferred_channel": _pick(row, "preferred_channel", "canal_preferido"),
                "industry": _pick(row, "industry", "industria", "rubro"),
                "services_offered": _pick(row, "services_offered", "servicios", "servicios_a_exponer"),
                "website": _pick(row, "website", "sitio_web", "web", "dominio"),
                "facebook": _pick(row, "facebook"),
                "instagram": _pick(row, "instagram"),
                "other_social": _pick(row, "other_social", "otras_redes", "otros_enlaces"),
                "address": _pick(row, "address", "direccion"),
                "city": _pick(row, "city", "ciudad"),
                "state": _pick(row, "state", "estado", "provincia"),
                "country": _pick(row, "country", "pais"),
                "timezone": _pick(row, "timezone", "zona_horaria"),
                "source": _pick(row, "source", "fuente"),
                "priority": _pick(row, "priority", "prioridad") or "media",
                "main_interest": _pick(row, "main_interest", "interes_principal", "interes"),
                "estimated_budget": _decimal_or_none(_pick(row, "estimated_budget", "presupuesto_estimado", "presupuesto")),
                "notes": _pick(row, "notes", "notas", "observaciones"),
            }

            status = _normalize_text(_pick(row, "client_status", "estado_cliente", "status")) or "activo"
            if status not in CLIENT_STATUSES:
                status = "activo"

            duplicate = _find_duplicate(business, phone, email)
            if duplicate:
                role = current_user.role.name if current_user.role else ""
                allowed_owner_ids = {c.id for c in _active_collaborators()}
                if role in {"advisor", "supervisor"} and duplicate.owner_id not in allowed_owner_ids:
                    rejected.append((line_number, "El registro ya existe fuera de tu cartera y no puede modificarse desde esta importación"))
                    continue
                before = {"record_type": duplicate.record_type, "business_name": duplicate.business_name}
                for field, value in values.items():
                    if value not in (None, ""):
                        setattr(duplicate, field, value)
                if owner_id:
                    duplicate.owner_id = owner_id
                duplicate.record_type = "cliente"
                duplicate.pipeline_stage = "venta_cerrada"
                duplicate.client_status = status
                if not duplicate.code:
                    duplicate.code = next_code("CLI", Client)
                audit(
                    "actualizar_cliente_importacion",
                    "Client",
                    duplicate.id,
                    before=before,
                    after={"record_type": "cliente", "business_name": duplicate.business_name},
                )
                updated += 1
                continue

            client = Client(
                code=next_code("CLI", Client),
                record_type="cliente",
                pipeline_stage="venta_cerrada",
                client_status=status,
                owner_id=owner_id,
                **values,
            )
            db.session.add(client)
            db.session.flush()
            created += 1

        audit(
            "importar_clientes",
            "Client",
            after={"created": created, "updated": updated, "rejected": len(rejected)},
        )
        db.session.commit()

        flash(
            f"Importación finalizada: {created} creados, {updated} actualizados/convertidos y {len(rejected)} rechazados.",
            "success" if not rejected else "warning",
        )
        return render_template(
            "clients/import.html",
            rejected=rejected,
            created=created,
            updated=updated,
        )

    return render_template("clients/import.html", rejected=None, created=0, updated=0)


@bp.route("/<int:client_id>/attachment", methods=["POST"])
@login_required
def upload_attachment(client_id):
    client = db.get_or_404(Client, client_id)
    uploaded = request.files.get("file")
    if not uploaded:
        flash("Selecciona un archivo.", "danger")
        return redirect(url_for("clients.detail", client_id=client.id) + "#archivos")

    category = (request.form.get("category") or "otros").strip()
    if category not in DOCUMENT_CATEGORIES:
        category = "otros"
    description = (request.form.get("description") or "").strip()[:255] or None

    try:
        path = save_upload(uploaded, prefix=f"client_{client.id}")
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("clients.detail", client_id=client.id) + "#archivos")

    attachment = Attachment(
        entity_type="Client",
        entity_id=client.id,
        file_name=uploaded.filename,
        file_path=path,
        uploaded_by_id=current_user.id,
    )
    db.session.add(attachment)
    db.session.flush()
    db.session.add(
        ClientDocumentMeta(
            client_id=client.id,
            attachment_id=attachment.id,
            category=category,
            description=description,
        )
    )
    audit(
        "subir_archivo_cliente",
        "Client",
        client.id,
        after={"file": uploaded.filename, "category": category},
    )
    db.session.commit()
    flash("Archivo agregado y categorizado en la ficha.", "success")
    return redirect(url_for("clients.detail", client_id=client.id) + "#archivos")


@bp.route("/<int:client_id>/attachments/<int:attachment_id>/download")
@login_required
def download_attachment(client_id, attachment_id):
    client = db.get_or_404(Client, client_id)
    if not current_user.has_permission("clients.view") or not _commercial_client_allowed(client):
        abort(403)

    attachment = db.get_or_404(Attachment, attachment_id)
    if attachment.entity_type != "Client" or attachment.entity_id != client.id:
        abort(404)

    filename = Path(attachment.file_path or "").name
    if not filename:
        abort(404)
    return send_from_directory(
        current_app.config["UPLOAD_FOLDER"],
        filename,
        as_attachment=False,
        download_name=attachment.file_name,
    )


@bp.route("/<int:client_id>/attachments/<int:attachment_id>/meta", methods=["POST"])
@login_required
def update_attachment_meta(client_id, attachment_id):
    client = db.get_or_404(Client, client_id)
    attachment = db.get_or_404(Attachment, attachment_id)
    if attachment.entity_type != "Client" or attachment.entity_id != client.id:
        abort(404)

    category = (request.form.get("category") or "otros").strip()
    if category not in DOCUMENT_CATEGORIES:
        category = "otros"
    description = (request.form.get("description") or "").strip()[:255] or None

    meta = ClientDocumentMeta.query.filter_by(attachment_id=attachment.id).first()
    if not meta:
        meta = ClientDocumentMeta(client_id=client.id, attachment_id=attachment.id)
        db.session.add(meta)
    meta.category = category
    meta.description = description
    audit(
        "editar_metadatos_archivo_cliente",
        "Attachment",
        attachment.id,
        after={"category": category, "description": description},
    )
    db.session.commit()
    flash("Datos del archivo actualizados.", "success")
    return redirect(url_for("clients.detail", client_id=client.id) + "#archivos")


@bp.route("/<int:client_id>/attachments/<int:attachment_id>/delete", methods=["POST"])
@login_required
def delete_attachment(client_id, attachment_id):
    client = db.get_or_404(Client, client_id)
    attachment = db.get_or_404(Attachment, attachment_id)
    if attachment.entity_type != "Client" or attachment.entity_id != client.id:
        abort(404)

    disk_path = Path(current_app.config["UPLOAD_FOLDER"]) / Path(attachment.file_path or "").name
    if disk_path.is_file():
        try:
            disk_path.unlink()
        except OSError:
            current_app.logger.warning("No se pudo eliminar el archivo físico %s", disk_path)

    audit("eliminar_archivo_cliente", "Attachment", attachment.id, before={"file": attachment.file_name})
    db.session.delete(attachment)
    db.session.commit()
    flash("Archivo eliminado de la ficha.", "warning")
    return redirect(url_for("clients.detail", client_id=client.id) + "#archivos")


def _sale_for_client_or_404(client, sale_id):
    sale = db.get_or_404(Sale, sale_id)
    if sale.client_id != client.id:
        abort(404)
    return sale


@bp.route("/<int:client_id>/installments", methods=["POST"])
@login_required
def add_installment(client_id):
    client = db.get_or_404(Client, client_id)
    sale_id = request.form.get("sale_id", type=int)
    if not sale_id:
        flash("Selecciona una venta para registrar la cuota.", "danger")
        return redirect(url_for("clients.detail", client_id=client.id) + "#cobranza")
    sale = _sale_for_client_or_404(client, sale_id)

    amount = _decimal_or_none(request.form.get("amount"))
    due_date = _date_or_none(request.form.get("due_date"))
    if amount is None or amount <= 0 or not due_date:
        flash("La cuota requiere monto mayor que cero y fecha de vencimiento válida.", "danger")
        return redirect(url_for("clients.detail", client_id=client.id) + "#cobranza")

    recalc_sale(sale)
    scheduled_outstanding = sum(
        (Decimal(str(row.amount or 0)) - Decimal(str(row.paid_amount or 0)) for row in sale.installments),
        Decimal("0"),
    )
    remaining_to_schedule = max(Decimal("0"), Decimal(str(sale.balance or 0)) - scheduled_outstanding)
    if amount > remaining_to_schedule:
        flash(
            f"La cuota excede el saldo aún no programado de {sale.currency} {remaining_to_schedule:,.2f}.",
            "danger",
        )
        return redirect(url_for("clients.detail", client_id=client.id) + "#cobranza")

    sequence = max([row.sequence or 0 for row in sale.installments] or [0]) + 1
    baseline_paid = (
        Decimal(str(sale.installments[0].base_paid_amount or 0))
        if sale.installments
        else Decimal(str(sale.amount_paid or 0))
    )
    row = ClientInstallment(
        client_id=client.id,
        sale_id=sale.id,
        sequence=sequence,
        amount=amount,
        paid_amount=0,
        base_paid_amount=baseline_paid,
        due_date=due_date,
        status="vencida" if due_date < date.today() else "pendiente",
        notes=(request.form.get("notes") or "").strip() or None,
        created_by_id=current_user.id,
    )
    db.session.add(row)
    db.session.flush()
    recalc_sale(sale)
    audit(
        "crear_cuota_cliente",
        "ClientInstallment",
        row.id,
        after={"sale_id": sale.id, "amount": str(amount), "due_date": str(due_date)},
    )
    db.session.commit()
    flash("Cuota agregada al plan de pago.", "success")
    return redirect(url_for("clients.detail", client_id=client.id) + "#cobranza")


@bp.route("/<int:client_id>/installments/<int:installment_id>/delete", methods=["POST"])
@login_required
def delete_installment(client_id, installment_id):
    client = db.get_or_404(Client, client_id)
    row = db.get_or_404(ClientInstallment, installment_id)
    if row.client_id != client.id:
        abort(404)
    if Decimal(str(row.paid_amount or 0)) > 0:
        flash("No se puede eliminar una cuota que ya tiene pagos aplicados.", "danger")
        return redirect(url_for("clients.detail", client_id=client.id) + "#cobranza")

    sale = row.sale
    audit("eliminar_cuota_cliente", "ClientInstallment", row.id, before={"amount": str(row.amount)})
    db.session.delete(row)
    db.session.flush()
    recalc_sale(sale)
    db.session.commit()
    flash("Cuota eliminada del plan de pago.", "warning")
    return redirect(url_for("clients.detail", client_id=client.id) + "#cobranza")


@bp.route("/<int:client_id>/collection-notes", methods=["POST"])
@login_required
def add_collection_note(client_id):
    client = db.get_or_404(Client, client_id)
    note_type = (request.form.get("note_type") or "cobranza").strip()
    if note_type not in COLLECTION_NOTE_TYPES:
        note_type = "cobranza"
    body = (request.form.get("body") or "").strip()
    if not body:
        flash("La nota de cobranza no puede estar vacía.", "danger")
        return redirect(url_for("clients.detail", client_id=client.id) + "#cobranza")

    sale_id = request.form.get("sale_id", type=int)
    sale = _sale_for_client_or_404(client, sale_id) if sale_id else None
    promised_amount = _decimal_or_none(request.form.get("promised_amount"))
    promise_date = _date_or_none(request.form.get("promise_date"))

    if promised_amount is not None and promised_amount < 0:
        flash("El monto prometido no puede ser negativo.", "danger")
        return redirect(url_for("clients.detail", client_id=client.id) + "#cobranza")
    if note_type == "promesa" and (not sale or not promise_date or promised_amount is None or promised_amount <= 0):
        flash("Una promesa requiere venta, monto prometido y fecha de compromiso.", "danger")
        return redirect(url_for("clients.detail", client_id=client.id) + "#cobranza")
    if sale and promised_amount is not None and promised_amount > Decimal(str(sale.balance or 0)):
        flash("El monto prometido no puede superar el saldo de la venta.", "danger")
        return redirect(url_for("clients.detail", client_id=client.id) + "#cobranza")

    receivable = sale.receivable if sale else None
    row = ClientCollectionNote(
        client_id=client.id,
        sale_id=sale.id if sale else None,
        receivable_id=receivable.id if receivable else None,
        note_type=note_type,
        promised_amount=promised_amount,
        promise_date=promise_date,
        status="abierta",
        body=body,
        created_by_id=current_user.id,
    )
    db.session.add(row)
    if note_type == "promesa" and receivable:
        receivable.promise_date = promise_date
        receivable.status = "promesa_pago"
    if sale:
        recalc_sale(sale)
    db.session.flush()
    audit(
        "nota_cobranza_cliente",
        "ClientCollectionNote",
        row.id,
        after={"type": note_type, "sale_id": sale.id if sale else None, "promise_date": str(promise_date) if promise_date else None},
    )
    db.session.commit()
    flash("Seguimiento de cobranza registrado.", "success")
    return redirect(url_for("clients.detail", client_id=client.id) + "#cobranza")


@bp.route("/<int:client_id>/collection-notes/<int:note_id>/<action>", methods=["POST"])
@login_required
def collection_note_action(client_id, note_id, action):
    client = db.get_or_404(Client, client_id)
    row = db.get_or_404(ClientCollectionNote, note_id)
    if row.client_id != client.id:
        abort(404)
    target = {"complete": "cumplida", "miss": "incumplida", "cancel": "cancelada"}.get(action)
    if not target:
        abort(404)
    row.status = target
    if row.note_type == "promesa" and row.receivable and row.receivable.promise_date == row.promise_date:
        other_open = ClientCollectionNote.query.filter(
            ClientCollectionNote.receivable_id == row.receivable_id,
            ClientCollectionNote.id != row.id,
            ClientCollectionNote.note_type == "promesa",
            ClientCollectionNote.status == "abierta",
        ).order_by(ClientCollectionNote.promise_date.desc()).first()
        row.receivable.promise_date = other_open.promise_date if other_open else None
    if row.sale:
        recalc_sale(row.sale)
    audit("actualizar_nota_cobranza", "ClientCollectionNote", row.id, after={"status": target})
    db.session.commit()
    flash("Estado del seguimiento actualizado.", "success")
    return redirect(url_for("clients.detail", client_id=client.id) + "#cobranza")


@bp.route("/<int:client_id>/statement")
@login_required
def statement(client_id):
    client = db.get_or_404(Client, client_id)
    if not (current_user.has_permission("sales.view") or current_user.has_permission("finance.view")):
        abort(403)
    if not _commercial_client_allowed(client):
        abort(403)

    sales = sorted(client.sales, key=lambda row: (row.sale_date or date.min, row.id), reverse=True)
    payments = sorted(client.payments, key=lambda row: (row.effective_date or date.min, row.id), reverse=True)
    installments = sorted(client.installments, key=lambda row: (row.due_date or date.max, row.sequence or 0))
    notes = sorted(client.collection_notes, key=lambda row: row.created_at or datetime.min, reverse=True)
    return render_template(
        "clients/statement.html",
        client=client,
        sales=sales,
        payments=payments,
        installments=installments,
        collection_notes=notes,
        generated_at=datetime.now(),
    )
