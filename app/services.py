from datetime import date, datetime, timedelta
from decimal import Decimal

from app.extensions import db
from app.helpers import audit, notify, next_code
from app.models import (
    AccountReceivable,
    ClientContract,
    Commission,
    CommissionRule,
    Income,
    Payment,
    PrintItem,
    PrintOrder,
    ProductService,
    Project,
    Renewal,
    Sale,
    SaleItem,
    Task,
    TaskTemplate,
)
from app.client_v2_models import ClientCollectionNote, ClientContractDetail, ClientInstallment


def D(value):
    if value is None or value == "":
        return Decimal("0")
    return Decimal(str(value))


def recalc_quote(quote):
    subtotal = sum((D(i.quantity) * D(i.unit_price) for i in quote.items), Decimal("0"))
    quote.subtotal = subtotal
    quote.total = max(Decimal("0"), subtotal - D(quote.discount))
    return quote


def sync_installments_for_sale(sale):
    """Distribuye pagos confirmados sobre las cuotas, de la más antigua a la más reciente.

    No crea movimientos financieros nuevos: únicamente refleja el avance del plan de pago
    usando como fuente de verdad los pagos confirmados de la venta.
    """
    installments = sorted(
        list(getattr(sale, "installments", []) or []),
        key=lambda row: (row.due_date or date.max, row.sequence or 0, row.id or 0),
    )
    if not installments:
        return

    confirmed_total = sum(
        (D(payment.amount) for payment in sale.payments if payment.status == "confirmado"),
        Decimal("0"),
    )
    baseline = D(installments[0].base_paid_amount) if installments else Decimal("0")
    remaining = max(Decimal("0"), confirmed_total - baseline)
    today = date.today()
    for row in installments:
        amount = max(Decimal("0"), D(row.amount))
        applied = min(amount, max(Decimal("0"), remaining))
        row.paid_amount = applied
        remaining = max(Decimal("0"), remaining - applied)

        if amount > 0 and applied >= amount:
            row.status = "pagada"
        elif applied > 0:
            row.status = "parcial"
        elif row.due_date and row.due_date < today:
            row.status = "vencida"
        else:
            row.status = "pendiente"


def sync_collection_notes_for_sale(sale):
    """Actualiza promesas abiertas con base en el saldo real de la venta."""
    notes = list(getattr(sale, "collection_notes", []) or [])
    if not notes:
        return
    today = date.today()
    for note in notes:
        if note.note_type != "promesa" or note.status in {"cancelada", "cumplida"}:
            continue
        if D(sale.balance) <= 0:
            note.status = "cumplida"
        elif note.promise_date and note.promise_date < today:
            note.status = "incumplida"
        else:
            note.status = "abierta"


def recalc_sale(sale):
    sale.total = sum((D(i.total) for i in sale.items), Decimal("0"))
    confirmed = sum((D(p.amount) for p in sale.payments if p.status == "confirmado"), Decimal("0"))
    sale.amount_paid = confirmed
    sale.balance = max(Decimal("0"), D(sale.total) - confirmed)
    receivable = sale.receivable
    if receivable:
        receivable.total_amount = sale.total
        receivable.paid_amount = confirmed
        today = date.today()
        if sale.balance <= 0:
            receivable.status = "pagado"
        elif receivable.promise_date and receivable.promise_date >= today:
            receivable.status = "promesa_pago"
        elif receivable.due_date and receivable.due_date < today:
            receivable.status = "vencido"
        elif receivable.due_date and (receivable.due_date - today).days <= 7:
            receivable.status = "proximo_vencer"
        else:
            receivable.status = "al_dia"

    sync_installments_for_sale(sale)
    sync_collection_notes_for_sale(sale)
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
        if rule.product_id and not any(item.product_id == rule.product_id for item in sale.items):
            continue
        return rule
    return None


