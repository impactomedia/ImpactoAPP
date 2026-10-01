from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import or_

from app.extensions import db
from app.helpers import audit, next_code
from app.models import (
    AccountReceivable,
    Client,
    Collaborator,
    Deduction,
    Income,
    Payment,
    ProductService,
    Quote,
    Renewal,
    Sale,
    SaleItem,
)
from app.services import D, add_payment, create_sale_from_quote, generate_operational_work, recalc_sale

bp = Blueprint("sales", __name__, url_prefix="/sales")


def _role_name():
    return current_user.role.name if current_user.role else ""


def _team_ids():
    collaborator = current_user.collaborator
    if not collaborator:
        return []
    return [collaborator.id] + [row.id for row in collaborator.subordinates]


def _visible_clients_query():
    query = Client.query.filter(
        Client.record_type == "cliente",
        Client.client_status != "archivado",
    )
    role = _role_name()
    collaborator = current_user.collaborator
    if role == "advisor" and collaborator:
        query = query.filter(Client.owner_id == collaborator.id)
    elif role == "supervisor" and collaborator:
        query = query.filter(Client.owner_id.in_(_team_ids()))
    return query


def _visible_sales_query():
    query = Sale.query
    role = _role_name()
    collaborator = current_user.collaborator
    if role == "advisor" and collaborator:
        query = query.filter(
            or_(
                Sale.advisor_id == collaborator.id,
                Sale.client.has(Client.owner_id == collaborator.id),
            )
        )
    elif role == "supervisor" and collaborator:
        ids = _team_ids()
        query = query.filter(
            or_(
                Sale.advisor_id.in_(ids),
                Sale.client.has(Client.owner_id.in_(ids)),
            )
        )
    return query


def _visible_renewals_query():
    query = Renewal.query
    role = _role_name()
    collaborator = current_user.collaborator
    if role == "advisor" and collaborator:
        query = query.filter(Renewal.client.has(Client.owner_id == collaborator.id))
    elif role == "supervisor" and collaborator:
        query = query.filter(Renewal.client.has(Client.owner_id.in_(_team_ids())))
    return query


def _allowed_advisors():
    query = Collaborator.query.filter_by(status="activo")
    role = _role_name()
    collaborator = current_user.collaborator
    if role == "advisor" and collaborator:
        query = query.filter(Collaborator.id == collaborator.id)
    elif role == "supervisor" and collaborator:
        query = query.filter(Collaborator.id.in_(_team_ids()))
    return query.order_by(Collaborator.id).all()


def _parse_date(value):
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


@bp.route("/")
@login_required
def index():
    sales = _visible_sales_query().order_by(Sale.sale_date.desc(), Sale.id.desc()).all()
    return render_template("sales/index.html", sales=sales)


