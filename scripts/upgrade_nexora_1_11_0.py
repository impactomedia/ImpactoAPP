"""Impacto Nexora v1.11.0 · Bloque 4.

Amplía la ficha operativa del cliente con datos manuales útiles para USA,
dominio y hosting, sin almacenar contraseñas.

Uso temporal recomendado en Railway Pre-Deploy:
    python -m scripts.upgrade_nexora_1_11_0
"""

from sqlalchemy import inspect, text

from app import create_app
from app.extensions import db
from app.client_v2_models import ClientOperationalProfile


COLUMNS = {
    "whatsapp_phone": "VARCHAR(60)",
    "postal_code": "VARCHAR(20)",
    "domain_name": "VARCHAR(255)",
    "domain_provider": "VARCHAR(120)",
    "hosting_provider": "VARCHAR(120)",
    "hosting_account_email": "VARCHAR(190)",
}


def upgrade_nexora_1_11_0():
    app = create_app()
    with app.app_context():
        ClientOperationalProfile.__table__.create(
            bind=db.engine,
            checkfirst=True,
        )

        inspector = inspect(db.engine)
        existing = {
            column["name"]
            for column in inspector.get_columns(
                ClientOperationalProfile.__tablename__
            )
        }

        added = []
        for name, sql_type in COLUMNS.items():
            if name in existing:
                continue
            db.session.execute(
                text(
                    f"ALTER TABLE {ClientOperationalProfile.__tablename__} "
                    f"ADD COLUMN {name} {sql_type}"
                )
            )
            added.append(name)

        db.session.commit()
        print(
            "Impacto Nexora 1.11.0 listo: "
            f"{len(added)} columna(s) agregada(s): "
            + (", ".join(added) if added else "ninguna; esquema ya actualizado")
        )


if __name__ == "__main__":
    upgrade_nexora_1_11_0()
