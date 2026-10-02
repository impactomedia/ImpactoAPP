from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import or_
from werkzeug.exceptions import HTTPException

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
    Role,
    Sale,
    SaleItem,
    User,
)
from app.client_v2_models import ClientInstallment
from app.services import D, add_payment, create_sale_from_quote, generate_operational_work, recalc_sale, active_principal_contract, principal_paid_credit, generate_principal_operational_work

bp = Blueprint("sales", __name__, url_prefix="/sales")

COMMERCIAL_ROLE_NAMES = {"superadmin", "admin", "manager", "supervisor", "advisor"}
PAYMENT_METHODS = {"transferencia", "efectivo", "Zelle", "ACH", "Wise", "tarjeta", "otro"}
CURRENCIES = {"USD"}
MONEY = Decimal("0.01")


def _role_name():
    return current_user.role.name if current_user.role else ""


def _team_ids():
    collaborator = current_user.collaborator
    if not collaborator:
        return []
    return [collaborator.id] + [row.id for row in collaborator.subordinates]


def _visible_clients_query():
    query = Client.query.filter(
        Client.record_type.in_(("seguimiento", "cliente")),
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
    """Solo colaboradores comerciales activos aparecen como asesores de venta."""
    query = (
        Collaborator.query
        .join(User, Collaborator.user_id == User.id)
        .join(Role, User.role_id == Role.id)
        .filter(
            Collaborator.status == "activo",
            User.active.is_(True),
            Role.active.is_(True),
            Role.name.in_(COMMERCIAL_ROLE_NAMES),
        )
    )

    role = _role_name()
    collaborator = current_user.collaborator
    if role == "advisor" and collaborator:
        query = query.filter(Collaborator.id == collaborator.id)
    elif role == "supervisor" and collaborator:
        query = query.filter(Collaborator.id.in_(_team_ids()))

    return query.order_by(User.name).all()


def _parse_date(value):
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _parse_money(value, label, *, allow_zero=True):
    raw = str(value or "0").strip()
    try:
        amount = Decimal(raw).quantize(MONEY, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        raise ValueError(f"{label} no es válido.")
    if amount < 0 or (not allow_zero and amount <= 0):
        raise ValueError(
            f"{label} debe ser {'mayor que cero' if not allow_zero else 'cero o mayor'}."
        )
    return amount


def _sale_form_context():
    clients = _visible_clients_query().order_by(Client.business_name).all()
    products = (
        ProductService.query
        .filter(
            ProductService.active.is_(True),
            ProductService.category != "paquete",
        )
        .order_by(ProductService.name)
        .all()
    )
    advisors = _allowed_advisors()
    selected_client_id = (
        request.form.get("client_id", type=int)
        or request.args.get("client_id", type=int)
    )
    return {
        "clients": clients,
        "products": products,
        "advisors": advisors,
        "today": date.today(),
        "selected_client_id": selected_client_id,
    }


def _render_sale_form():
    return render_template("sales/form.html", **_sale_form_context())


def _payment_plan_from_request(remaining_balance, sale_date):
    """Valida y devuelve las cuotas del saldo posterior al pago inicial."""
    remaining_balance = D(remaining_balance).quantize(MONEY)
    if remaining_balance <= 0:
        return []

    mode = (request.form.get("payment_plan_mode") or "single").strip().lower()
    if mode not in {"single", "custom"}:
        mode = "single"

    if mode == "single":
        raw_due = (request.form.get("due_date") or "").strip()
        due_date = _parse_date(raw_due)
        if not due_date:
            raise ValueError("Indica la fecha de vencimiento del saldo.")
        if due_date < sale_date:
            raise ValueError("El vencimiento del saldo no puede ser anterior a la fecha de venta.")
        return [
            {
                "amount": remaining_balance,
                "due_date": due_date,
                "notes": "Saldo único",
            }
        ]

    amounts = request.form.getlist("installment_amount[]")
    dates = request.form.getlist("installment_due_date[]")
    notes = request.form.getlist("installment_notes[]")
    count = max(len(amounts), len(dates), len(notes))
    rows = []

    for index in range(count):
        raw_amount = (amounts[index] if index < len(amounts) else "").strip()
        raw_date = (dates[index] if index < len(dates) else "").strip()
        note = (notes[index] if index < len(notes) else "").strip()

        if not raw_amount and not raw_date and not note:
            continue
        if not raw_amount or not raw_date:
            raise ValueError("Cada cuota debe tener monto y fecha de vencimiento.")

        amount = _parse_money(raw_amount, f"Monto de cuota #{len(rows) + 1}", allow_zero=False)
        due_date = _parse_date(raw_date)
        if not due_date:
            raise ValueError(f"La fecha de la cuota #{len(rows) + 1} no es válida.")
        if due_date < sale_date:
            raise ValueError(
                f"La fecha de la cuota #{len(rows) + 1} no puede ser anterior a la venta."
            )

        rows.append(
            {
                "amount": amount,
                "due_date": due_date,
                "notes": note or None,
            }
        )

    if not rows:
        raise ValueError("Agrega al menos una cuota para el saldo pendiente.")

    scheduled = sum((D(row["amount"]) for row in rows), Decimal("0")).quantize(MONEY)
    if scheduled != remaining_balance:
        raise ValueError(
            "La suma de las cuotas debe ser igual al saldo pendiente "
            f"({remaining_balance:,.2f}). Actualmente suma {scheduled:,.2f}."
        )

    return sorted(rows, key=lambda row: row["due_date"])


def _create_installments(sale, rows, baseline_paid):
    if not rows:
        return []

    created = []
    baseline = D(baseline_paid).quantize(MONEY)
    for sequence, source in enumerate(rows, start=1):
        row = ClientInstallment(
            client_id=sale.client_id,
            sale_id=sale.id,
            sequence=sequence,
            amount=D(source["amount"]).quantize(MONEY),
            paid_amount=Decimal("0"),
            base_paid_amount=baseline,
            due_date=source["due_date"],
            status="vencida" if source["due_date"] < date.today() else "pendiente",
            notes=source.get("notes"),
            created_by_id=current_user.id,
        )
        db.session.add(row)
        created.append(row)
    db.session.flush()
    return created


def _refresh_receivable_due_date(sale):
    """Hace que la cuenta por cobrar muestre la próxima cuota pendiente."""
    if not sale.receivable:
        return

    unpaid = [
        row
        for row in sale.installments
        if row.status != "pagada" and D(row.amount) > D(row.paid_amount)
    ]
    if unpaid:
        sale.receivable.due_date = min(row.due_date for row in unpaid if row.due_date)
    elif D(sale.balance) <= 0:
        sale.receivable.due_date = None


@bp.route("/")
@login_required
def index():
    sales = _visible_sales_query().order_by(Sale.sale_date.desc(), Sale.id.desc()).all()
    return render_template("sales/index.html", sales=sales)


@bp.route("/new", methods=["GET", "POST"])
@login_required
def new_sale():
    context = _sale_form_context()
    clients = context["clients"]
    products = context["products"]
    advisors = context["advisors"]

    visible_client_ids = {client.id for client in clients}
    active_product_ids = {product.id for product in products}
    allowed_advisor_ids = {advisor.id for advisor in advisors}

    if request.method == "GET":
        return render_template("sales/form.html", **context)

    if not clients:
        flash("No hay seguimientos o clientes disponibles para registrar una venta.", "danger")
        return render_template("sales/form.html", **context)

    try:
        client_id = request.form.get("client_id", type=int)
        if not client_id or client_id not in visible_client_ids:
            abort(403)

        client = db.get_or_404(Client, client_id)
        was_followup = client.record_type == "seguimiento"
        role = _role_name()
        collaborator = current_user.collaborator

        requested_advisor_id = request.form.get("advisor_id", type=int)
        advisor_id = requested_advisor_id or client.owner_id

        if role == "advisor" and collaborator:
            advisor_id = collaborator.id
        elif role == "supervisor" and collaborator:
            if advisor_id not in allowed_advisor_ids:
                advisor_id = (
                    client.owner_id
                    if client.owner_id in allowed_advisor_ids
                    else collaborator.id
                )
        elif advisor_id and advisor_id not in allowed_advisor_ids:
            raise ValueError("Selecciona un asesor comercial activo válido.")

        sale_date_raw = (request.form.get("sale_date") or "").strip()
        sale_date = _parse_date(sale_date_raw) if sale_date_raw else date.today()
        if sale_date_raw and not sale_date:
            raise ValueError("La fecha de venta no es válida.")

        currency = "USD"

        sale = Sale(
            sale_no=next_code("VEN", Sale),
            client_id=client.id,
            advisor_id=advisor_id,
            sale_date=sale_date,
            status="confirmada",
            currency=currency,
            notes=(request.form.get("notes") or "").strip() or None,
        )
        db.session.add(sale)
        db.session.flush()

        product_ids = request.form.getlist("product_id[]")
        descriptions = request.form.getlist("description[]")
        quantities = request.form.getlist("quantity[]")
        list_prices = request.form.getlist("unit_price[]")
        discounts = request.form.getlist("discount[]")
        valid_items = 0

        for index, description in enumerate(descriptions):
            description = (description or "").strip()
            if not description:
                continue

            try:
                quantity = Decimal(
                    quantities[index] if index < len(quantities) and quantities[index] else "1"
                )
                list_price = Decimal(
                    list_prices[index] if index < len(list_prices) and list_prices[index] else "0"
                )
                discount = Decimal(
                    discounts[index] if index < len(discounts) and discounts[index] else "0"
                )
            except (InvalidOperation, ValueError):
                raise ValueError("Revisa las cantidades, precios y descuentos de la venta.")

            if quantity <= 0:
                raise ValueError("La cantidad debe ser mayor que cero.")
            if list_price < 0:
                raise ValueError("El precio no puede ser negativo.")
            if discount < 0:
                raise ValueError("El descuento no puede ser negativo.")
            if discount > list_price:
                raise ValueError("El descuento por unidad no puede superar el precio de lista.")

            product_id = None
            if index < len(product_ids) and product_ids[index]:
                try:
                    product_id = int(product_ids[index])
                except ValueError:
                    product_id = None
                if product_id not in active_product_ids:
                    raise ValueError("Uno de los productos seleccionados ya no está disponible.")

            unit_price = list_price - discount
            db.session.add(
                SaleItem(
                    sale_id=sale.id,
                    product_id=product_id,
                    description=description,
                    quantity=quantity,
                    list_price=list_price,
                    discount=discount,
                    unit_price=unit_price,
                    total=quantity * unit_price,
                )
            )
            valid_items += 1

        if not valid_items:
            raise ValueError("Agrega al menos un ítem válido a la venta.")

        db.session.flush()
        recalc_sale(sale)
        if D(sale.total) <= 0:
            raise ValueError("El total de la venta debe ser mayor que cero.")

        initial_payment = _parse_money(
            request.form.get("initial_payment"),
            "El pago inicial",
            allow_zero=True,
        )
        if initial_payment > D(sale.total):
            raise ValueError("El pago inicial no puede superar el total de la venta.")

        initial_date_raw = (request.form.get("initial_payment_date") or "").strip()
        initial_payment_date = (
            _parse_date(initial_date_raw)
            if initial_date_raw
            else sale_date
        )
        if initial_date_raw and not initial_payment_date:
            raise ValueError("La fecha del pago inicial no es válida.")

        payment_method = request.form.get("payment_method", "transferencia")
        if payment_method not in PAYMENT_METHODS:
            payment_method = "otro"

        remaining_after_initial = (D(sale.total) - initial_payment).quantize(MONEY)
        installment_rows = _payment_plan_from_request(
            remaining_after_initial,
            sale_date,
        )
        first_due_date = installment_rows[0]["due_date"] if installment_rows else None

        receivable = AccountReceivable(
            client_id=client.id,
            sale_id=sale.id,
            total_amount=sale.total,
            paid_amount=0,
            due_date=first_due_date,
            status="al_dia",
            notes="Plan de pago generado al crear la venta.",
        )
        db.session.add(receivable)

        client.record_type = "cliente"
        client.pipeline_stage = "venta_cerrada"
        client.client_status = "activo"
        client.country = "USA"
        if was_followup:
            audit(
                "convertir_registro_cliente",
                "Client",
                client.id,
                before={"record_type": "seguimiento"},
                after={"record_type": "cliente", "pipeline_stage": "venta_cerrada"},
                reason=f"Compra confirmada {sale.sale_no}",
            )
        db.session.flush()

        _create_installments(
            sale,
            installment_rows,
            baseline_paid=initial_payment,
        )

        if initial_payment > 0:
            add_payment(
                sale,
                initial_payment,
                payment_method,
                effective_date=initial_payment_date,
                reference=(request.form.get("initial_payment_reference") or "").strip() or None,
                notes="Pago inicial registrado con la venta.",
                registered_by_id=current_user.id,
            )

        generate_operational_work(sale)
        recalc_sale(sale)
        _refresh_receivable_due_date(sale)

        audit(
            "crear_venta",
            "Sale",
            sale.id,
            after={
                "sale_no": sale.sale_no,
                "total": str(sale.total),
                "initial_payment": str(initial_payment),
                "installments": len(installment_rows),
            },
        )
        db.session.commit()
        flash("Venta registrada, plan de pago creado y flujo operativo generado.", "success")
        return redirect(url_for("sales.detail", sale_id=sale.id))

    except (ValueError, InvalidOperation) as exc:
        db.session.rollback()
        flash(str(exc), "danger")
        return _render_sale_form()
    except HTTPException:
        db.session.rollback()
        raise
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Error al crear una venta")
        flash(
            "No se pudo registrar la venta. No se guardaron cambios parciales. "
            "Revisa los datos e inténtalo nuevamente.",
            "danger",
        )
        return _render_sale_form()


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
        _refresh_receivable_due_date(sale)
        db.session.commit()
        flash("Cotización convertida en venta.", "success")
        return redirect(url_for("sales.detail", sale_id=sale.id))
    except (ValueError, InvalidOperation) as exc:
        db.session.rollback()
        flash(str(exc), "danger")
        return redirect(url_for("crm.quote_detail", quote_id=quote.id))



@bp.route("/principal/<int:client_id>", methods=["GET", "POST"])
@login_required
def principal_service(client_id):
    client = _visible_clients_query().filter(Client.id == client_id).first_or_404()
    plans = (
        ProductService.query
        .filter_by(active=True, category="paquete")
        .order_by(ProductService.base_price, ProductService.name)
        .all()
    )
    current_contract = active_principal_contract(client)
    current_price = D(
        current_contract.agreed_price
        if current_contract and current_contract.agreed_price is not None
        else (current_contract.product.base_price if current_contract and current_contract.product else 0)
    )
    recognized_credit = principal_paid_credit(current_contract) if current_contract else Decimal("0")

    eligible_plans = []
    for plan in plans:
        price = D(plan.base_price)
        if price <= 0:
            continue
        if current_contract and (plan.id == current_contract.product_id or price <= current_price):
            continue
        eligible_plans.append(plan)

    if request.method == "GET":
        return render_template(
            "sales/principal_form.html",
            client=client,
            plans=eligible_plans,
            current_contract=current_contract,
            current_price=current_price,
            recognized_credit=recognized_credit,
            today=date.today(),
        )

    try:
        product_id = request.form.get("product_id", type=int)
        plan = next((row for row in eligible_plans if row.id == product_id), None)
        if not plan:
            raise ValueError(
                "Selecciona un paquete principal con precio fijo superior al plan actual."
                if current_contract
                else "Selecciona un paquete principal con precio fijo configurado."
            )

        catalog_price = D(plan.base_price).quantize(MONEY)
        credit = (
            min(catalog_price, recognized_credit.quantize(MONEY))
            if current_contract
            else Decimal("0")
        )
        net_total = (catalog_price - credit).quantize(MONEY)
        if net_total <= 0:
            raise ValueError("El nuevo plan debe generar un saldo mayor que cero.")

        role = _role_name()
        collaborator = current_user.collaborator
        advisors = _allowed_advisors()
        allowed_advisor_ids = {row.id for row in advisors}
        advisor_id = request.form.get("advisor_id", type=int) or client.owner_id
        if role == "advisor" and collaborator:
            advisor_id = collaborator.id
        elif role == "supervisor" and collaborator:
            if advisor_id not in allowed_advisor_ids:
                advisor_id = client.owner_id if client.owner_id in allowed_advisor_ids else collaborator.id
        elif advisor_id and advisor_id not in allowed_advisor_ids:
            raise ValueError("Selecciona un asesor comercial activo válido.")

        sale_date_raw = (request.form.get("sale_date") or "").strip()
        sale_date = _parse_date(sale_date_raw) if sale_date_raw else date.today()
        if sale_date_raw and not sale_date:
            raise ValueError("La fecha de venta no es válida.")

        sale = Sale(
            sale_no=next_code("VEN", Sale),
            client_id=client.id,
            advisor_id=advisor_id,
            sale_date=sale_date,
            status="confirmada",
            currency="USD",
            notes=(
                f"Upgrade de servicio principal desde {current_contract.product.name} a {plan.name}."
                if current_contract and current_contract.product
                else f"Servicio principal: {plan.name}."
            ),
        )
        db.session.add(sale)
        db.session.flush()

        db.session.add(
            SaleItem(
                sale_id=sale.id,
                product_id=plan.id,
                description=(
                    f"Upgrade a {plan.name}"
                    if current_contract
                    else f"Servicio principal {plan.name}"
                ),
                quantity=1,
                list_price=catalog_price,
                discount=credit,
                unit_price=net_total,
                total=net_total,
            )
        )
        db.session.flush()
        recalc_sale(sale)

        initial_payment = _parse_money(
            request.form.get("initial_payment"),
            "El pago inicial",
            allow_zero=True,
        )
        if initial_payment > D(sale.total):
            raise ValueError("El pago inicial no puede superar el saldo del nuevo plan.")

        initial_date_raw = (request.form.get("initial_payment_date") or "").strip()
        initial_payment_date = _parse_date(initial_date_raw) if initial_date_raw else sale_date
        if initial_date_raw and not initial_payment_date:
            raise ValueError("La fecha del pago inicial no es válida.")

        payment_method = request.form.get("payment_method", "transferencia")
        if payment_method not in PAYMENT_METHODS:
            payment_method = "otro"

        remaining_after_initial = (D(sale.total) - initial_payment).quantize(MONEY)
        installment_rows = _payment_plan_from_request(remaining_after_initial, sale_date)
        first_due_date = installment_rows[0]["due_date"] if installment_rows else None

        db.session.add(
            AccountReceivable(
                client_id=client.id,
                sale_id=sale.id,
                total_amount=sale.total,
                paid_amount=0,
                due_date=first_due_date,
                status="al_dia",
                notes="Saldo vigente del servicio principal en Impacto Nexora.",
            )
        )

        was_followup = client.record_type == "seguimiento"
        client.record_type = "cliente"
        client.pipeline_stage = "venta_cerrada"
        client.client_status = "activo"
        client.country = "USA"
        if not client.code:
            client.code = next_code("CLI", Client)

        if was_followup:
            audit(
                "convertir_registro_cliente",
                "Client",
                client.id,
                before={"record_type": "seguimiento"},
                after={"record_type": "cliente", "pipeline_stage": "venta_cerrada"},
                reason=f"Servicio principal confirmado {sale.sale_no}",
            )

        db.session.flush()
        _create_installments(sale, installment_rows, baseline_paid=initial_payment)

        if initial_payment > 0:
            add_payment(
                sale,
                initial_payment,
                payment_method,
                effective_date=initial_payment_date,
                reference=(request.form.get("initial_payment_reference") or "").strip() or None,
                notes="Pago inicial del servicio principal.",
                registered_by_id=current_user.id,
            )

        contract = generate_principal_operational_work(
            sale,
            plan,
            previous_contract=current_contract,
            courtesies=request.form.get("courtesies"),
        )
        recalc_sale(sale)
        _refresh_receivable_due_date(sale)

        audit(
            "servicio_principal_upgrade" if current_contract else "servicio_principal_nuevo",
            "ClientContract",
            contract.id,
            after={
                "client_id": client.id,
                "product_id": plan.id,
                "catalog_price": str(catalog_price),
                "credit": str(credit),
                "net_total": str(net_total),
                "sale_no": sale.sale_no,
            },
        )
        db.session.commit()

        flash(
            (
                f"Upgrade aplicado. {plan.name}: USD {catalog_price:,.2f}; "
                f"crédito reconocido USD {credit:,.2f}; nuevo saldo USD {net_total:,.2f}."
                if current_contract
                else f"Servicio principal {plan.name} registrado por USD {catalog_price:,.2f}."
            ),
            "success",
        )
        return redirect(url_for("clients.detail", client_id=client.id, _anchor="servicios"))

    except (ValueError, InvalidOperation) as exc:
        db.session.rollback()
        flash(str(exc), "danger")
        return redirect(url_for("sales.principal_service", client_id=client.id))
    except HTTPException:
        db.session.rollback()
        raise
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Error al registrar servicio principal para cliente %s", client.id)
        flash("No se pudo registrar el servicio principal. No se guardaron cambios parciales.", "danger")
        return redirect(url_for("sales.principal_service", client_id=client.id))


@bp.route("/<int:sale_id>")
@login_required
def detail(sale_id):
    sale = db.get_or_404(Sale, sale_id)
    return render_template("sales/detail.html", sale=sale)


@bp.route("/<int:sale_id>/payment", methods=["POST"])
@login_required
def payment(sale_id):
    sale = db.get_or_404(Sale, sale_id)
    if sale.status == "reemplazada":
        flash("Esta venta fue sustituida por un upgrade. Registra los pagos en el servicio principal vigente.", "warning")
        return redirect(url_for("sales.detail", sale_id=sale.id))
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
        _refresh_receivable_due_date(sale)
        db.session.commit()
        flash("Pago registrado. Saldo, cuotas y comisión actualizados.", "success")
    except (ValueError, InvalidOperation) as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Error registrando pago de venta %s", sale_id)
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
        audit(
            "registrar_deduccion",
            "Sale",
            sale.id,
            after={"concept": row.concept, "amount": str(row.amount)},
        )
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
    _refresh_receivable_due_date(sale)
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
