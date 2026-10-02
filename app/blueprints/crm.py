from datetime import datetime
from decimal import Decimal, InvalidOperation

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import or_

from app.decorators import roles_required
from app.extensions import db
from app.helpers import audit, next_code
from app.models import (
    AdvisorProject,
    Client,
    Collaborator,
    Interaction,
    OwnershipHistory,
    ProductService,
    Quote,
    QuoteItem,
)
from app.services import recalc_quote

bp = Blueprint("crm", __name__, url_prefix="/crm")

STAGES = [
    "nuevo",
    "intento_contacto",
    "contactado",
    "calificado",
    "interesado",
    "seguimiento",
    "cotizacion_enviada",
    "negociacion",
    "pendiente_pago",
    "venta_cerrada",
    "perdido",
]
PRIORITIES = {"baja", "media", "alta", "urgente"}
INTERACTION_TYPES = {"llamada", "whatsapp", "email", "reunion", "nota"}
CURRENCIES = {"USD"}


def _role_name():
    return current_user.role.name if current_user.role else ""


def _team_ids():
    collaborator = current_user.collaborator
    if not collaborator:
        return []
    return [collaborator.id] + [row.id for row in collaborator.subordinates]


def visible_clients():
    query = Client.query
    role = _role_name()
    collaborator = current_user.collaborator
    if role == "advisor" and collaborator:
        query = query.filter(Client.owner_id == collaborator.id)
    elif role == "supervisor" and collaborator:
        query = query.filter(Client.owner_id.in_(_team_ids()))
    return query


def _assignable_owners():
    query = Collaborator.query.filter_by(status="activo")
    role = _role_name()
    collaborator = current_user.collaborator

    if role == "advisor" and collaborator:
        query = query.filter(Collaborator.id == collaborator.id)
    elif role == "supervisor" and collaborator:
        query = query.filter(Collaborator.id.in_(_team_ids()))

    return query.order_by(Collaborator.advisor_project_id, Collaborator.id).all()


def _decimal_or_none(value):
    if value in (None, ""):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result


def _parse_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


@bp.route("/")
@login_required
def prospects():
    query = visible_clients().filter(Client.record_type == "seguimiento")
    search = request.args.get("q", "").strip()
    stage = request.args.get("stage", "")
    advisor_project_id = request.args.get("advisor_project_id", type=int)

    if search:
        like = f"%{search}%"
        query = query.filter(
            or_(
                Client.business_name.ilike(like),
                Client.contact_name.ilike(like),
                Client.phone.ilike(like),
                Client.email.ilike(like),
            )
        )
    if stage in STAGES:
        query = query.filter_by(pipeline_stage=stage)
    if advisor_project_id:
        query = query.join(Collaborator, Client.owner_id == Collaborator.id).filter(
            Collaborator.advisor_project_id == advisor_project_id
        )

    clients = query.order_by(Client.updated_at.desc()).all()
    advisor_projects = AdvisorProject.query.filter_by(active=True).order_by(AdvisorProject.name).all()
    return render_template(
        "crm/prospects.html",
        clients=clients,
        stages=STAGES,
        search=search,
        stage=stage,
        advisor_project_id=advisor_project_id,
        advisor_projects=advisor_projects,
    )


@bp.route("/pipeline")
@login_required
def pipeline():
    query = visible_clients().filter(Client.record_type == "seguimiento")
    advisor_project_id = request.args.get("advisor_project_id", type=int)
    if advisor_project_id:
        query = query.join(Collaborator, Client.owner_id == Collaborator.id).filter(
            Collaborator.advisor_project_id == advisor_project_id
        )

    clients = query.order_by(Client.updated_at.desc()).all()
    columns = {stage: [] for stage in STAGES}
    for client in clients:
        columns.setdefault(client.pipeline_stage, []).append(client)

    advisor_projects = AdvisorProject.query.filter_by(active=True).order_by(AdvisorProject.name).all()
    return render_template(
        "crm/pipeline.html",
        columns=columns,
        stages=STAGES,
        advisor_projects=advisor_projects,
        advisor_project_id=advisor_project_id,
    )