def recalc_commission(sale):
    if not sale.advisor_id:
        return None

    # Una comisión ya pagada se congela. No debe cambiar su monto por ajustes
    # posteriores de la venta sin una intervención financiera explícita.
    commission = sale.commission
    if commission and commission.status == "pagada":
        return commission

    deductions = sum((D(d.amount) for d in sale.deductions if d.affects_commission), Decimal("0"))
    full_base = max(Decimal("0"), D(sale.total) - deductions)
    rule = choose_commission_rule(sale, full_base)
    if not rule:
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

    if not commission:
        commission = Commission(sale_id=sale.id, advisor_id=sale.advisor_id)
        db.session.add(commission)
    commission.rule_id = rule.id
    commission.base_amount = base
    commission.percentage = rule.percentage
    commission.minimum_applied = minimum_applied
    commission.amount = amount
    commission.status = status
    return commission


def add_payment(
    sale,
    amount,
    method,
    effective_date=None,
    reference=None,
    status="confirmado",
    notes=None,
    registered_by_id=None,
):
    amount = D(amount)
    if amount <= 0:
        raise ValueError("El monto del pago debe ser mayor que cero.")

    if status == "confirmado" and amount > D(sale.balance):
        raise ValueError(
            f"El pago excede el saldo pendiente de {sale.currency} {D(sale.balance):,.2f}."
        )

    payment = Payment(
        client_id=sale.client_id,
        sale=sale,
        effective_date=effective_date or date.today(),
        amount=amount,
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
        raise ValueError("La cotización no puede convertirse en venta desde su estado actual.")
    if not quote.items:
        raise ValueError("La cotización no contiene ítems para convertir en venta.")
    if any(item.product and item.product.category == "paquete" for item in quote.items):
        raise ValueError(
            "Los paquetes principales se confirman desde Servicio principal / Upgrade "
            "para aplicar el precio fijo y reconocer correctamente cualquier crédito previo."
        )

    client = quote.client
    was_followup = client.record_type == "seguimiento"
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

    for quote_item in quote.items:
        item = SaleItem(
            sale_id=sale.id,
            product_id=quote_item.product_id,
            description=quote_item.description,
            quantity=quote_item.quantity,
            list_price=quote_item.unit_price,
            discount=0,
            unit_price=quote_item.unit_price,
            total=D(quote_item.quantity) * D(quote_item.unit_price),
        )
        db.session.add(item)

    db.session.flush()
    recalc_sale(sale)
    if D(sale.total) <= 0:
        raise ValueError("La cotización debe tener un total mayor que cero.")

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
    client.country = "USA"
    if was_followup:
        audit(
            "convertir_registro_cliente",
            "Client",
            client.id,
            before={"record_type": "seguimiento"},
            after={"record_type": "cliente", "pipeline_stage": "venta_cerrada"},
            reason=f"Cotización convertida en venta {sale.sale_no}",
        )
    quote.status = "aceptada"
    db.session.flush()

    if D(initial_payment) > 0:
        add_payment(sale, initial_payment, payment_method, registered_by_id=user_id)

    generate_operational_work(sale)
    recalc_sale(sale)
    audit("convertir_cotizacion_venta", "Sale", sale.id, after={"sale_no": sale.sale_no, "client": client.business_name})
    return sale



def active_principal_contract(client):
    explicit = (
        ClientContract.query
        .filter_by(client_id=client.id, principal=True, status="activo")
        .order_by(ClientContract.starts_on.desc(), ClientContract.id.desc())
        .first()
    )
    if explicit:
        return explicit

    # Compatibilidad con contratos históricos creados antes de Nexora:
    # el paquete activo más reciente se toma como principal si no existe uno explícito.
    return (
        ClientContract.query
        .join(ProductService, ClientContract.product_id == ProductService.id)
        .filter(
            ClientContract.client_id == client.id,
            ClientContract.status == "activo",
            ProductService.category == "paquete",
        )
        .order_by(ClientContract.starts_on.desc(), ClientContract.id.desc())
        .first()
    )


def principal_paid_credit(contract):
    """Crédito acumulado reconocido al servicio principal vigente."""
    if not contract or not contract.sale_id:
        return Decimal("0")
    sale = db.session.get(Sale, contract.sale_id)
    if not sale:
        return Decimal("0")
    item = next(
        (row for row in sale.items if row.product_id == contract.product_id),
        sale.items[0] if sale.items else None,
    )
    inherited = D(item.discount) if item else Decimal("0")
    paid = D(sale.amount_paid)
    contract_value = D(contract.agreed_price or (contract.product.base_price if contract.product else 0))
    return min(contract_value, inherited + paid)


def _close_replaced_principal(contract):
    if not contract:
        return
    contract.principal = False
    contract.status = "inactivo"
    note = "Sustituido por upgrade de servicio principal."
    contract.notes = f"{contract.notes}\n{note}".strip() if contract.notes else note

    sale = db.session.get(Sale, contract.sale_id) if contract.sale_id else None
    if sale:
        sale.status = "reemplazada"
        sale.balance = Decimal("0")
        sale_note = "Saldo pendiente sustituido por el nuevo plan principal."
        sale.notes = f"{sale.notes}\n{sale_note}".strip() if sale.notes else sale_note
        if sale.receivable:
            # Se conserva la deuda original en la venta y se cierra la cuenta por cobrar
            # sin fingir pagos que nunca existieron. El monto exigible queda igual a lo
            # efectivamente pagado y la nota conserva el total anterior.
            original_total = D(sale.receivable.total_amount)
            actual_paid = D(sale.amount_paid)
            sale.receivable.status = "cancelado"
            sale.receivable.notes = (
                f"{sale.receivable.notes or ''}\n"
                f"Upgrade: cuenta anterior cerrada. Total anterior USD {original_total:,.2f}; "
                f"pagado real USD {actual_paid:,.2f}."
            ).strip()
            sale.receivable.total_amount = actual_paid
            sale.receivable.due_date = None
            sale.receivable.promise_date = None
        for row in getattr(sale, "installments", []) or []:
            if row.status != "pagada":
                row.status = "cancelada"

    for renewal in contract.client.renewals:
        if renewal.contract_id == contract.id and renewal.status not in {"renovado", "cancelado"}:
            renewal.status = "cancelado"


def generate_principal_operational_work(sale, product, previous_contract=None, courtesies=None):
    """Crea el nuevo plan principal y conserva el anterior como historial."""
    if previous_contract:
        _close_replaced_principal(previous_contract)

    for row in ClientContract.query.filter_by(
        client_id=sale.client_id,
        principal=True,
        status="activo",
    ).all():
        row.principal = False
        row.status = "inactivo"

    starts_on = sale.sale_date or date.today()
    duration = product.duration_months or 0
    end_date = starts_on + timedelta(days=duration * 30) if duration else None

    contract = ClientContract(
        client_id=sale.client_id,
        product_id=product.id,
        sale_id=sale.id,
        status="activo",
        starts_on=starts_on,
        ends_on=end_date,
        agreed_price=product.base_price,
        principal=True,
        notes=(
            f"Upgrade desde {previous_contract.product.name}."
            if previous_contract and previous_contract.product
            else "Servicio principal registrado desde Impacto Nexora."
        ),
    )
    db.session.add(contract)
    db.session.flush()
    db.session.add(
        ClientContractDetail(
            contract_id=contract.id,
            modality_snapshot=product.modality,
            maintenance_snapshot=product.maintenance,
            benefits_snapshot=product.components,
            courtesies_snapshot=(courtesies or "").strip() or None,
        )
    )

    if product.renewal_required or end_date:
        db.session.add(
            Renewal(
                client_id=sale.client_id,
                contract=contract,
                renewal_type=product.name,
                due_date=end_date or (starts_on + timedelta(days=365)),
                status="pendiente",
            )
        )

    project = Project(
        project_no=next_code("PRJ", Project),
        client_id=sale.client_id,
        sale_id=sale.id,
        product_id=product.id,
        name=f"{product.name} - {sale.client.business_name}",
        department=product.responsible_area or "Desarrollo",
        status="pendiente_onboarding",
        progress=0,
        starts_on=starts_on,
        due_on=starts_on + timedelta(days=30),
    )
    db.session.add(project)
    db.session.flush()

    template = TaskTemplate.query.filter_by(product_id=product.id, active=True).first()
    if template and template.items:
        for template_item in template.items:
            db.session.add(
                Task(
                    title=template_item.title,
                    description=template_item.description,
                    task_type=template_item.task_type,
                    client_id=sale.client_id,
                    project_id=project.id,
                    priority=template_item.priority,
                    status="pendiente",
                    due_at=datetime.utcnow() + timedelta(days=template_item.due_days or 3),
                    checklist="[ ] Requisito obligatorio" if template_item.required else None,
                )
            )
    else:
        db.session.add(
            Task(
                title=f"Onboarding: {product.name}",
                description="Revisar ficha, beneficios, cortesías, materiales y próximos entregables.",
                task_type="cliente",
                client_id=sale.client_id,
                project_id=project.id,
                priority="alta",
                status="pendiente",
                due_at=datetime.utcnow() + timedelta(days=3),
            )
        )
    return contract


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
        db.session.flush()
        db.session.add(
            ClientContractDetail(
                contract_id=contract.id,
                modality_snapshot=product.modality,
                maintenance_snapshot=product.maintenance,
                benefits_snapshot=product.components,
            )
        )

        if product.renewal_required or end_date:
            renewal_due = end_date or (date.today() + timedelta(days=365))
            db.session.add(
                Renewal(
                    client_id=sale.client_id,
                    contract=contract,
                    renewal_type=product.name,
                    due_date=renewal_due,
                )
            )

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
            db.session.add(
                PrintItem(
                    order_id=order.id,
                    name=item.description,
                    quantity=max(1, int(D(item.quantity))),
                    design_status="pendiente",
                )
            )
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
                for template_item in template.items:
                    db.session.add(
                        Task(
                            title=template_item.title,
                            description=template_item.description,
                            task_type=template_item.task_type,
                            client_id=sale.client_id,
                            project_id=project.id,
                            priority=template_item.priority,
                            status="pendiente",
                            due_at=datetime.utcnow() + timedelta(days=template_item.due_days or 3),
                            checklist="[ ] Requisito obligatorio" if template_item.required else None,
                        )
                    )
            else:
                db.session.add(
                    Task(
                        title=f"Onboarding: {product.name}",
                        description="Recopilar información, accesos, materiales y aprobación inicial del cliente.",
                        task_type="cliente",
                        client_id=sale.client_id,
                        project_id=project.id,
                        priority="alta",
                        status="pendiente",
                        due_at=datetime.utcnow() + timedelta(days=3),
                    )
                )


def update_print_order_status(order):
    items = order.items
    if not items:
        return order.status
    if all(i.qty_received >= i.quantity for i in items):
        order.status = "recibido"
    elif order.incidents and any(i.status == "abierta" for i in order.incidents):
        order.status = "incidencia"
    elif any(s.status in {"en_transito", "parcial"} for s in order.shipments):
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
    for receivable in AccountReceivable.query.filter(AccountReceivable.status != "pagado").all():
        if receivable.due_date and receivable.due_date < today:
            receivable.status = "vencido"


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
                message = f"{renewal.renewal_type} vence en {delta} día(s) ({renewal.due_date})."
                already = any(
                    notification.title == title
                    and notification.message == message
                    and not notification.read
                    for notification in owner.user.notifications
                )
                if not already:
                    notify(
                        owner.user_id,
                        title,
                        message,
                        link=f"/clients/{renewal.client_id}",
                        priority="alta" if delta <= 7 else "normal",
                    )
