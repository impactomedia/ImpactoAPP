"""Reglas para atribuir leads y calcular incentivos sobre cobros netos.

La comisión no reduce el neto comisionable del asesor.
Se devenga sobre cobros confirmados MENOS gastos ejecutados
de Imprenta/Meta Ads vinculados con ESA VENTA, nunca comisiones de banco.
"""
from decimal import Decimal
from app.extensions import db
from app.models import Payment, Sale
from app.finance_control_models import FinanceProjectMovement
from app.finance_control_service import money, project_of_sale
from app.lead_commission_models import (
    LeadGenerator, ClientLeadOrigin, SaleLeadAttribution, LeadCommissionPayout,
)

GENERATOR_CODES = (
    ("DANIEL", "Daniel", Decimal("4.000")),
    ("OSCAR", "Oscar", Decimal("2.000")),
    ("EMPRESA", "Empresa", Decimal("0.000")),
)


def initialize_generators():
    """Sembrar solo faltantes. No sobrescribe nombres/tarifas existentes."""
    for code, name, percentage in GENERATOR_CODES:
        if not LeadGenerator.query.filter_by(code=code).first():
            db.session.add(LeadGenerator(code=code, name=name, percentage=percentage))
    db.session.flush()


def available_generators():
    initialize_generators()
    return LeadGenerator.query.filter_by(active=True).order_by(LeadGenerator.id).all()


def company_generator():
    initialize_generators()
    return LeadGenerator.query.filter_by(code="EMPRESA").one()


def source_for_client(client):
    if client.lead_origin and client.lead_origin.generator:
        return client.lead_origin.generator
    return company_generator()


def assign_client_origin(client, generator_id, user_id):
    """Se permite actualizar el origen del cliente sin reescribir ventas ya atribuidas."""
    generator = db.session.get(LeadGenerator, generator_id) if generator_id else company_generator()
    if not generator or not generator.active:
        raise ValueError("Selecciona un generador de leads válido.")
    current = client.lead_origin
    if current is None:
        current = ClientLeadOrigin(
            client=client, generator_id=generator.id, assigned_by_id=user_id,
        )
        db.session.add(current)
    else:
        current.generator_id = generator.id
        current.assigned_by_id = user_id
    db.session.flush()
    return generator


def snapshot_at_first_payment(sale):
    """Atribución fija al producirse el primer cobro confirmado de una venta."""
    if sale.lead_attribution is not None:
        return sale.lead_attribution
    origin = source_for_client(sale.client)
    record = SaleLeadAttribution(
        sale=sale, generator_id=origin.id, percentage=origin.percentage,
    )
    db.session.add(record)
    db.session.flush()
    return record


def net_sale_commission(sale):
    """Resultados de venta: bruto cobrado - costos realmente ejecutados de Imprenta/Meta Ads."""
    collected = sum((
        money(p.amount) for p in sale.payments
        if p.status == "confirmado" and p.currency == "USD"
    ), money(0))
    executed = sum((
        money(e.amount) for e in FinanceProjectMovement.query.filter(
            FinanceProjectMovement.sale_id == sale.id,
            FinanceProjectMovement.status == "ejecutado",
            FinanceProjectMovement.category.in_(("imprenta", "meta_ads")),
            FinanceProjectMovement.currency == "USD",
        ).all()
    ), money(0))
    base = max(money(0), collected - executed)
    attribution = sale.lead_attribution
    percentage = Decimal(str(attribution.percentage)) if attribution else Decimal("0")
    commission = money(base * percentage / 100)
    paid = sum((
        money(p.amount) for p in LeadCommissionPayout.query.filter_by(sale_id=sale.id).all()
    ), money(0))
    return {
        "sale": sale, "attribution": attribution,
        "generator": attribution.generator if attribution else None,
        "project_id": project_of_sale(sale),
        "collected": collected, "deductions": executed,
        "base": base, "percentage": percentage, "commission": commission,
        "paid": paid, "available": max(money(0), commission - paid),
    }


def lead_commission_rows():
    """Nunca genera una comisión para cobros sin vínculo de origen."""
    sales = Sale.query.join(SaleLeadAttribution, SaleLeadAttribution.sale_id == Sale.id).order_by(
        Sale.sale_date.desc(), Sale.id.desc()
    ).all()
    return [
        net_sale_commission(sale) for sale in sales
        if sale.status not in {"cancelada", "anulada"}
        and sale.lead_attribution.percentage > 0
    ]
