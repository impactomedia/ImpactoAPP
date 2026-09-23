import csv
import io
from datetime import date
from flask import Blueprint, render_template, request, redirect, url_for, flash, Response
from flask_login import login_required, current_user
from sqlalchemy import or_

from app.extensions import db
from app.helpers import audit, next_code, save_upload
from app.models import Client, Collaborator, ClientCollaborator, ClientContract, ProductService, Attachment, ClientComment, AdvisorProject

bp = Blueprint("clients", __name__, url_prefix="/clients")


@bp.route("/")
@login_required
def index():
    q = Client.query.filter(Client.record_type == "cliente")
    search = request.args.get("q", "").strip()
    status = request.args.get("status", "")
    advisor_project_id = request.args.get("advisor_project_id", type=int)
    if search:
        like = f"%{search}%"
        q = q.filter(or_(Client.business_name.ilike(like), Client.contact_name.ilike(like), Client.phone.ilike(like), Client.email.ilike(like)))
    if status:
        q = q.filter_by(client_status=status)
    if advisor_project_id:
        q = q.join(Collaborator, Client.owner_id == Collaborator.id).filter(Collaborator.advisor_project_id == advisor_project_id)
    clients = q.order_by(Client.updated_at.desc()).all()
    advisor_projects = AdvisorProject.query.filter_by(active=True).order_by(AdvisorProject.name).all()
    return render_template("clients/index.html", clients=clients, search=search, status=status, advisor_project_id=advisor_project_id, advisor_projects=advisor_projects)


@bp.route("/<int:client_id>")
@login_required
def detail(client_id):
    client = db.get_or_404(Client, client_id)
    collaborators = Collaborator.query.filter_by(status="activo").order_by(Collaborator.job_title).all()
    products = ProductService.query.filter_by(active=True).order_by(ProductService.name).all()
    timeline = []
    for x in client.interactions:
        timeline.append((x.occurred_at or x.created_at, "Interacción", x.subject or x.interaction_type, x.notes))
    for p in client.payments:
        timeline.append((p.created_at, "Pago", f"Pago {p.amount}", p.reference or p.method))
    for s in client.sales:
        timeline.append((s.created_at, "Venta", s.sale_no, f"Total {s.total}"))
    timeline.sort(key=lambda x: x[0] or date.min, reverse=True)
    comments = ClientComment.query.filter_by(client_id=client.id).order_by(ClientComment.created_at.desc()).all()
    for comment in comments:
        title = f"{comment.user.name} · {comment.process_type}"
        detail = comment.process_detail or comment.department_snapshot or ""
        timeline.append((comment.created_at, "Comentario interno", title, f"{detail} — {comment.body}" if detail else comment.body))
    timeline.sort(key=lambda x: x[0] or date.min, reverse=True)
    attachments = Attachment.query.filter_by(entity_type="Client", entity_id=client.id).order_by(Attachment.created_at.desc()).all()
    return render_template("clients/detail.html", client=client, collaborators=collaborators, products=products, timeline=timeline[:40], attachments=attachments, comments=comments)


@bp.route("/<int:client_id>/edit", methods=["GET", "POST"])
@login_required
def edit(client_id):
    client = db.get_or_404(Client, client_id)
    if request.method == "POST":
        before = {"business_name": client.business_name, "status": client.client_status}
        for field in ["business_name", "contact_name", "phone", "email", "industry", "services_offered", "website", "facebook", "instagram", "address", "city", "state", "country", "timezone", "notes"]:
            if field in request.form:
                setattr(client, field, request.form.get(field))
        client.client_status = request.form.get("client_status", client.client_status)
        audit("editar_cliente", "Client", client.id, before=before, after={"business_name": client.business_name, "status": client.client_status})
        db.session.commit()
        flash("Ficha actualizada.", "success")
        return redirect(url_for("clients.detail", client_id=client.id))
    return render_template("clients/edit.html", client=client)


@bp.route("/<int:client_id>/assign", methods=["POST"])
@login_required
def assign_collaborator(client_id):
    client = db.get_or_404(Client, client_id)
    collaborator_id = request.form.get("collaborator_id", type=int)
    if collaborator_id and not ClientCollaborator.query.filter_by(client_id=client.id, collaborator_id=collaborator_id).first():
        assignment = ClientCollaborator(client_id=client.id, collaborator_id=collaborator_id, role_in_client=request.form.get("role_in_client"), primary=bool(request.form.get("primary")))
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
    clients = Client.query.order_by(Client.id).all()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["code", "business_name", "contact_name", "phone", "email", "record_type", "pipeline_stage", "client_status", "source"])
    for c in clients:
        writer.writerow([c.code, c.business_name, c.contact_name, c.phone, c.email, c.record_type, c.pipeline_stage, c.client_status, c.source])
    audit("exportar", "Client")
    db.session.commit()
    return Response(buf.getvalue(), mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=clientes.csv"})


@bp.route("/import", methods=["GET", "POST"])
@login_required
def import_csv():
    if request.method == "POST":
        f = request.files.get("file")
        if not f:
            flash("Selecciona un CSV.", "danger")
            return redirect(url_for("clients.import_csv"))
        content = f.read().decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(content))
        created = 0
        rejected = []
        for i, row in enumerate(reader, start=2):
            business = (row.get("business_name") or "").strip()
            contact = (row.get("contact_name") or "").strip()
            phone = (row.get("phone") or "").strip()
            email = (row.get("email") or "").strip().lower()
            if not business or not contact:
                rejected.append((i, "Falta business_name/contact_name"))
                continue
            dup = Client.query.filter(or_(Client.email == email if email else False, Client.phone == phone if phone else False, Client.business_name == business)).first()
            if dup:
                rejected.append((i, f"Posible duplicado: {dup.business_name}"))
                continue
            c = Client(code=next_code("CLI", Client), business_name=business, contact_name=contact, phone=phone, email=email, record_type=row.get("record_type") or "seguimiento", pipeline_stage=row.get("pipeline_stage") or "nuevo", client_status=row.get("client_status") or "activo", source=row.get("source"))
            db.session.add(c)
            db.session.flush()
            created += 1
        audit("importar_clientes", "Client", after={"created": created, "rejected": len(rejected)})
        db.session.commit()
        flash(f"Importación finalizada: {created} creados, {len(rejected)} rechazados.", "success" if not rejected else "warning")
        return render_template("clients/import.html", rejected=rejected)
    return render_template("clients/import.html", rejected=None)


@bp.route("/<int:client_id>/attachment", methods=["POST"])
@login_required
def upload_attachment(client_id):
    client = db.get_or_404(Client, client_id)
    f = request.files.get("file")
    if not f:
        flash("Selecciona un archivo.", "danger")
        return redirect(url_for("clients.detail", client_id=client.id))
    try:
        path = save_upload(f, prefix=f"client_{client.id}")
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("clients.detail", client_id=client.id))
    db.session.add(Attachment(entity_type="Client", entity_id=client.id, file_name=f.filename, file_path=path, uploaded_by_id=current_user.id))
    audit("subir_archivo_cliente", "Client", client.id, after={"file": f.filename})
    db.session.commit()
    flash("Archivo agregado a la ficha.", "success")
    return redirect(url_for("clients.detail", client_id=client.id))
