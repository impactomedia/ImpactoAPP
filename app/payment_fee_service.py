"""Comisiones de procesamiento: costes empresariales, nunca del asesor."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from app.extensions import db
from app.payment_fee_models import PaymentFeeRule, PaymentProcessingFee


METHODS = {
    "tarjeta": "Tarjeta",
    "Zelle": "Zelle",
    "transferencia": "Transferencia",
    "ACH": "ACH",
    "Wise": "Wise",
    "efectivo": "Efectivo",
    "otro": "Otro",
}


def normalize_method(method):
    raw = (method or "").strip()
    for key in METHODS:
        if raw.casefold() == key.casefold():
            return key
    return "otro"


def usd_money(value):
    return Decimal(str(value or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def compute_processing_fee(amount, percentage, fixed_amount):
    base = usd_money(amount)
    return usd_money(base * Decimal(str(percentage or 0)) / Decimal("100") + Decimal(str(fixed_amount or 0)))


def record_processing_fee(payment):
    """Graba tasa de la fecha del cobro una sola vez, en la misma transacción.

    Si no existe configuración guarda una instantánea de tasa cero:
    nunca se aplican tarifas nuevas retroactivamente a cobros existentes.
    """
    if payment.status != "confirmado" or payment.currency != "USD":
        return None
    if not payment.id:
        db.session.flush()
    existing = PaymentProcessingFee.query.filter_by(payment_id=payment.id).first()
    if existing is not None:
        return existing
    key = normalize_method(payment.method)
    rule = PaymentFeeRule.query.filter_by(method_key=key).first()
    percentage = Decimal(str(rule.percentage)) if rule else Decimal("0")
    fixed = usd_money(rule.fixed_amount) if rule else usd_money(0)
    fee = PaymentProcessingFee(
        payment_id=payment.id, method_key=key,
        percentage=percentage, fixed_amount=fixed,
        amount=compute_processing_fee(payment.amount, percentage, fixed),
    )
    db.session.add(fee)
    return fee


def valid_rule_amount(raw, maximum=None):
    try:
        value = Decimal(str(raw))
        if not value.is_finite() or value < 0 or (maximum is not None and value > maximum):
            raise ValueError("Monto o porcentaje fuera de rango.")
        return value
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("Indica un número positivo válido.")
