"""Create the database schema and seed mandatory catalogs/roles.
Use on a fresh database. For future schema changes use Flask-Migrate.
"""
from scripts.seed import seed_all
from app import create_app
from app.extensions import db

app = create_app()
with app.app_context():
    db.create_all()
    seed_all()
    from app.plan_benefit_tasks import sync_existing_plan_benefits
    imported_benefits = sync_existing_plan_benefits()
    db.session.commit()
    print(f"Base de datos inicializada; {imported_benefits} entregables nuevos.")
