import csv
import io
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation

from flask import Blueprint, render_template, request, redirect, url_for, flash, Response
from flask_login import login_required, current_user
from sqlalchemy import or_

from app.extensions import db
from app.decorators import permission_required, roles_required
from app.helpers import audit, next_code, save_upload
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
)

bp = Blueprint("clients", __name__, url_prefix="/clients")

CLIENT_STATUSES = {"activo", "inactivo", "caducado", "archivado"}


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
    collaborators = _active_collaborators()

    if request.method == "POST":
        business_name = request.form.get("business_name", "").strip()
        contact_name = request.form.get("contact_name", "").strip()
        phone = request.form.get("phone", "").strip()
        email = request.form.get("email", "").strip().lower()

        if not business_name:
            flash("El nombre del negocio es obligatorio.", "danger")
            return render_template("clients/form.html", client=None, collaborators=collaborators, mode="new")
        if not contact_name:
            flash("El nombre del contacto es obligatorio.", "danger")
            return render_template("clients/form.html", client=None, collaborators=collaborators, mode="new")

        duplicate = _find_duplicate(business_name, phone, email)
        if duplicate:
            role = current_user.role.name if current_user.role else ""
            allowed_owner_ids = {c.id for c in _active_collaborators()}
            if role in {"advisor", "supervisor"} and duplicate.owner_id not in allowed_owner_ids:
                flash("Ya existe un registro parecido fuera de tu cartera. No se realizaron cambios.", "warning")
                return redirect(url_for("clients.index"))

            if duplicate.record_type == "seguimiento":
                before = {
                    "record_type": duplicate.record_type,
                    "pipeline_stage": duplicate.pipeline_stage,
                    "business_name": duplicate.business_name,
                }
                _apply_client_form(duplicate)
                if not duplicate.code:
                    duplicate.code = next_code("CLI", Client)
                audit(
                    "convertir_registro_cliente",
                    "Client",
                    duplicate.id,
                    before=before,
                    after={"record_type": "cliente", "business_name": duplicate.business_name},
                )
                db.session.commit()
                flash("El registro ya existía como seguimiento y fue convertido en cliente.", "success")
                return redirect(url_for("clients.detail", client_id=duplicate.id))

            flash("Ya existe un cliente parecido en tu cartera.", "warning")
            return redirect(url_for("clients.detail", client_id=duplicate.id))

        client = Client(
            code=next_code("CLI", Client),
            business_name=business_name,
            contact_name=contact_name,
            record_type="cliente",
            pipeline_stage="venta_cerrada",
            client_status="activo",
        )
        _apply_client_form(client)
        db.session.add(client)
        db.session.flush()
        audit("crear_cliente", "Client", client.id, after={"business_name": client.business_name})
        db.session.commit()
        flash("Cliente creado correctamente.", "success")
        return redirect(url_for("clients.detail", client_id=client.id))

    return render_template("clients/form.html", client=None, collaborators=collaborators, mode="new")


@bp.route("/<int:client_id>")
@login_required
def detail(client_id):
    client = db.get_or_404(Client, client_id)
    collaborators = []
    if current_user.has_permission("clients.assign"):
        collaborators = Collaborator.query.filter_by(status="activo").order_by(Collaborator.job_title).all()
    products = []
    if current_user.has_permission("clients.edit"):
        products = ProductService.query.filter_by(active=True).order_by(ProductService.name).all()
    timeline = []

    for interaction in client.interactions:
        timeline.append((interaction.occurred_at or interaction.created_at, "Interacción", interaction.subject or interaction.interaction_type, interaction.notes))

    can_financial = current_user.has_permission("sales.view") or current_user.has_permission("finance.view")
    if can_financial:
        for payment in client.payments:
            timeline.append((payment.created_at, "Pago", f"Pago {payment.amount}", payment.reference or payment.method))
        for sale in client.sales:
            timeline.append((sale.created_at, "Venta", sale.sale_no, f"Total {sale.total}"))

    comments = ClientComment.query.filter_by(client_id=client.id).order_by(ClientComment.created_at.desc()).all()
    for comment in comments:
        title = f"{comment.user.name} · {comment.process_type}"
        comment_detail = comment.process_detail or comment.department_snapshot or ""
        timeline.append((
            comment.created_at,
            "Comentario interno",
            title,
            f"{comment_detail} — {comment.body}" if comment_detail else comment.body,
        ))

    timeline.sort(key=lambda x: x[0] or date.min, reverse=True)
    attachments = Attachment.query.filter_by(entity_type="Client", entity_id=client.id).order_by(Attachment.created_at.desc()).all()

    return render_template(
        "clients/detail.html",
        client=client,
        collaborators=collaborators,
        products=products,
        timeline=timeline[:40],
        attachments=attachments,
        comments=comments,
    )


