from datetime import datetime, date
from decimal import Decimal, InvalidOperation
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from sqlalchemy import or_

from app.extensions import db
from app.decorators import roles_required
from app.helpers import audit, next_code
from app.models import Client, Collaborator, Interaction, Quote, QuoteItem, ProductService, OwnershipHistory, AdvisorProject
from app.services import recalc_quote

bp = Blueprint("crm", __name__, url_prefix="/crm")

STAGES = ["nuevo", "intento_contacto", "contactado", "calificado", "interesado", "seguimiento", "cotizacion_enviada", "negociacion", "pendiente_pago", "venta_cerrada", "perdido"]


def visible_clients():
    q = Client.query
    role = current_user.role.name if current_user.role else ""
    if role == "advisor" and current_user.collaborator:
        q = q.filter_by(owner_id=current_user.collaborator.id)
    elif role == "supervisor" and current_user.collaborator:
        ids = [c.id for c in current_user.collaborator.subordinates] + [current_user.collaborator.id]
        q = q.filter(Client.owner_id.in_(ids))
    return q


@bp.route("/")
@login_required
def prospects():
    q = visible_clients().filter(Client.record_type == "seguimiento")
    search = request.args.get("q", "").strip()
    stage = request.args.get("stage", "")
    advisor_project_id = request.args.get("advisor_project_id", type=int)
    if search:
        like = f"%{search}%"
        q = q.filter(or_(Client.business_name.ilike(like), Client.contact_name.ilike(like), Client.phone.ilike(like), Client.email.ilike(like)))
    if stage:
        q = q.filter_by(pipeline_stage=stage)
    if advisor_project_id:
        q = q.join(Collaborator, Client.owner_id == Collaborator.id).filter(Collaborator.advisor_project_id == advisor_project_id)
    clients = q.order_by(Client.updated_at.desc()).all()
    advisor_projects = AdvisorProject.query.filter_by(active=True).order_by(AdvisorProject.name).all()
    return render_template("crm/prospects.html", clients=clients, stages=STAGES, search=search, stage=stage, advisor_project_id=advisor_project_id, advisor_projects=advisor_projects)


@bp.route("/pipeline")
@login_required
def pipeline():
    q = visible_clients().filter(Client.record_type == "seguimiento")
    advisor_project_id = request.args.get("advisor_project_id", type=int)
    if advisor_project_id:
        q = q.join(Collaborator, Client.owner_id == Collaborator.id).filter(Collaborator.advisor_project_id == advisor_project_id)
    clients = q.order_by(Client.updated_at.desc()).all()
    columns = {stage: [] for stage in STAGES}
    for client in clients:
        columns.setdefault(client.pipeline_stage, []).append(client)
    advisor_projects = AdvisorProject.query.filter_by(active=True).order_by(AdvisorProject.name).all()
    return render_template("crm/pipeline.html", columns=columns, stages=STAGES, advisor_projects=advisor_projects, advisor_project_id=advisor_project_id)


@bp.route("/new", methods=["GET", "POST"])
@login_required
def new_prospect():
    collaborators = Collaborator.query.filter_by(status="activo").order_by(Collaborator.advisor_project_id, Collaborator.id).all()
    if request.method == "POST":
        business_name = request.form.get("business_name", "").strip()
        contact_name = request.form.get("contact_name", "").strip()
        phone = request.form.get("phone", "").strip()
        email = request.form.get("email", "").strip().lower()
        possible = Client.query.filter(or_(Client.phone == phone if phone else False, Client.email == email if email else False, Client.business_name == business_name)).first()
        if possible:
            flash(f"Posible duplicado: {possible.business_name}. Revisa el registro existente antes de continuar.", "warning")
            return render_template("crm/prospect_form.html", collaborators=collaborators)
        owner_id = request.form.get("owner_id", type=int)
        if not owner_id and current_user.collaborator:
            owner_id = current_user.collaborator.id
        client = Client(
            code=next_code("CLI", Client),
            business_name=business_name,
            contact_name=contact_name,
            phone=phone,
            email=email,
            preferred_channel=request.form.get("preferred_channel"),
            industry=request.form.get("industry"),
            services_offered=request.form.get("services_offered"),
            website=request.form.get("website"),
            facebook=request.form.get("facebook"),
            instagram=request.form.get("instagram"),
            address=request.form.get("address"),
            city=request.form.get("city"),
            state=request.form.get("state"),
            country=request.form.get("country"),
            timezone=request.form.get("timezone"),
            source=request.form.get("source"),
            owner_id=owner_id,
            priority=request.form.get("priority", "media"),
            main_interest=request.form.get("main_interest"),
            estimated_budget=request.form.get("estimated_budget") or None,
            record_type="seguimiento",
            pipeline_stage="nuevo",
            notes=request.form.get("notes"),
        )
        db.session.add(client)
        db.session.flush()
        audit("crear_prospecto", "Client", client.id, after={"business_name": client.business_name})
        db.session.commit()
        flash("Prospecto creado.", "success")
        return redirect(url_for("crm.detail", client_id=client.id))
    return render_template("crm/prospect_form.html", collaborators=collaborators)


