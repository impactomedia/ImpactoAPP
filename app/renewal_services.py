from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation

from app.client_v2_models import ClientContractDetail, ClientInstallment, ClientOperationalProfile
from app.extensions import db
from app.helpers import audit, next_code, notify
from app.models import (
    AccountReceivable,
    Client,
    ClientContract,
    Interaction,
    Notification,
    Quote,
    QuoteItem,
    Renewal,
    Sale,
    SaleItem,
    SystemSetting,
    Task,
)
from app.nexora_models import SaleOperationMeta
from app.services import add_payment, recalc_sale


OPEN_RENEWAL_STATUSES = {"pendiente", "contactado"}
FINAL_RENEWAL_STATUSES = {"renovado", "no_renueva", "cancelado"}
DEFAULT_ALERT_DAYS = (60, 30, 15, 7, 0)
MONEY = Decimal("0.01")


def _money(value, default="0"):
    try:
        return Decimal(str(value if value not in (None, "") else default)).quantize(MONEY)
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("El monto indicado no es válido.")


def _append_note(current, line):
    line = (line or "").strip()
    if not line:
        return current
    return f"{current}\n{line}".strip() if current else line


def renewal_alert_days():
    row = SystemSetting.query.filter_by(key="renewal_alert_days").first()
    raw = row.value if row and row.value else ",".join(str(v) for v in DEFAULT_ALERT_DAYS)
    values = []
    for item in str(raw).split(","):
        try:
            number = int(item.strip())
        except (TypeError, ValueError):
            continue
        if number >= 0 and number not in values:
            values.append(number)
    return tuple(sorted(values, reverse=True)) if values else DEFAULT_ALERT_DAYS


def _ensure_contract_renewal(contract):
    product = contract.product
    due_date = contract.ends_on
    if not due_date and product and product.renewal_required and contract.starts_on:
        due_date = contract.starts_on + timedelta(days=365)
    if not due_date:
        return None

    existing = (
        Renewal.query
        .filter_by(contract_id=contract.id)
        .order_by(Renewal.id.desc())
        .first()
    )
    if existing:
        if existing.status in OPEN_RENEWAL_STATUSES:
            existing.due_date = due_date
            if product:
                existing.renewal_type = product.name
        return existing

    row = Renewal(
        client_id=contract.client_id,
        contract_id=contract.id,
        renewal_type=product.name if product else "Servicio",
        due_date=due_date,
        status="pendiente",
    )
    db.session.add(row)
    db.session.flush()
    return row


def _ensure_external_renewal(client_id, renewal_type, due_date):
    if not due_date:
        return None

    exact = (
        Renewal.query
        .filter_by(
            client_id=client_id,
            contract_id=None,
            renewal_type=renewal_type,
            due_date=due_date,
        )
        .order_by(Renewal.id.desc())
        .first()
    )
    if exact:
        return exact

    active = (
        Renewal.query
        .filter(
            Renewal.client_id == client_id,
            Renewal.contract_id.is_(None),
            Renewal.renewal_type == renewal_type,
            Renewal.status.in_(OPEN_RENEWAL_STATUSES),
        )
        .order_by(Renewal.id.desc())
        .first()
    )
    if active:
        active.due_date = due_date
        return active

    row = Renewal(
        client_id=client_id,
        renewal_type=renewal_type,
        due_date=due_date,
        status="pendiente",
    )
    db.session.add(row)
    db.session.flush()
    return row


def sync_renewal_sources():
    """Sincroniza contratos, dominio y hosting con la tabla de renovaciones existente."""
    for contract in (
        ClientContract.query
        .filter(ClientContract.status == "activo")
        .all()
    ):
        product = contract.product
        if contract.ends_on or (product and product.renewal_required):
            _ensure_contract_renewal(contract)

    for profile in ClientOperationalProfile.query.all():
        _ensure_external_renewal(profile.client_id, "Dominio", profile.domain_renews_on)
        _ensure_external_renewal(profile.client_id, "Hosting", profile.hosting_renews_on)


def _notification_exists(user_id, title, message):
    return (
        Notification.query
        .filter_by(user_id=user_id, title=title, message=message, read=False)
        .first()
        is not None
    )