@bp.route("/<int:client_id>/edit", methods=["GET", "POST"])
@login_required
@permission_required("clients.edit")
def edit(client_id):
    client = db.get_or_404(Client, client_id)
    collaborators = _active_collaborators()

    if request.method == "POST":
        business_name = request.form.get("business_name", "").strip()
        contact_name = request.form.get("contact_name", "").strip()
        phone = request.form.get("phone", "").strip()
        email = request.form.get("email", "").strip().lower()

        if not business_name or not contact_name:
            flash("Negocio y contacto son obligatorios.", "danger")
            return render_template("clients/form.html", client=client, collaborators=collaborators, mode="edit")

        duplicate = _find_duplicate(business_name, phone, email, exclude_id=client.id)
        if duplicate:
            flash(f"Existe otro registro parecido: {duplicate.business_name}.", "warning")
            return render_template("clients/form.html", client=client, collaborators=collaborators, mode="edit")

        before = {
            "business_name": client.business_name,
            "contact_name": client.contact_name,
            "status": client.client_status,
            "owner_id": client.owner_id,
        }
        _apply_client_form(client)
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
            },
        )
        db.session.commit()
        flash("Ficha actualizada.", "success")
        return redirect(url_for("clients.detail", client_id=client.id))

    return render_template("clients/form.html", client=client, collaborators=collaborators, mode="edit")


@bp.route("/<int:client_id>/convert", methods=["POST"])
@login_required
@permission_required("clients.edit")
def convert_to_client(client_id):
    client = db.get_or_404(Client, client_id)
    if client.record_type == "cliente":
        flash("Este registro ya es un cliente.", "info")
        return redirect(url_for("clients.detail", client_id=client.id))

    before = {"record_type": client.record_type, "pipeline_stage": client.pipeline_stage}
    client.record_type = "cliente"
    client.pipeline_stage = "venta_cerrada"
    client.client_status = "activo"
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
    flash("Registro convertido en cliente.", "success")
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


@bp.route("/<int:client_id>/contract", methods=["POST"])
@login_required
def add_contract(client_id):
    client = db.get_or_404(Client, client_id)
    product_id = request.form.get("product_id", type=int)
    starts = request.form.get("starts_on")
    ends = request.form.get("ends_on")
    contract = ClientContract(
        client_id=client.id,
        product_id=product_id,
        status=request.form.get("status", "activo"),
        starts_on=date.fromisoformat(starts) if starts else date.today(),
        ends_on=date.fromisoformat(ends) if ends else None,
        agreed_price=request.form.get("agreed_price") or None,
        principal=bool(request.form.get("principal")),
        notes=request.form.get("notes"),
    )
    db.session.add(contract)
    audit("asignar_paquete", "Client", client.id, after={"product_id": product_id, "status": contract.status})
    db.session.commit()
    flash("Paquete/servicio asignado.", "success")
    return redirect(url_for("clients.detail", client_id=client.id))


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
        return redirect(url_for("clients.detail", client_id=client.id))
    try:
        path = save_upload(uploaded, prefix=f"client_{client.id}")
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("clients.detail", client_id=client.id))
    db.session.add(
        Attachment(
            entity_type="Client",
            entity_id=client.id,
            file_name=uploaded.filename,
            file_path=path,
            uploaded_by_id=current_user.id,
        )
    )
    audit("subir_archivo_cliente", "Client", client.id, after={"file": uploaded.filename})
    db.session.commit()
    flash("Archivo agregado a la ficha.", "success")
    return redirect(url_for("clients.detail", client_id=client.id))
