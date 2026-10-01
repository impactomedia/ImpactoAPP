"""Actualiza Clientes V2 a Fase 2 sin borrar información.

Ejecución única recomendada en Railway Pre-Deploy:
    python -m scripts.upgrade_clients_v2_phase2

El script es idempotente: puede ejecutarse nuevamente de forma segura.
"""

from app import create_app
from app.extensions import db
from app.models import Attachment, Client
from app.client_v2_models import (
    ClientCollectionNote,
    ClientDocumentMeta,
    ClientInstallment,
)


def upgrade_clients_v2_phase2():
    app = create_app()
    with app.app_context():
        for table in [
            ClientInstallment.__table__,
            ClientCollectionNote.__table__,
            ClientDocumentMeta.__table__,
        ]:
            table.create(bind=db.engine, checkfirst=True)

        metadata_created = 0
        client_files = Attachment.query.filter_by(entity_type="Client").all()
        skipped_orphans = 0
        for attachment in client_files:
            if ClientDocumentMeta.query.filter_by(attachment_id=attachment.id).first():
                continue
            if db.session.get(Client, attachment.entity_id) is None:
                skipped_orphans += 1
                continue
            db.session.add(
                ClientDocumentMeta(
                    client_id=attachment.entity_id,
                    attachment_id=attachment.id,
                    category="otros",
                    description=None,
                )
            )
            metadata_created += 1

        db.session.commit()
        print(
            "Clientes V2 Fase 2 listo: tablas verificadas y "
            f"{metadata_created} archivo(s) existente(s) categorizados como Otros; "
            f"{skipped_orphans} huérfano(s) omitidos."
        )


if __name__ == "__main__":
    upgrade_clients_v2_phase2()