@bp.route("/<int:client_id>")
@login_required
def detail(client_id):
    client = db.get_or_404(Client, client_id)
    if client.record_type == "cliente":
        return redirect(url_for("clients.detail", client_id=client.id))
    interactions = Interaction.query.filter_by(client_id=client.id).order_by(Interaction.occurred_at.desc()).all()
    quotes = Quote.query.filter_by(client_id=client.id).order_by(Quote.created_at.desc()).all()
    return render_template("crm/detail.html", client=client, interactions=interactions, quotes=quotes, stages=STAGES)


@bp.route("/<int:client_id>/stage", methods=["POST"])
@login_required
def move_stage(client_id):
    client = db.get_or_404(Client, client_id)
    stage = request.form.get("stage")
    if stage not in STAGES:
        flash("Etapa no válida.", "danger")
    else:
        before = client.pipeline_stage
        client.pipeline_stage = stage
        audit("cambiar_etapa", "Client", client.id, before={"stage": before}, after={"stage": stage})
        db.session.commit()
        flash("Etapa actualizada.", "success")
    return redirect(request.referrer or url_for("crm.detail", client_id=client.id))


@bp.route("/<int:client_id>/interaction", methods=["POST"])
@login_required
def add_interaction(client_id):
    client = db.get_or_404(Client, client_id)
    dt = None
    raw_next = request.form.get("next_followup_at")
    if raw_next:
        try:
            dt = datetime.fromisoformat(raw_next)
        except ValueError:
            dt = None
    interaction = Interaction(
        client_id=client.id,
        user_id=current_user.id,
        interaction_type=request.form.get("interaction_type", "nota"),
        occurred_at=datetime.utcnow(),
        subject=request.form.get("subject"),
        notes=request.form.get("notes"),
        result=request.form.get("result"),
        next_followup_at=dt,
    )
    db.session.add(interaction)
    audit("agregar_interaccion", "Client", client.id, after={"type": interaction.interaction_type})
    db.session.commit()
    flash("Actividad registrada.", "success")
    return redirect(url_for("crm.detail", client_id=client.id))


@bp.route("/<int:client_id>/transfer", methods=["POST"])
@login_required
@roles_required("superadmin", "admin", "manager", "supervisor")
def transfer(client_id):
    client = db.get_or_404(Client, client_id)
    new_owner_id = request.form.get("owner_id", type=int)
    if new_owner_id and new_owner_id != client.owner_id:
        db.session.add(OwnershipHistory(client_id=client.id, previous_owner_id=client.owner_id, new_owner_id=new_owner_id, reason=request.form.get("reason"), changed_by_id=current_user.id))
        client.owner_id = new_owner_id
        audit("reasignar_cliente", "Client", client.id, reason=request.form.get("reason"))
        db.session.commit()
        flash("Responsable comercial actualizado.", "success")
    return redirect(url_for("crm.detail", client_id=client.id))


@bp.route("/<int:client_id>/quotes/new", methods=["GET", "POST"])
@login_required
def new_quote(client_id):
    client = db.get_or_404(Client, client_id)
    products = ProductService.query.filter_by(active=True).order_by(ProductService.name).all()
    if request.method == "POST":
        quote = Quote(
            quote_no=next_code("COT", Quote),
            client_id=client.id,
            advisor_id=client.owner_id,
            status="borrador",
            currency=request.form.get("currency", "USD"),
            discount=request.form.get("discount") or 0,
            valid_until=datetime.strptime(request.form.get("valid_until"), "%Y-%m-%d").date() if request.form.get("valid_until") else None,
            notes=request.form.get("notes"),
        )
        db.session.add(quote)
        db.session.flush()
        product_ids = request.form.getlist("product_id[]")
        descriptions = request.form.getlist("description[]")
        quantities = request.form.getlist("quantity[]")
        prices = request.form.getlist("unit_price[]")
        for idx, desc in enumerate(descriptions):
            if not desc.strip():
                continue
            try:
                qty = Decimal(quantities[idx] or "1")
                price = Decimal(prices[idx] or "0")
            except (InvalidOperation, IndexError):
                qty, price = Decimal("1"), Decimal("0")
            pid = int(product_ids[idx]) if idx < len(product_ids) and product_ids[idx] else None
            db.session.add(QuoteItem(quote_id=quote.id, product_id=pid, description=desc.strip(), quantity=qty, unit_price=price, total=qty*price))
        db.session.flush()
        recalc_quote(quote)
        client.pipeline_stage = "cotizacion_enviada" if request.form.get("mark_sent") else "cotizacion_enviada"
        quote.status = "enviada" if request.form.get("mark_sent") else "borrador"
        audit("crear_cotizacion", "Quote", quote.id, after={"quote_no": quote.quote_no, "total": str(quote.total)})
        db.session.commit()
        flash("Cotización creada.", "success")
        return redirect(url_for("crm.quote_detail", quote_id=quote.id))
    return render_template("crm/quote_form.html", client=client, products=products)


@bp.route("/quotes/<int:quote_id>")
@login_required
def quote_detail(quote_id):
    quote = db.get_or_404(Quote, quote_id)
    return render_template("crm/quote_detail.html", quote=quote)