@bp.route("/new", methods=["GET", "POST"])
@login_required
def new_sale():
    clients = _visible_clients_query().order_by(Client.business_name).all()
    products = ProductService.query.filter_by(active=True).order_by(ProductService.name).all()
    advisors = _allowed_advisors()
    visible_client_ids = {client.id for client in clients}
    allowed_advisor_ids = {advisor.id for advisor in advisors}

    if request.method == "POST":
        client_id = request.form.get("client_id", type=int)
        if not client_id or client_id not in visible_client_ids:
            abort(403)

        client = db.get_or_404(Client, client_id)
        role = _role_name()
        collaborator = current_user.collaborator

        requested_advisor_id = request.form.get("advisor_id", type=int)
        advisor_id = requested_advisor_id or client.owner_id
        if role == "advisor" and collaborator:
            advisor_id = collaborator.id
        elif role == "supervisor":
            if advisor_id not in allowed_advisor_ids:
                advisor_id = client.owner_id if client.owner_id in allowed_advisor_ids else current_user.collaborator.id
        elif advisor_id and advisor_id not in allowed_advisor_ids:
            flash("Selecciona un asesor activo válido.", "danger")
            return render_template("sales/form.html", clients=clients, products=products, advisors=advisors)

        sale_date = _parse_date(request.form.get("sale_date")) or date.today()
        due_date = _parse_date(request.form.get("due_date"))
        currency = request.form.get("currency", "USD")
        if currency not in {"USD", "NIO"}:
            currency = "USD"

        sale = Sale(
            sale_no=next_code("VEN", Sale),
            client_id=client.id,
            advisor_id=advisor_id,
            sale_date=sale_date,
            status="confirmada",
            currency=currency,
            notes=request.form.get("notes"),
        )
        db.session.add(sale)
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
            except (InvalidOperation, IndexError, ValueError):
                db.session.rollback()
                flash("Revisa las cantidades y precios de la venta.", "danger")
                return render_template("sales/form.html", clients=clients, products=products, advisors=advisors)

            if quantity <= 0 or price < 0:
                db.session.rollback()
                flash("La cantidad debe ser mayor que cero y el precio no puede ser negativo.", "danger")
                return render_template("sales/form.html", clients=clients, products=products, advisors=advisors)

            product_id = None
            if index < len(product_ids) and product_ids[index]:
                try:
                    product_id = int(product_ids[index])
                except ValueError:
                    product_id = None

            db.session.add(
                SaleItem(
                    sale_id=sale.id,
                    product_id=product_id,
                    description=description,
                    quantity=quantity,
                    list_price=price,
                    discount=0,
                    unit_price=price,
                    total=quantity * price,
                )
            )
            valid_items += 1

        if not valid_items:
            db.session.rollback()
            flash("Agrega al menos un ítem válido a la venta.", "danger")
            return render_template("sales/form.html", clients=clients, products=products, advisors=advisors)

        db.session.flush()
        recalc_sale(sale)
        if D(sale.total) <= 0:
            db.session.rollback()
            flash("El total de la venta debe ser mayor que cero.", "danger")
            return render_template("sales/form.html", clients=clients, products=products, advisors=advisors)

        receivable = AccountReceivable(
            client_id=client.id,
            sale_id=sale.id,
            total_amount=sale.total,
            paid_amount=0,
            due_date=due_date,
            status="al_dia",
        )
        db.session.add(receivable)
        client.record_type = "cliente"
        client.pipeline_stage = "venta_cerrada"
        client.client_status = "activo"
        db.session.flush()

        try:
            initial_payment = D(request.form.get("initial_payment"))
            if initial_payment > 0:
                add_payment(
                    sale,
                    initial_payment,
                    request.form.get("payment_method", "transferencia"),
                    registered_by_id=current_user.id,
                )
            generate_operational_work(sale)
            recalc_sale(sale)
        except (ValueError, InvalidOperation) as exc:
            db.session.rollback()
            flash(str(exc), "danger")
            return render_template("sales/form.html", clients=clients, products=products, advisors=advisors)

        audit("crear_venta", "Sale", sale.id, after={"sale_no": sale.sale_no, "total": str(sale.total)})
        db.session.commit()
        flash("Venta registrada y flujo operativo generado.", "success")
        return redirect(url_for("sales.detail", sale_id=sale.id))

    return render_template("sales/form.html", clients=clients, products=products, advisors=advisors)


@bp.route("/from-quote/<int:quote_id>", methods=["POST"])
@login_required
def from_quote(quote_id):
    quote = db.get_or_404(Quote, quote_id)
    try:
        sale = create_sale_from_quote(
            quote,
            initial_payment=request.form.get("initial_payment") or 0,
            payment_method=request.form.get("payment_method", "transferencia"),
            due_date=_parse_date(request.form.get("due_date")),
            user_id=current_user.id,
        )
        db.session.commit()
        flash("Cotización convertida en venta.", "success")
        return redirect(url_for("sales.detail", sale_id=sale.id))
    except (ValueError, InvalidOperation) as exc:
        db.session.rollback()
        flash(str(exc), "danger")
        return redirect(url_for("crm.quote_detail", quote_id=quote.id))


@bp.route("/<int:sale_id>")
@login_required
def detail(sale_id):
    sale = db.get_or_404(Sale, sale_id)
    return render_template("sales/detail.html", sale=sale)