def _ensure_renewal_task(renewal):
    owner = renewal.client.owner
    if not owner or not owner.user or not owner.user.active:
        return None

    marker = f"[RENOVACION:{renewal.id}]"
    existing = (
        Task.query
        .filter(
            Task.client_id == renewal.client_id,
            Task.task_type == "renovacion",
            Task.status.notin_(["completada", "cancelada"]),
            Task.description.like(f"%{marker}%"),
        )
        .first()
    )
    if existing:
        if renewal.due_date:
            existing.due_at = datetime.combine(renewal.due_date, time(hour=17))
        return existing

    days = (renewal.due_date - date.today()).days
    task = Task(
        title=f"Renovación: {renewal.renewal_type} · {renewal.client.business_name}"[:180],
        description=(
            f"{marker}\nGestionar renovación de {renewal.renewal_type}. "
            f"Vencimiento: {renewal.due_date}."
        ),
        task_type="renovacion",
        client_id=renewal.client_id,
        assignee_id=owner.id,
        priority="urgente" if days <= 7 else "alta" if days <= 15 else "media",
        status="pendiente",
        due_at=datetime.combine(renewal.due_date, time(hour=17)),
    )
    db.session.add(task)
    return task


def ensure_renewal_alerts():
    """Crea tareas y alertas sin duplicarlas, usando los días configurados."""
    sync_renewal_sources()
    today = date.today()
    days = renewal_alert_days()
    horizon = max(days) if days else 60

    renewals = Renewal.query.filter(Renewal.status.in_(OPEN_RENEWAL_STATUSES)).all()
    for renewal in renewals:
        if not renewal.due_date:
            continue
        delta = (renewal.due_date - today).days
        if delta <= horizon:
            _ensure_renewal_task(renewal)

        owner = renewal.client.owner
        if not owner or not owner.user or not owner.user.active:
            continue

        if delta in days:
            title = f"Renovación: {renewal.client.business_name}"
            message = f"{renewal.renewal_type} vence en {delta} día(s) ({renewal.due_date})."
        elif delta < 0:
            title = f"Renovación vencida: {renewal.client.business_name}"
            message = f"{renewal.renewal_type} venció el {renewal.due_date}."
        else:
            continue

        if not _notification_exists(owner.user_id, title, message):
            notify(
                owner.user_id,
                title,
                message,
                link="/renewals/",
                priority="alta" if delta <= 7 else "normal",
            )

    _ensure_installment_alerts(days)


def _ensure_installment_alerts(alert_days):
    """Cuotas: aviso previo y aviso de atraso."""
    today = date.today()
    reminder_days = {7, 0}
    reminder_days.update(day for day in alert_days if day <= 7)

    rows = (
        ClientInstallment.query
        .filter(ClientInstallment.status.notin_(["pagada", "cancelada"]))
        .all()
    )
    for installment in rows:
        client = installment.client
        owner = client.owner if client else None
        if not owner or not owner.user or not owner.user.active:
            continue

        delta = (installment.due_date - today).days
        sale_no = installment.sale.sale_no if installment.sale else "Venta"
        if delta in reminder_days:
            title = f"Cuota próxima: {client.business_name}"
            message = (
                f"{sale_no} · cuota #{installment.sequence} vence en {delta} día(s) "
                f"({installment.due_date})."
            )
        elif delta < 0:
            title = f"Cuota atrasada: {client.business_name}"
            message = f"{sale_no} · cuota #{installment.sequence} venció el {installment.due_date}."
        else:
            continue

        if not _notification_exists(owner.user_id, title, message):
            notify(
                owner.user_id,
                title,
                message,
                link=f"/sales/{installment.sale_id}" if installment.sale_id else "/renewals/",
                priority="alta" if delta <= 0 else "normal",
            )


def _close_renewal_tasks(renewal, *, cancelled=False):
    marker = f"[RENOVACION:{renewal.id}]"
    rows = (
        Task.query
        .filter(
            Task.client_id == renewal.client_id,
            Task.task_type == "renovacion",
            Task.status.notin_(["completada", "cancelada"]),
            Task.description.like(f"%{marker}%"),
        )
        .all()
    )
    for task in rows:
        task.status = "cancelada" if cancelled else "completada"
        task.completed_at = None if cancelled else datetime.utcnow()


