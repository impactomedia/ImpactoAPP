from datetime import date, datetime, timedelta
from decimal import Decimal

from app.extensions import db
from app.helpers import audit, notify, next_code
from app.models import (
    AccountReceivable,
    Client,
    ClientContract,
    Commission,
    CommissionRule,
    Collaborator,
    Deduction,
    Income,
    Payment,
    PrintItem,
    PrintOrder,
    ProductService,
    Project,
    Quote,
    Renewal,
    Sale,
    SaleItem,
    Task,
    TaskTemplate,
)


def D(value):
    if value is None or value == "":
        return Decimal("0")
    return Decimal(str(value))


def recalc_quote(quote):
    subtotal = sum((D(i.quantity) * D(i.unit_price) for i in quote.items), Decimal("0"))
    quote.subtotal = subtotal
    quote.total = max(Decimal("0"), subtotal - D(quote.discount))
    return quote


def recalc_sale(sale):
    sale.total = sum((D(i.total) for i in sale.items), Decimal("0"))
    confirmed = sum((D(p.amount) for p in sale.payments if p.status == "confirmado"), Decimal("0"))
    sale.amount_paid = confirmed
    sale.balance = max(Decimal("0"), D(sale.total) - confirmed)
    ar = sale.receivable
    if ar:
        ar.total_amount = sale.total
        ar.paid_amount = confirmed
        today = date.today()
        if sale.balance <= 0:
            ar.status = "pagado"
        elif ar.due_date and ar.due_date < today:
            ar.status = "vencido"
        elif ar.due_date and (ar.due_date - today).days <= 7:
            ar.status = "proximo_vencer"
        else:
            ar.status = "al_dia"
    recalc_commission(sale)
    return sale


def choose_commission_rule(sale, commission_base):
    rules = CommissionRule.query.filter_by(active=True).order_by(CommissionRule.threshold_min.desc()).all()
    for rule in rules:
        minimum = D(rule.threshold_min)
        maximum = D(rule.threshold_max) if rule.threshold_max is not None else None
        if commission_base < minimum:
            continue
        if maximum is not None and commission_base > maximum:
            continue
        if rule.product_id:
            if not any(item.product_id == rule.product_id for item in sale.items):
                continue
        return rule
    return None


def recalc_commission(sale):
    if not sale.advisor_id:
        return None
    deductions = sum((D(d.amount) for d in sale.deductions if d.affects_commission), Decimal("0"))
    full_base = max(Decimal("0"), D(sale.total) - deductions)
    rule = choose_commission_rule(sale, full_base)
    if not rule:
        commission = sale.commission
        if commission:
            commission.base_amount = full_base
            commission.amount = Decimal("0")
            commission.status = "estimada"
        return commission

    trigger = rule.trigger or "paid"
    if trigger == "proportional":
        proportional_base = max(Decimal("0"), D(sale.amount_paid) - deductions)
        base = min(full_base, proportional_base)
        status = "generada" if sale.amount_paid > 0 else "estimada"
    elif trigger == "sale":
        base = full_base
        status = "generada"
    else:  # paid
        base = full_base
        status = "generada" if D(sale.balance) <= 0 and D(sale.total) > 0 else "estimada"

    raw = base * (D(rule.percentage) / Decimal("100")) + D(rule.fixed_amount)
    minimum = D(rule.minimum_commission)
    minimum_applied = bool(raw < minimum and base > 0)
    amount = max(raw, minimum) if base > 0 else Decimal("0")

    commission = sale.commission
    if not commission:
        commission = Commission(sale_id=sale.id, advisor_id=sale.advisor_id)
        db.session.add(commission)
    commission.rule_id = rule.id
    commission.base_amount = base
    commission.percentage = rule.percentage
    commission.minimum_applied = minimum_applied
    commission.amount = amount
    if commission.status != "pagada":
        commission.status = status
    return commission


def add_payment(sale, amount, method, effective_date=None, reference=None, status="confirmado", notes=None, registered_by_id=None):
    payment = Payment(
        client_id=sale.client_id,
        sale_id=sale.id,
        effective_date=effective_date or date.today(),
        amount=D(amount),
        currency=sale.currency,
        method=method,
        reference=reference,
        status=status,
        notes=notes,
        registered_by_id=registered_by_id,
    )
    db.session.add(payment)
    db.session.flush()
    if status == "confirmado":
        income = Income(
            effective_date=payment.effective_date,
            client_id=sale.client_id,
            sale_id=sale.id,
            concept=f"Pago {sale.sale_no}",
            amount=payment.amount,
            currency=sale.currency,
            method=method,
            reference=reference,
            advisor_id=sale.advisor_id,
            status="confirmado",
        )
        db.session.add(income)
    recalc_sale(sale)
    audit("registrar_pago", "Sale", sale.id, after={"amount": str(payment.amount), "method": method})
    return payment


