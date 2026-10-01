"""Actualización Clientes V2 · Fase 3.

Crea el historial de equipo operativo y migra las asignaciones legacy existentes
sin borrar ni alterar datos previos.

Uso temporal recomendado en Railway Pre-Deploy:
    python -m scripts.upgrade_clients_v2_phase3
"""

from app import create_app
from app.extensions import db
from app.client_v2_models import ClientTeamAssignment
from app.client_v3_services import sync_legacy_team_assignments


def upgrade_clients_v2_phase3():
    app = create_app()
    with app.app_context():
        ClientTeamAssignment.__table__.create(bind=db.engine, checkfirst=True)
        before = ClientTeamAssignment.query.count()
        sync_legacy_team_assignments()
        db.session.commit()
        after = ClientTeamAssignment.query.count()
        print(
            "Clientes V2 Fase 3 listo: tabla de equipo verificada y "
            f"{max(0, after - before)} asignación(es) histórica(s) migrada(s)."
        )


if __name__ == "__main__":
    upgrade_clients_v2_phase3()