def renewal_amount(renewal):
    contract = renewal.contract
    if not contract:
        return Decimal("0")
    if contract.agreed_price is not None:
        return Decimal(str(contract.agreed_price)).quantize(MONEY)
    if contract.product and contract.product.base_price is not None:
        return Decimal(str(contract.product.base_price)).quantize(MONEY)
    return Decimal("0")


def record_renewal_contact(
    renewal,
    *,
    user_id,
    channel="llamada",
    notes="",
    result="",
    next_followup_at=None,
):
    channel = (channel or "llamada").strip()[:40]
    notes = (notes or "").strip()
    result = (result or "").strip()[:180]

    interaction = Interaction(
        client_id=renewal.client_id,
        user_id=user_id,
        interaction_type=channel,
        occurred_at=datetime.utcnow(),
        subject=f"Renovación: {renewal.renewal_type}"[:180],
        notes=notes or "Contacto de renovación registrado.",
        result=result or "Seguimiento de renovación",
        next_followup_at=next_followup_at,
    )
    db.session.add(interaction)

    renewal.last_contact_at = interaction.occurred_at
    if renewal.status == "pendiente":
        renewal.status = "contactado"
    if notes:
        renewal.notes = _append_note(
            renewal.notes,
            f"{interaction.occurred_at:%Y-%m-%d %H:%M} · {notes}",
        )
    audit(
        "contacto_renovacion",
        "Renewal",
        renewal.id,
        after={"channel": channel, "result": result, "next_followup_at": next_followup_at},
    )
    return interaction


def create_renewal_quote(renewal, *, amount=None):
    amount_value = _money(amount if amount not in (None, "") else renewal_amount(renewal))
    if amount_value <= 0:
        raise ValueError("Indica un monto mayor que cero para generar la cotización de renovación.")

    product = renewal.contract.product if renewal.contract and renewal.contract.product else None
    marker = f"[RENOVACION:{renewal.id}]"
    notes = f"{marker} Cotización generada desde el Centro de Renovaciones."

    quote = Quote(
        quote_no=next_code("COT", Quote),
        client_id=renewal.client_id,
        advisor_id=renewal.client.owner_id,
        status="borrador",
        currency="USD",
        subtotal=amount_value,
        discount=Decimal("0"),
        total=amount_value,
        valid_until=date.today() + timedelta(days=15),
        notes=notes,
    )
    db.session.add(quote)
    db.session.flush()

    db.session.add(
        QuoteItem(
            quote_id=quote.id,
            product_id=product.id if product else None,
            description=f"Renovación de {renewal.renewal_type}",
            quantity=Decimal("1"),
            unit_price=amount_value,
            total=amount_value,
        )
    )
    audit(
        "crear_cotizacion_renovacion",
        "Quote",
        quote.id,
        after={"renewal_id": renewal.id, "amount": str(amount_value)},
    )
    return quote


def _next_contract_period(contract):
    today = date.today()
    product = contract.product
    starts_on = (
        contract.ends_on + timedelta(days=1)
        if contract.ends_on and contract.ends_on >= today
        else today
    )

    if product and product.duration_months:
        ends_on = starts_on + timedelta(days=product.duration_months * 30)
    elif contract.starts_on and contract.ends_on:
        span = max(1, (contract.ends_on - contract.starts_on).days)
        ends_on = starts_on + timedelta(days=span)
    elif product and product.renewal_required:
        ends_on = starts_on + timedelta(days=365)
    else:
        ends_on = None

    return starts_on, ends_on


