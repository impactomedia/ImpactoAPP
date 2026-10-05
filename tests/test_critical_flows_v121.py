from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from app import create_app
from app.critical_audit import build_critical_audit
from app.extensions import db
from app.models import (
    Client,
    Collaborator,
    Payment,
    PrintOrder,
    Project,
    Role,
    Sale,
    Task,
    User,
)
from scripts.seed import seed_all


class TestConfig:
    TESTING = True
    SECRET_KEY = "block14-test"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    WTF_CSRF_TIME_LIMIT = None
    UPLOAD_FOLDER = str(Path(__file__).parent / "uploads_block14")
    BACKUP_DIR = str(Path(__file__).parent / "backups_block14")
    MAX_CONTENT_LENGTH = 10 * 1024 * 1024
    COMPANY_NAME = "Impacto Media Agency"
    COMPANY_TIMEZONE = "America/Managua"
    SMTP_HOST = None
    SMTP_PORT = 587
    SMTP_USER = None
    SMTP_PASSWORD = None
    SMTP_FROM = "test@example.com"
    SMTP_USE_TLS = False
    SESSION_COOKIE_SECURE = False
    REMEMBER_COOKIE_SECURE = False


@pytest.fixture()
def app():
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        seed_all()

        admin_role = Role.query.filter_by(name="admin").one()
        advisor_role = Role.query.filter_by(name="advisor").one()

        admin = User(
            name="Admin B14",
            email="admin-b14@test.local",
            role=admin_role,
            active=True,
        )
        admin.set_password("Test123!")
        db.session.add(admin)
        db.session.flush()

        admin_collaborator = Collaborator(
            user_id=admin.id,
            code="B14-ADM",
            job_title="Administración",
            department="Administración",
            status="activo",
            join_date=date(2026, 1, 1),
        )
        db.session.add(admin_collaborator)

        advisor_a = User(
            name="Asesor A B14",
            email="advisor-a-b14@test.local",
            role=advisor_role,
            active=True,
        )
        advisor_a.set_password("Test123!")
        db.session.add(advisor_a)
        db.session.flush()

        collab_a = Collaborator(
            user_id=advisor_a.id,
            code="B14-A",
            job_title="Asesor",
            department="Ventas",
            status="activo",
            join_date=date(2026, 1, 1),
            vacation_rate=0,
        )
        db.session.add(collab_a)

        advisor_b = User(
            name="Asesor B B14",
            email="advisor-b-b14@test.local",
            role=advisor_role,
            active=True,
        )
        advisor_b.set_password("Test123!")
        db.session.add(advisor_b)
        db.session.flush()

        collab_b = Collaborator(
            user_id=advisor_b.id,
            code="B14-B",
            job_title="Asesor",
            department="Ventas",
            status="activo",
            join_date=date(2026, 1, 1),
            vacation_rate=0,
        )
        db.session.add(collab_b)
        db.session.flush()

        client = Client(
            code="CLI-B14",
            business_name="Cliente B14",
            contact_name="Contacto B14",
            email="cliente-b14@test.local",
            country="USA",
            owner_id=collab_a.id,
            record_type="cliente",
            pipeline_stage="venta_cerrada",
            client_status="activo",
        )
        db.session.add(client)
        db.session.flush()

        sale = Sale(
            sale_no="SALE-B14",
            client_id=client.id,
            advisor_id=collab_a.id,
            sale_date=date(2026, 10, 5),
            status="confirmada",
            currency="USD",
            total=100,
            amount_paid=50,
            balance=50,
        )
        db.session.add(sale)
        db.session.flush()

        payment = Payment(
            client_id=client.id,
            sale_id=sale.id,
            effective_date=date(2026, 10, 5),
            amount=50,
            currency="USD",
            method="efectivo",
            status="confirmado",
            registered_by_id=admin.id,
        )
        db.session.add(payment)

        project = Project(
            project_no="PRJ-B14",
            client_id=client.id,
            sale_id=sale.id,
            name="Proyecto B14",
            status="en_produccion",
            progress=80,
            starts_on=date(2026, 10, 1),
            due_on=date(2026, 10, 31),
        )
        db.session.add(project)
        db.session.flush()

        task = Task(
            title="Requisito obligatorio B14",
            task_type="cliente",
            client_id=client.id,
            project_id=project.id,
            assignee_id=collab_a.id,
            priority="alta",
            status="pendiente",
            checklist="[ ] Requisito obligatorio",
            due_at=datetime(2026, 10, 10, 17, 0),
        )
        db.session.add(task)

        order = PrintOrder(
            order_no="IMP-B14",
            client_id=client.id,
            sale_id=sale.id,
            status="diseno",
            total_sale=100,
        )
        db.session.add(order)

        db.session.commit()

        app.config["B14_IDS"] = {
            "payment_id": payment.id,
            "client_id": client.id,
            "new_owner_id": collab_b.id,
            "project_id": project.id,
            "order_id": order.id,
        }

    yield app


@pytest.fixture()
def client(app):
    return app.test_client()


def _login(client):
    response = client.post(
        "/auth/login",
        data={
            "email": "admin-b14@test.local",
            "password": "Test123!",
        },
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}


def test_reverse_payment_requires_reason(client, app):
    _login(client)
    payment_id = app.config["B14_IDS"]["payment_id"]

    response = client.post(
        f"/sales/payments/{payment_id}/reverse",
        data={},
        follow_redirects=False,
    )
    # Esta regla ya existe en security_controls desde el Bloque 10:
    # una reversión sensible sin motivo se rechaza con HTTP 400.
    assert response.status_code == 400

    with app.app_context():
        payment = db.session.get(Payment, payment_id)
        assert payment.status == "confirmado"


def test_crm_transfer_requires_reason(client, app):
    _login(client)
    ids = app.config["B14_IDS"]

    response = client.post(
        f"/crm/{ids['client_id']}/transfer",
        data={"owner_id": str(ids["new_owner_id"])},
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}

    with app.app_context():
        row = db.session.get(Client, ids["client_id"])
        assert row.owner_id != ids["new_owner_id"]


def test_project_cannot_close_with_required_task_pending(client, app):
    _login(client)
    project_id = app.config["B14_IDS"]["project_id"]

    response = client.post(
        f"/operations/projects/{project_id}/update",
        data={"status": "completado", "progress": "100"},
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}

    with app.app_context():
        project = db.session.get(Project, project_id)
        assert project.status == "en_produccion"


def test_print_shipment_requires_estimated_delivery(client, app):
    _login(client)
    order_id = app.config["B14_IDS"]["order_id"]

    response = client.post(
        f"/printing/{order_id}/shipment",
        data={
            "local_delivery": "on",
            "shipped_at": "2026-10-05T10:00",
        },
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}


def test_quality_center_loads_for_admin(client):
    _login(client)
    response = client.get("/quality/")
    assert response.status_code == 200
    assert b"Auditor" in response.data
    assert b"Flujos Cr" in response.data


def test_audit_detects_hierarchy_warning(app):
    with app.app_context():
        result = build_critical_audit()
        hierarchy = next(
            item
            for section in result["sections"]
            for item in section["items"]
            if item["key"] == "advisor_hierarchy"
        )
        assert hierarchy["count"] >= 2
        assert hierarchy["severity"] == "warning"
