"""Escala oficial de comisiones quincenales (vigente desde octubre de 2026).

El rango se elige por cobros personales confirmados en USD durante la quincena.
El porcentaje se aplica a la base neta positiva (cobros menos deducciones
ejecutadas que correspondan). El bono no se deduce de esa base.
Un supervisor recibe solo 15 % de sus cobros personales netos, sin bono.
Las comisiones de captación de leads se contabilizan por separado.
"""
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

POLICY_START = date(2026, 10, 1)
SUPERVISOR_RATE = Decimal("15")
ADVISOR_TIERS = (
    (Decimal("5500"), Decimal("15"), Decimal("100")),
    (Decimal("4000"), Decimal("14"), Decimal("50")),
    (Decimal("3000"), Decimal("13"), Decimal("0")),
    (Decimal("2000"), Decimal("12"), Decimal("0")),
    (Decimal("0"), Decimal("11"), Decimal("0")),
)


def rate_and_bonus(role_name, gross):
    """(porcentaje, bono fijo) según cobros de la quincena."""
    gross = Decimal(str(gross or 0))
    if role_name == "supervisor":
        return SUPERVISOR_RATE, Decimal("0")
    if role_name != "advisor":
        return Decimal("0"), Decimal("0")
    for threshold, percentage, bonus in ADVISOR_TIERS:
        if gross >= threshold:
            return percentage, bonus
    return Decimal("11"), Decimal("0")


def commission_amount(role_name, gross, net):
    """Desglose de comisión, sin alterar ingresos ni pago de generadores."""
    rate, bonus = rate_and_bonus(role_name, gross)
    base = max(Decimal("0"), Decimal(str(net or 0)))
    if base == 0 or Decimal(str(gross or 0)) <= 0:
        bonus = Decimal("0")
    value = (base * rate / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return {
        "percentage": rate,
        "base_commission": value,
        "bonus": bonus,
        "earned": value + bonus,
    }


def commission_payment_date(period_start):
    """1–15: día 20 del mismo mes; 16–fin: día 5 del siguiente mes."""
    if period_start.day <= 15:
        return date(period_start.year, period_start.month, 20)
    year = period_start.year + (period_start.month // 12)
    month = (period_start.month % 12) + 1
    return date(year, month, 5)
