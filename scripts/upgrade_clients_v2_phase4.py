"""Clientes V2 · Fase 4.

Crea la tabla que conserva vistas previas saneadas de importación.
No importa datos automáticamente durante el despliegue.

Uso temporal recomendado en Railway Pre-Deploy:
    python -m scripts.upgrade_clients_v2_phase4
"""

from app import create_app
from app.extensions import db
from app.client_v2_models import ClientImportBatch


def upgrade_clients_v2_phase4():
    app = create_app()
    with app.app_context():
        ClientImportBatch.__table__.create(
            bind=db.engine,
            checkfirst=True,
        )
        print(
            "Clientes V2 Fase 4 listo: "
            "tabla de lotes de importación verificada."
        )


if __name__ == "__main__":
    upgrade_clients_v2_phase4()