def _create_renewal_sale(
    renewal,
    *,
    amount,
    initial_payment,
    payment_method,
    balance_due,
    user_id,
    description,
    product_id=None,
    operation_type="service_renewal",
):
    total = _money(amount)
    initial = _money(initial_payment)
    if total <= 0:
        raise ValueError("El monto de renovación debe ser mayor que cero.")
    if initial < 0 or initial > total:
        raise ValueError("El pago inicial no puede ser negativo ni superar el total.")

    sale = Sale(
        sale_no=next_code("VEN", Sale),
        client_id=renewal.client_id,
        advisor_id=renewal.client.owner_id,
        sale_date=date.today(),
        status="confirmada",
        currency="USD",
        total=total,
        amount_paid=Decimal("0"),
        balance=total,
        notes=f"Renovación de {renewal.renewal_type}.",
    )
    db.session.add(sale)
    db.session.flush()

    db.session.add(
        SaleItem(
            sale_id=sale.id,
            product_id=product_id,
            description=description,
            quantity=Decimal("1"),
            list_price=total,
            discount=Decimal("0"),
            unit_price=total,
            total=total,
        )
    )

    outstanding = total - initial
    receivable = AccountReceivable(
        client_id=renewal.client_id,
        sale_id=sale.id,
        total_amount=total,
        paid_amount=Decimal("0"),
        due_date=balance_due if outstanding > 0 else None,
        status="al_dia",
        notes="Saldo generado por renovación.",
    )
    db.session.add(receivable)

    payment_mode = "contado" if initial >= total else ("entrega" if balance_due else "financiamiento")
    db.session.add(
        SaleOperationMeta(
            sale_id=sale.id,
            operation_type=operation_type,
            payment_mode=payment_mode,
            payment_terms="Renovación generada desde el Centro de Renovaciones.",
            catalog_total=total,
            credit_applied=Decimal("0"),
            created_by_id=user_id,
        )
    )

    if initial > 0:
        add_payment(
            sale,
            initial,
            payment_method or "transferencia",
            effective_date=date.today(),
            notes="Pago inicial de renovación.",
            registered_by_id=user_id,
        )
    recalc_sale(sale)
    return sale


def complete_contract_renewal(
    renewal,
    *,
    amount=None,
    initial_payment=0,
    payment_method="transferencia",
    balance_due=None,
    user_id=None,
):
    if renewal.status in FINAL_RENEWAL_STATUSES:
        raise ValueError("Esta renovación ya fue cerrada.")
    contract = renewal.contract
    if not contract or not contract.product:
        raise ValueError("Esta renovación no está vinculada a un contrato renovable.")

    price = amount if amount not in (None, "") else renewal_amount(renewal)
    was_principal = bool(contract.principal or contract.product.category == "paquete")

    sale = _create_renewal_sale(
        renewal,
        amount=price,
        initial_payment=initial_payment,
        payment_method=payment_method,
        balance_due=balance_due,
        user_id=user_id,
        description=f"Renovación de {contract.product.name}",
        product_id=contract.product_id,
        operation_type="principal_renewal" if was_principal else "service_renewal",
    )

    starts_on, ends_on = _next_contract_period(contract)

    if was_principal:
        for row in ClientContract.query.filter_by(
            client_id=renewal.client_id,
            principal=True,
            status="activo",
        ).all():
            row.principal = False
            row.status = "inactivo"

    contract.status = "inactivo"
    contract.principal = False
    contract.notes = _append_note(
        contract.notes,
        f"Período renovado mediante {sale.sale_no}.",
    )

    new_contract = ClientContract(
        client_id=renewal.client_id,
        product_id=contract.product_id,
        sale_id=sale.id,
        status="activo",
        starts_on=starts_on,
        ends_on=ends_on,
        agreed_price=_money(price),
        principal=was_principal,
        notes=f"Renovación del contrato #{contract.id}.",
    )
    db.session.add(new_contract)
    db.session.flush()

    if contract.v2_detail:
        detail = contract.v2_detail
        db.session.add(
            ClientContractDetail(
                contract_id=new_contract.id,
                modality_snapshot=detail.modality_snapshot,
                maintenance_snapshot=detail.maintenance_snapshot,
                benefits_snapshot=detail.benefits_snapshot,
                courtesies_snapshot=detail.courtesies_snapshot,
                status_reason=None,
            )
        )

    next_due = ends_on or (
        starts_on + timedelta(days=365)
        if contract.product.renewal_required
        else None
    )
    if next_due:
        db.session.add(
            Renewal(
                client_id=renewal.client_id,
                contract_id=new_contract.id,
                renewal_type=contract.product.name,
                due_date=next_due,
                status="pendiente",
            )
        )

    renewal.status = "renovado"
    renewal.last_contact_at = datetime.utcnow()
    _close_renewal_tasks(renewal)
    renewal.notes = _append_note(
        renewal.notes,
        f"Renovado mediante {sale.sale_no}. Nuevo período: {starts_on} a {ends_on or 'sin vencimiento'}.",
    )
    renewal.client.client_status = "activo"

    marker = f"[RENOVACION:{renewal.id}]"
    quote = (
        Quote.query
        .filter(Quote.client_id == renewal.client_id, Quote.notes.like(f"%{marker}%"))
        .order_by(Quote.id.desc())
        .first()
    )
    if quote:
        quote.status = "aceptada"

    audit(
        "confirmar_renovacion",
        "Renewal",
        renewal.id,
        after={
            "sale_id": sale.id,
            "new_contract_id": new_contract.id,
            "starts_on": starts_on,
            "ends_on": ends_on,
        },
    )
    return sale, new_contract