@bp.route("/new", methods=["GET", "POST"])
@login_required
def new_prospect():
    collaborators = _assignable_owners()
    allowed_owner_ids = {row.id for row in collaborators}

    if request.method == "POST":
        business_name = (request.form.get("business_name") or "").strip()
        contact_name = (request.form.get("contact_name") or "").strip()
        phone = (request.form.get("phone") or "").strip()
        email = (request.form.get("email") or "").strip().lower()

        if not business_name or not contact_name:
            flash("Negocio y contacto son obligatorios.", "danger")
            return render_template("crm/prospect_form.html", collaborators=collaborators)

        priority = request.form.get("priority", "media")
        if priority not in PRIORITIES:
            priority = "media"

        budget_raw = request.form.get("estimated_budget")
        budget = _decimal_or_none(budget_raw)
        if budget_raw not in (None, "") and budget is None:
            flash("El presupuesto estimado no es válido.", "danger")
            return render_template("crm/prospect_form.html", collaborators=collaborators)
        if budget is not None and budget < 0:
            flash("El presupuesto estimado no puede ser negativo.", "danger")
            return render_template("crm/prospect_form.html", collaborators=collaborators)

        duplicate_conditions = [Client.business_name == business_name]
        if phone:
            duplicate_conditions.append(Client.phone == phone)
        if email:
            duplicate_conditions.append(Client.email == email)
        possible = Client.query.filter(or_(*duplicate_conditions)).first()
        if possible:
            visible_duplicate = visible_clients().filter(Client.id == possible.id).first()
            if visible_duplicate:
                flash(
                    f"Posible duplicado: {possible.business_name}. Revisa el registro existente antes de continuar.",
                    "warning",
                )
            else:
                flash(
                    "Ya existe un registro coincidente fuera de tu cartera. Solicita una revisión antes de crear un duplicado.",
                    "warning",
                )
            return render_template("crm/prospect_form.html", collaborators=collaborators)

        owner_id = request.form.get("owner_id", type=int)
        role = _role_name()
        if role == "advisor" and current_user.collaborator:
            owner_id = current_user.collaborator.id
        elif owner_id and owner_id not in allowed_owner_ids:
            abort(403)
        elif not owner_id and current_user.collaborator:
            owner_id = current_user.collaborator.id

        client = Client(
            code=next_code("CLI", Client),
            business_name=business_name,
            contact_name=contact_name,
            phone=phone or None,
            email=email or None,
            preferred_channel=request.form.get("preferred_channel"),
            industry=(request.form.get("industry") or "").strip() or None,
            services_offered=request.form.get("services_offered"),
            website=(request.form.get("website") or "").strip() or None,
            facebook=(request.form.get("facebook") or "").strip() or None,
            instagram=(request.form.get("instagram") or "").strip() or None,
            address=(request.form.get("address") or "").strip() or None,
            city=(request.form.get("city") or "").strip() or None,
            state=(request.form.get("state") or "").strip() or None,
            country="USA",
            timezone=(request.form.get("timezone") or "").strip() or None,
            source=(request.form.get("source") or "").strip() or None,
            owner_id=owner_id,
            priority=priority,
            main_interest=(request.form.get("main_interest") or "").strip() or None,
            estimated_budget=budget,
            record_type="seguimiento",
            pipeline_stage="nuevo",
            notes=request.form.get("notes"),
        )
        db.session.add(client)
        db.session.flush()
        audit("crear_prospecto", "Client", client.id, after={"business_name": client.business_name})
        db.session.commit()
        flash("Seguimiento creado.", "success")
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
    return redirect(url_for("crm.detail", client_id=client.id))