def create_sale_from_quote(quote, initial_payment=0, payment_method="transferencia", due_date=None, user_id=None):
    if quote.status not in {"aceptada", "enviada", "borrador"}:
        raise ValueError("La cotización no puede convertirse en venta desde su estado actual")
    client = quote.client
    sale = Sale(
        sale_no=next_code("VEN", Sale),
        client_id=client.id,
        advisor_id=quote.advisor_id or client.owner_id,
        quote_id=quote.id,
        sale_date=date.today(),
        status="confirmada",
        currency=quote.currency,
        total=quote.total,
        amount_paid=0,
        balance=quote.total,
        notes=quote.notes,
    )
    db.session.add(sale)
    db.session.flush()
    for qi in quote.items:
        item = SaleItem(
            sale_id=sale.id,
            product_id=qi.product_id,
            description=qi.description,
            quantity=qi.quantity,
            list_price=qi.unit_price,
            discount=0,
            unit_price=qi.unit_price,
            total=D(qi.quantity) * D(qi.unit_price),
        )
        db.session.add(item)
    db.session.flush()
    recalc_sale(sale)
    ar = AccountReceivable(
        client_id=client.id,
        sale_id=sale.id,
        total_amount=sale.total,
        paid_amount=0,
        due_date=due_date,
        status="al_dia",
    )
    db.session.add(ar)
    client.record_type = "cliente"
    client.pipeline_stage = "venta_cerrada"
    client.client_status = "activo"
    quote.status = "aceptada"
    db.session.flush()

    if D(initial_payment) > 0:
        add_payment(sale, initial_payment, payment_method, registered_by_id=user_id)

    generate_operational_work(sale)
    recalc_sale(sale)
    audit("convertir_cotizacion_venta", "Sale", sale.id, after={"sale_no": sale.sale_no, "client": client.business_name})
    return sale


def generate_operational_work(sale):
    for item in sale.items:
        product = item.product
        if not product:
            continue
        duration = product.duration_months or 0
        end_date = date.today() + timedelta(days=duration * 30) if duration else None
        contract = ClientContract(
            client_id=sale.client_id,
            product_id=product.id,
            sale_id=sale.id,
            status="activo",
            starts_on=date.today(),
            ends_on=end_date,
            agreed_price=item.total,
            principal=False,
        )
        db.session.add(contract)
        if product.renewal_required or end_date:
            renewal_due = end_date or (date.today() + timedelta(days=365))
            db.session.add(Renewal(client_id=sale.client_id, contract=contract, renewal_type=product.name, due_date=renewal_due))

        if product.is_physical:
            order = PrintOrder(
                order_no=next_code("IMP", PrintOrder),
                client_id=sale.client_id,
                sale_id=sale.id,
                status="diseno",
                total_sale=item.total,
            )
            db.session.add(order)
            db.session.flush()
            db.session.add(PrintItem(order_id=order.id, name=item.description, quantity=int(D(item.quantity)), design_status="pendiente"))
        else:
            project = Project(
                project_no=next_code("PRJ", Project),
                client_id=sale.client_id,
                sale_id=sale.id,
                product_id=product.id,
                name=f"{product.name} - {sale.client.business_name}",
                department=product.responsible_area,
                status="pendiente_onboarding",
                progress=0,
                starts_on=date.today(),
                due_on=date.today() + timedelta(days=30),
            )
            db.session.add(project)
            db.session.flush()
            template = TaskTemplate.query.filter_by(product_id=product.id, active=True).first()
            if template and template.items:
                for ti in template.items:
                    db.session.add(Task(
                        title=ti.title,
                        description=ti.description,
                        task_type=ti.task_type,
                        client_id=sale.client_id,
                        project_id=project.id,
                        priority=ti.priority,
                        status="pendiente",
                        due_at=datetime.utcnow() + timedelta(days=ti.due_days or 3),
                        checklist="[ ] Requisito obligatorio" if ti.required else None,
                    ))
            else:
                db.session.add(Task(
                    title=f"Onboarding: {product.name}",
                    description="Recopilar información, accesos, materiales y aprobación inicial del cliente.",
                    task_type="cliente",
                    client_id=sale.client_id,
                    project_id=project.id,
                    priority="alta",
                    status="pendiente",
                    due_at=datetime.utcnow() + timedelta(days=3),
                ))


def update_print_order_status(order):
    items = order.items
    if not items:
        return order.status
    if all(i.qty_received >= i.quantity for i in items):
        order.status = "recibido"
    elif order.incidents and any(i.status == "abierta" for i in order.incidents):
        order.status = "incidencia"
    elif any(s.status == "en_transito" for s in order.shipments):
        order.status = "en_transito"
    elif all(i.shipping_approved_at for i in items) and not order.shipments:
        order.status = "aprobado_envio"
    elif all(i.design_approved_at for i in items):
        order.status = "produccion"
    else:
        order.status = "diseno"
    return order.status


def refresh_overdue_receivables():
    today = date.today()
    for ar in AccountReceivable.query.filter(AccountReceivable.status != "pagado").all():
        if ar.due_date and ar.due_date < today:
            ar.status = "vencido"


def refresh_expired_contracts():
    today = date.today()
    for contract in ClientContract.query.filter(ClientContract.status == "activo").all():
        if contract.ends_on and contract.ends_on < today:
            contract.status = "caducado"


def ensure_renewal_notifications(days=(60, 30, 15, 7, 0)):
    today = date.today()
    renewals = Renewal.query.filter(Renewal.status == "pendiente").all()
    for renewal in renewals:
        delta = (renewal.due_date - today).days
        if delta in days:
            owner = renewal.client.owner
            if owner and owner.user_id:
                title = f"Renovación: {renewal.client.business_name}"
                msg = f"{renewal.renewal_type} vence en {delta} día(s) ({renewal.due_date})."
                already = False
                for n in owner.user.notifications:
                    if n.title == title and n.message == msg and not n.read:
                        already = True
                        break
                if not already:
                    notify(owner.user_id, title, msg, link=f"/clients/{renewal.client_id}", priority="alta" if delta <= 7 else "normal")