def complete_external_renewal(
    renewal,
    *,
    next_due_date,
    amount=0,
    initial_payment=0,
    payment_method="transferencia",
    balance_due=None,
    user_id=None,
):
    if renewal.status in FINAL_RENEWAL_STATUSES:
        raise ValueError("Esta renovación ya fue cerrada.")
    if renewal.contract_id:
        raise ValueError("Esta renovación debe procesarse como contrato.")
    if renewal.renewal_type not in {"Dominio", "Hosting"}:
        raise ValueError("El vencimiento externo no es compatible con este flujo.")
    if not next_due_date or next_due_date <= renewal.due_date:
        raise ValueError("La próxima fecha debe ser posterior al vencimiento actual.")

    profile = ClientOperationalProfile.query.filter_by(client_id=renewal.client_id).first()
    if not profile:
        profile = ClientOperationalProfile(client_id=renewal.client_id)
        db.session.add(profile)
        db.session.flush()

    if renewal.renewal_type == "Dominio":
        profile.domain_renews_on = next_due_date
    else:
        profile.hosting_renews_on = next_due_date

    total = _money(amount)
    sale = None
    if total > 0:
        sale = _create_renewal_sale(
            renewal,
            amount=total,
            initial_payment=initial_payment,
            payment_method=payment_method,
            balance_due=balance_due,
            user_id=user_id,
            description=f"Renovación de {renewal.renewal_type}",
            product_id=None,
            operation_type="service_renewal",
        )

    renewal.status = "renovado"
    renewal.last_contact_at = datetime.utcnow()
    _close_renewal_tasks(renewal)
    renewal.notes = _append_note(
        renewal.notes,
        (
            f"Renovado hasta {next_due_date}."
            + (f" Venta {sale.sale_no}." if sale else "")
        ),
    )
    db.session.add(
        Renewal(
            client_id=renewal.client_id,
            renewal_type=renewal.renewal_type,
            due_date=next_due_date,
            status="pendiente",
        )
    )
    audit(
        "confirmar_renovacion_externa",
        "Renewal",
        renewal.id,
        after={"next_due_date": next_due_date, "sale_id": sale.id if sale else None},
    )
    return sale


def decline_renewal(renewal, *, reason=""):
    if renewal.status in {"renovado", "cancelado"}:
        raise ValueError("Esta renovación ya fue cerrada.")
    reason = (reason or "").strip()
    renewal.status = "no_renueva"
    renewal.last_contact_at = datetime.utcnow()
    _close_renewal_tasks(renewal, cancelled=True)
    renewal.notes = _append_note(
        renewal.notes,
        f"No renueva. Motivo: {reason or 'No especificado'}.",
    )
    if renewal.contract and renewal.due_date <= date.today():
        renewal.contract.status = "caducado"
        renewal.contract.principal = False
    audit(
        "no_renueva",
        "Renewal",
        renewal.id,
        after={"reason": reason},
    )