@bp.route("/<int:client_id>/interaction", methods=["POST"])
@login_required
def add_interaction(client_id):
    client = db.get_or_404(Client, client_id)
    interaction_type = request.form.get("interaction_type", "nota")
    if interaction_type not in INTERACTION_TYPES:
        interaction_type = "nota"

    next_followup_at = None
    raw_next = request.form.get("next_followup_at")
    if raw_next:
        try:
            next_followup_at = datetime.fromisoformat(raw_next)
        except ValueError:
            flash("La fecha del próximo seguimiento no es válida.", "warning")

    interaction = Interaction(
        client_id=client.id,
        user_id=current_user.id,
        interaction_type=interaction_type,
        occurred_at=datetime.utcnow(),
        subject=(request.form.get("subject") or "").strip() or None,
        notes=request.form.get("notes"),
        result=request.form.get("result"),
        next_followup_at=next_followup_at,
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
    collaborators = _assignable_owners()
    allowed_ids = {row.id for row in collaborators}
    new_owner_id = request.form.get("owner_id", type=int)

    if not new_owner_id:
        flash("Selecciona un responsable comercial.", "warning")
        return redirect(url_for("crm.detail", client_id=client.id))
    if new_owner_id not in allowed_ids:
        abort(403)
    if new_owner_id == client.owner_id:
        flash("El prospecto ya está asignado a ese responsable.", "info")
        return redirect(url_for("crm.detail", client_id=client.id))

    db.session.add(
        OwnershipHistory(
            client_id=client.id,
            previous_owner_id=client.owner_id,
            new_owner_id=new_owner_id,
            reason=(request.form.get("reason") or "").strip() or None,
            changed_by_id=current_user.id,
        )
    )
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
    active_product_ids = {product.id for product in products}

    if request.method == "POST":
        currency = "USD"

        try:
            discount = Decimal(str(request.form.get("discount") or 0))
        except (InvalidOperation, TypeError, ValueError):
            flash("El descuento no es válido.", "danger")
            return render_template("crm/quote_form.html", client=client, products=products)
        if discount < 0:
            flash("El descuento no puede ser negativo.", "danger")
            return render_template("crm/quote_form.html", client=client, products=products)

        valid_until_raw = request.form.get("valid_until")
        valid_until = _parse_date(valid_until_raw)
        if valid_until_raw and not valid_until:
            flash("La fecha de validez no es válida.", "danger")
            return render_template("crm/quote_form.html", client=client, products=products)

        quote = Quote(
            quote_no=next_code("COT", Quote),
            client_id=client.id,
            advisor_id=client.owner_id,
            status="borrador",
            currency=currency,
            discount=discount,
            valid_until=valid_until,
            notes=request.form.get("notes"),
        )
        db.session.add(quote)
        db.session.flush()

        product_ids = request.form.getlist("product_id[]")
        descriptions = request.form.getlist("description[]")
        quantities = request.form.getlist("quantity[]")
        prices = request.form.getlist("unit_price[]")
        valid_items = 0

        for index, description in enumerate(descriptions):
            description = (description or "").strip()
            if not description:
                continue
            try:
                quantity = Decimal(quantities[index] or "1")
                price = Decimal(prices[index] or "0")
            except (InvalidOperation, IndexError, TypeError, ValueError):
                db.session.rollback()
                flash("Revisa las cantidades y precios de la cotización.", "danger")
                return render_template("crm/quote_form.html", client=client, products=products)

            if quantity <= 0 or price < 0:
                db.session.rollback()
                flash("La cantidad debe ser mayor que cero y el precio no puede ser negativo.", "danger")
                return render_template("crm/quote_form.html", client=client, products=products)

            product_id = None
            if index < len(product_ids) and product_ids[index]:
                try:
                    product_id = int(product_ids[index])
                except ValueError:
                    product_id = None
                if product_id not in active_product_ids:
                    db.session.rollback()
                    flash("Uno de los productos seleccionados ya no está disponible.", "danger")
                    return render_template("crm/quote_form.html", client=client, products=products)

            db.session.add(
                QuoteItem(
                    quote_id=quote.id,
                    product_id=product_id,
                    description=description,
                    quantity=quantity,
                    unit_price=price,
                    total=quantity * price,
                )
            )
            valid_items += 1

        if not valid_items:
            db.session.rollback()
            flash("Agrega al menos un ítem válido a la cotización.", "danger")
            return render_template("crm/quote_form.html", client=client, products=products)

        db.session.flush()
        recalc_quote(quote)
        if quote.subtotal <= 0:
            db.session.rollback()
            flash("La cotización debe tener un subtotal mayor que cero.", "danger")
            return render_template("crm/quote_form.html", client=client, products=products)
        if discount > quote.subtotal:
            db.session.rollback()
            flash("El descuento no puede superar el subtotal de la cotización.", "danger")
            return render_template("crm/quote_form.html", client=client, products=products)

        mark_sent = bool(request.form.get("mark_sent"))
        quote.status = "enviada" if mark_sent else "borrador"
        if mark_sent:
            client.pipeline_stage = "cotizacion_enviada"

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
