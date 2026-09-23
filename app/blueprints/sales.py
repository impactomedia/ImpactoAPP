from datetime import date
from decimal import Decimal, InvalidOperation
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user

from app.extensions import db
from app.helpers import audit, next_code
from app.models import Client, Collaborator, ProductService, Sale, SaleItem, Payment, Deduction, Quote, Renewal
from app.services import D, add_payment, create_sale_from_quote, recalc_sale

bp = Blueprint("sales", __name__, url_prefix="/sales")


@bp.route("/")
@login_required
def index():
    q = Sale.query
    if current_user.role and current_user.role.name == "advisor" and current_user.collaborator:
        q = q.filter_by(advisor_id=current_user.collaborator.id)
    sales = q.order_by(Sale.sale_date.desc(), Sale.id.desc()).all()
    return render_template("sales/index.html", sales=sales)


@bp.route("/new", methods=["GET", "POST"])
@login_required
def new_sale():
    clients = Client.query.order_by(Client.business_name).all()
    products = ProductService.query.filter_by(active=True).order_by(ProductService.name).all()
    advisors = Collaborator.query.filter_by(status="activo").order_by(Collaborator.id).all()
    if request.method == "POST":
        client = db.get_or_404(Client, request.form.get("client_id", type=int))
        advisor_id = request.form.get("advisor_id", type=int) or client.owner_id
        sale = Sale(
            sale_no=next_code("VEN", Sale),
            client_id=client.id,
            advisor_id=advisor_id,
            sale_date=date.fromisoformat(request.form.get("sale_date")) if request.form.get("sale_date") else date.today(),
            status="confirmada",
            currency=request.form.get("currency", "USD"),
            notes=request.form.get("notes"),
        )
        db.session.add(sale)
        db.session.flush()
        pids = request.form.getlist("product_id[]")
        descs = request.form.getlist("description[]")
        qtys = request.form.getlist("quantity[]")
        prices = request.form.getlist("unit_price[]")
        for i, desc in enumerate(descs):
            if not desc.strip():
                continue
            try:
                qty = Decimal(qtys[i] or "1")
                price = Decimal(prices[i] or "0")
            except (InvalidOperation, IndexError):
                qty, price = Decimal("1"), Decimal("0")
            pid = int(pids[i]) if i < len(pids) and pids[i] else None
            db.session.add(SaleItem(sale_id=sale.id, product_id=pid, description=desc.strip(), quantity=qty, list_price=price, discount=0, unit_price=price, total=qty*price))
        db.session.flush()
        recalc_sale(sale)
        # create receivable and operational work by using a synthetic quote path would duplicate; do it directly after commit route below
        from app.models import AccountReceivable
        ar = AccountReceivable(client_id=client.id, sale_id=sale.id, total_amount=sale.total, paid_amount=0, due_date=date.fromisoformat(request.form.get("due_date")) if request.form.get("due_date") else None, status="al_dia")
        db.session.add(ar)
        client.record_type = "cliente"
        client.pipeline_stage = "venta_cerrada"
        client.client_status = "activo"
        db.session.flush()
        initial = D(request.form.get("initial_payment"))
        if initial > 0:
            add_payment(sale, initial, request.form.get("payment_method", "transferencia"), registered_by_id=current_user.id)
        from app.services import generate_operational_work
        generate_operational_work(sale)
        recalc_sale(sale)
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
            due_date=date.fromisoformat(request.form.get("due_date")) if request.form.get("due_date") else None,
            user_id=current_user.id,
        )
        db.session.commit()
        flash("Cotización convertida en venta.", "success")
        return redirect(url_for("sales.detail", sale_id=sale.id))
    except ValueError as exc:
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
    amount = D(request.form.get("amount"))
    if amount <= 0:
        flash("El monto debe ser mayor que cero.", "danger")
    else:
        try:
            payment = add_payment(
                sale,
                amount,
                request.form.get("method", "transferencia"),
                effective_date=date.fromisoformat(request.form.get("effective_date")) if request.form.get("effective_date") else date.today(),
                reference=request.form.get("reference"),
                status=request.form.get("status", "confirmado"),
                notes=request.form.get("notes"),
                registered_by_id=current_user.id,
            )
            db.session.commit()
            flash("Pago registrado. Saldo y comisión actualizados.", "success")
        except Exception as exc:
            db.session.rollback()
            flash(f"No se pudo registrar el pago: {exc}", "danger")
    return redirect(url_for("sales.detail", sale_id=sale.id))


@bp.route("/<int:sale_id>/deduction", methods=["POST"])
@login_required
def deduction(sale_id):
    sale = db.get_or_404(Sale, sale_id)
    amount = D(request.form.get("amount"))
    if amount <= 0:
        flash("La deducción debe ser mayor que cero.", "danger")
    else:
        d = Deduction(
            sale_id=sale.id,
            concept=request.form.get("concept", "Deducción"),
            amount=amount,
            affects_commission=bool(request.form.get("affects_commission")),
            notes=request.form.get("notes"),
        )
        db.session.add(d)
        db.session.flush()
        recalc_sale(sale)
        audit("registrar_deduccion", "Sale", sale.id, after={"concept": d.concept, "amount": str(d.amount)})
        db.session.commit()
        flash("Deducción registrada y comisión recalculada.", "success")
    return redirect(url_for("sales.detail", sale_id=sale.id))


@bp.route("/payments/<int:payment_id>/reverse", methods=["POST"])
@login_required
def reverse_payment(payment_id):
    payment = db.get_or_404(Payment, payment_id)
    if payment.status == "confirmado":
        payment.status = "reversado"
        recalc_sale(payment.sale)
        audit("reversar_pago", "Payment", payment.id, reason=request.form.get("reason"))
        db.session.commit()
        flash("Pago reversado con trazabilidad.", "warning")
    return redirect(url_for("sales.detail", sale_id=payment.sale_id))


@bp.route("/renewals")
@login_required
def renewals():
    rows = Renewal.query.order_by(Renewal.due_date.asc()).all()
    return render_template("sales/renewals.html", rows=rows, today=date.today())


@bp.route("/renewals/<int:renewal_id>/update", methods=["POST"])
@login_required
def renewal_update(renewal_id):
    row = db.get_or_404(Renewal, renewal_id)
    status = request.form.get("status", row.status)
    if status in {"pendiente", "contactado", "renovado", "no_renueva", "cancelado"}:
        row.status = status
    row.notes = request.form.get("notes", row.notes)
    from datetime import datetime
    row.last_contact_at = datetime.utcnow()
    audit("actualizar_renovacion", "Renewal", row.id, after={"status": row.status})
    db.session.commit()
    flash("Renovación actualizada.", "success")
    return redirect(url_for("sales.renewals"))