@bp.route("/<int:sale_id>/payment", methods=["POST"])
@login_required
def payment(sale_id):
    sale = db.get_or_404(Sale, sale_id)
    try:
        payment_row = add_payment(
            sale,
            request.form.get("amount"),
            request.form.get("method", "transferencia"),
            effective_date=_parse_date(request.form.get("effective_date")) or date.today(),
            reference=request.form.get("reference"),
            status="confirmado",
            notes=request.form.get("notes"),
            registered_by_id=current_user.id,
        )
        db.session.commit()
        flash("Pago registrado. Saldo y comisión actualizados.", "success")
    except (ValueError, InvalidOperation) as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    except Exception:
        db.session.rollback()
        flash("No se pudo registrar el pago. Revisa los datos e inténtalo nuevamente.", "danger")
    return redirect(url_for("sales.detail", sale_id=sale.id))


@bp.route("/<int:sale_id>/deduction", methods=["POST"])
@login_required
def deduction(sale_id):
    sale = db.get_or_404(Sale, sale_id)
    if sale.commission and sale.commission.status == "pagada":
        flash("No se puede modificar la base comisionable después de pagar la comisión.", "danger")
        return redirect(url_for("sales.detail", sale_id=sale.id))

    try:
        amount = D(request.form.get("amount"))
    except InvalidOperation:
        amount = Decimal("0")

    if amount <= 0:
        flash("La deducción debe ser mayor que cero.", "danger")
    elif amount > D(sale.total):
        flash("La deducción no puede superar el total de la venta.", "danger")
    else:
        row = Deduction(
            sale_id=sale.id,
            concept=(request.form.get("concept") or "Deducción").strip(),
            amount=amount,
            affects_commission=bool(request.form.get("affects_commission")),
            notes=request.form.get("notes"),
        )
        db.session.add(row)
        db.session.flush()
        recalc_sale(sale)
        audit("registrar_deduccion", "Sale", sale.id, after={"concept": row.concept, "amount": str(row.amount)})
        db.session.commit()
        flash("Deducción registrada y comisión recalculada.", "success")
    return redirect(url_for("sales.detail", sale_id=sale.id))


@bp.route("/payments/<int:payment_id>/reverse", methods=["POST"])
@login_required
def reverse_payment(payment_id):
    payment_row = db.get_or_404(Payment, payment_id)
    sale = payment_row.sale

    if payment_row.status != "confirmado":
        flash("Este pago ya no está confirmado y no puede reversarse nuevamente.", "warning")
        return redirect(url_for("sales.detail", sale_id=sale.id))

    if sale.commission and sale.commission.status == "pagada":
        flash("No se puede reversar el pago porque la comisión asociada ya fue pagada.", "danger")
        return redirect(url_for("sales.detail", sale_id=sale.id))

    payment_row.status = "reversado"

    income = (
        Income.query
        .filter_by(
            sale_id=sale.id,
            effective_date=payment_row.effective_date,
            amount=payment_row.amount,
            status="confirmado",
        )
        .order_by(Income.id.desc())
        .first()
    )
    if income:
        income.status = "reversado"

    recalc_sale(sale)
    audit("reversar_pago", "Payment", payment_row.id, reason=request.form.get("reason"))
    db.session.commit()
    flash("Pago e ingreso asociado reversados con trazabilidad.", "warning")
    return redirect(url_for("sales.detail", sale_id=sale.id))


@bp.route("/renewals")
@login_required
def renewals():
    rows = _visible_renewals_query().order_by(Renewal.due_date.asc()).all()
    return render_template("sales/renewals.html", rows=rows, today=date.today())


@bp.route("/renewals/<int:renewal_id>/update", methods=["POST"])
@login_required
def renewal_update(renewal_id):
    row = db.get_or_404(Renewal, renewal_id)
    status = request.form.get("status", row.status)
    allowed_statuses = {"pendiente", "contactado", "renovado", "no_renueva", "cancelado"}
    if status not in allowed_statuses:
        flash("Estado de renovación no válido.", "danger")
        return redirect(url_for("sales.renewals"))

    row.status = status
    row.notes = request.form.get("notes", row.notes)
    row.last_contact_at = datetime.utcnow()
    audit("actualizar_renovacion", "Renewal", row.id, after={"status": row.status})
    db.session.commit()
    flash("Renovación actualizada.", "success")
    return redirect(url_for("sales.renewals"))
