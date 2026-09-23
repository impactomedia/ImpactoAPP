"""Actualiza una instalación existente para comentarios de cliente y proyectos IMPACTO/NOVAX.

Ejecutar una sola vez desde la raíz del proyecto:
    python -m scripts.upgrade_comments_projects
"""
from sqlalchemy import inspect, text

from app import create_app
from app.extensions import db
from app.models import AdvisorProject


app = create_app()

with app.app_context():
    # Crea las tablas nuevas (advisor_projects y client_comments) sin borrar datos existentes.
    db.create_all()

    inspector = inspect(db.engine)
    columns = {c["name"] for c in inspector.get_columns("collaborators")}

    if "advisor_project_id" not in columns:
        db.session.execute(text("ALTER TABLE collaborators ADD COLUMN advisor_project_id INT NULL"))
        db.session.commit()
        print("+ Columna collaborators.advisor_project_id creada")
    else:
        print("= La columna collaborators.advisor_project_id ya existe")

    inspector = inspect(db.engine)
    indexes = {i["name"] for i in inspector.get_indexes("collaborators")}
    if "ix_collaborators_advisor_project_id" not in indexes:
        db.session.execute(text("CREATE INDEX ix_collaborators_advisor_project_id ON collaborators (advisor_project_id)"))
        db.session.commit()
        print("+ Índice de proyecto comercial creado")

    inspector = inspect(db.engine)
    fks = {fk.get("name") for fk in inspector.get_foreign_keys("collaborators")}
    if "fk_collaborators_advisor_project" not in fks:
        try:
            db.session.execute(text(
                "ALTER TABLE collaborators "
                "ADD CONSTRAINT fk_collaborators_advisor_project "
                "FOREIGN KEY (advisor_project_id) REFERENCES advisor_projects(id)"
            ))
            db.session.commit()
            print("+ Relación de proyecto comercial creada")
        except Exception as exc:
            db.session.rollback()
            print(f"! No se agregó la FK automáticamente: {exc}")
            print("  La aplicación puede seguir funcionando; revisa la restricción después si lo deseas.")

    for code, name in (("IMPACTO", "IMPACTO"), ("NOVAX", "NOVAX")):
        row = AdvisorProject.query.filter_by(code=code).first()
        if not row:
            db.session.add(AdvisorProject(code=code, name=name, description=f"Proyecto comercial {name}", active=True))
    db.session.commit()

    print("\nActualización completada.")
    print("- Historial de comentarios por cliente: listo")
    print("- Proyectos de asesores IMPACTO / NOVAX: listos")
    print("- Los asesores existentes quedan sin proyecto hasta que un administrador los asigne.")
