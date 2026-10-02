"""Impacto Nexora v1.10.0 · Bloque 3.

Crea la metadata comercial de ventas para diferenciar servicio principal,
upgrade y compra adicional, además de la modalidad de pago.

Uso temporal recomendado en Railway Pre-Deploy:
    python -m scripts.upgrade_nexora_1_10_0
"""

from decimal import Decimal

from app import create_app
from app.extensions import db
from app.models import ClientContract, ProductService, Sale
from app.nexora_models import SaleOperationMeta


def D(value):
    if value is None or value == "":
        return Decimal("0")
    return Decimal(str(value))


def _infer_payment_mode(sale):
    if D(sale.total) > 0 and D(sale.amount_paid) >= D(sale.total):
        return "contado"

    installments = list(getattr(sale, "installments", []) or [])
    if len(installments) > 1:
        return "cuotas"
    if len(installments) == 1:
        return "entrega"
    if D(sale.balance) > 0:
        return "financiamiento"
    return "contado"


def _principal_contract_for_sale(sale):
    return (
        ClientContract.query
        .join(ProductService, ClientContract.product_id == ProductService.id)
        .filter(
            ClientContract.sale_id == sale.id,
            ProductService.category == "paquete",
        )
        .order_by(ClientContract.id.desc())
        .first()
    )


def upgrade_nexora_1_10_0():
    app = create_app()
    with app.app_context():
        SaleOperationMeta.__table__.create(bind=db.engine, checkfirst=True)

        created = 0
        for sale in Sale.query.order_by(Sale.id).all():
            if SaleOperationMeta.query.filter_by(sale_id=sale.id).first():
                continue

            contract = _principal_contract_for_sale(sale)
            if contract:
                item = next(
                    (row for row in sale.items if row.product_id == contract.product_id),
                    sale.items[0] if sale.items else None,
                )
                credit = D(item.discount) if item else Decimal("0")
                catalog = (
                    D(contract.agreed_price)
                    if contract.agreed_price is not None
                    else D(item.list_price if item else sale.total)
                )
                operation_type = (
                    "principal_upgrade"
                    if credit > 0 or (sale.notes or "").lower().startswith("upgrade")
                    else "principal_service"
                )
            else:
                operation_type = "additional_purchase"
                catalog = D(sale.total)
                credit = Decimal("0")

            db.session.add(
                SaleOperationMeta(
                    sale_id=sale.id,
                    operation_type=operation_type,
                    payment_mode=_infer_payment_mode(sale),
                    catalog_total=catalog,
                    credit_applied=credit,
                )
            )
            created += 1

        db.session.commit()
        print(
            "Impacto Nexora 1.10.0 listo: "
            f"sale_operation_meta verificada; {created} venta(s) histórica(s) clasificadas."
        )


if __name__ == "__main__":
    upgrade_nexora_1_10_0()
