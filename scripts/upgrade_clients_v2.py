"""Crea las tablas de Clientes V2 y prepara datos existentes sin borrar información.

Ejecución única recomendada en Railway Pre-Deploy:
    python -m scripts.upgrade_clients_v2

El script es idempotente: puede ejecutarse nuevamente de forma segura.
"""

from app import create_app
from app.extensions import db
from app.models import ClientContract
from app.client_v2_models import (
    ClientOperationalProfile,
    ClientPlatform,
    ClientContractDetail,
)


def upgrade_clients_v2():
    app = create_app()
    with app.app_context():
        tables = [
            ClientOperationalProfile.__table__,
            ClientPlatform.__table__,
            ClientContractDetail.__table__,
        ]
        for table in tables:
            table.create(bind=db.engine, checkfirst=True)

        created_details = 0
        for contract in ClientContract.query.all():
            if ClientContractDetail.query.filter_by(contract_id=contract.id).first():
                continue
            product = contract.product
            db.session.add(
                ClientContractDetail(
                    contract_id=contract.id,
                    modality_snapshot=product.modality if product else None,
                    maintenance_snapshot=product.maintenance if product else None,
                    benefits_snapshot=product.components if product else None,
                )
            )
            created_details += 1

        db.session.commit()
        print(
            "Clientes V2 listo: tablas verificadas y "
            f"{created_details} detalle(s) histórico(s) de contrato creados."
        )


if __name__ == "__main__":
    upgrade_clients_v2()
