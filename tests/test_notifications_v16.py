from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app import create_app
from app.extensions import db
from app.models import (
    AccountReceivable,
    Client,
    Collaborator,
    Interaction,
    LeaveRequest,
    Notification,
    PrintItem,
    PrintOrder,
    Project,
    Role,
    Sale,
    SupportTicket,
    Task,
    User,
)
from scripts.seed import seed_all


class TestConfig:
    TESTING = True
    SECRET_KEY = "block9-test"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    WTF_CSRF_TIME_LIMIT = None
    UPLOAD_FOLDER = str(Path(__file__).parent / "uploads_block9")
    MAX_CONTENT_LENGTH = 10 * 1024 * 1024
    COMPANY_NAME = "Impacto Media Agency"
    COMPANY_TIMEZONE = "America/Managua"
    SMTP_HOST = None
    SMTP_PORT = 587
    SMTP_USER = None
    SMTP_PASSWORD = None
    SMTP_FROM = "test@example.com"
    SMTP_USE_TLS = False


@pytest.fixture()
def app():
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        seed_all()

        _, admin = _create_user("admin", "admin-b9@test.local", "Admin B9", "Administración")
        _, supervisor = _create_user("supervisor", "supervisor-b9@test.local", "Supervisor B9", "Ventas")
        advisor_user, advisor = _create_user("advisor", "advisor-b9@test.local", "Asesor B9", "Ventas")
        production_user, production = _create_user("production", "production-b9@test.local", "Producción B9", "Desarrollo")

        advisor.supervisor_id = supervisor.id
        production.supervisor_id = supervisor.id

        client = Client(
            code="B9-CLI",
            business_name="Cliente Alertas",
            contact_name="Contacto",
            owner_id=advisor.id,
            record_type="cliente",
            pipeline_stage="venta_cerrada",
            client_status="activo",
            country="USA",
        )
        prospect = Client(
            code="B9-SEG",
            business_name="Prospecto Seguimiento",
            contact_name="Contacto",
            owner_id=advisor.id,
            record_type="seguimiento",
            pipeline_stage="interesado",
            client_status="activo",
            country="USA",
        )
        db.session.add_all([client, prospect])
        db.session.flush()

        db.session.add(
            Interaction(
                client_id=prospect.id,
                user_id=advisor_user.id,
                interaction_type="whatsapp",
                occurred_at=datetime.utcnow() - timedelta(days=2),
                subject="Seguimiento comercial",
                notes="Llamar nuevamente",
                next_followup_at=datetime.utcnow() - timedelta(days=1),
            )
        )

        sale = Sale(
            sale_no="B9-VEN-001",
            client_id=client.id,
            advisor_id=advisor.id,
            sale_date=date.today(),
            status="confirmada",
            currency="USD",
            total=Decimal("1000"),
            amount_paid=Decimal("200"),
            balance=Decimal("800"),
        )
        db.session.add(sale)
        db.session.flush()

        db.session.add(
            AccountReceivable(
                client_id=client.id,
                sale_id=sale.id,
                total_amount=Decimal("1000"),
                paid_amount=Decimal("200"),
                due_date=date.today() - timedelta(days=1),
                status="vencido",
            )
        )

        project = Project(
            project_no="B9-PRJ-001",
            client_id=client.id,
            sale_id=sale.id,
            name="Proyecto bloqueado B9",
            department="Desarrollo",
            status="esperando_cliente",
            progress=40,
            coordinator_id=production.id,
        )
        db.session.add(project)
        db.session.flush()

        db.session.add(
            Task(
                title="Tarea vencida B9",
                client_id=client.id,
                project_id=project.id,
                assignee_id=production.id,
                priority="urgente",
                status="en_proceso",
                due_at=datetime.utcnow() - timedelta(hours=2),
            )
        )

        db.session.add(
            LeaveRequest(
                collaborator_id=production.id,
                leave_type="vacaciones",
                start_date=date.today() + timedelta(days=10),
                end_date=date.today() + timedelta(days=12),
                days=Decimal("3"),
                status="pendiente",
            )
        )

        db.session.add(
            SupportTicket(
                ticket_no="B9-TKT-001",
                client_id=client.id,
                ticket_type="Soporte",
                priority="urgente",
                responsible_id=production.id,
                status="en_proceso",
                subject="Ticket crítico B9",
                description="Requiere atención.",
            )
        )

        order = PrintOrder(
            order_no="B9-IMP-001",
            client_id=client.id,
            responsible_id=production.id,
            provider="Proveedor B9",
            status="diseno",
        )
        db.session.add(order)
        db.session.flush()
        db.session.add(
            PrintItem(
                order_id=order.id,
                name="Business Cards",
                quantity=500,
                design_status="revision",
            )
        )

        db.session.commit()

    yield app


@pytest.fixture()
def client(app):
    return app.test_client()


def _create_user(role_name, email, name, department):
    role = Role.query.filter_by(name=role_name).one()
    user = User(name=name, email=email, role=role, active=True)
    user.set_password("Test123!")
    db.session.add(user)
    db.session.flush()

    collaborator = Collaborator(
        user_id=user.id,
        code=f"B9-{role_name.upper()}-{user.id}",
        job_title=name,
        department=department,
        status="activo",
        join_date=date.today(),
    )
    db.session.add(collaborator)
    db.session.flush()
    return user, collaborator


def _login(client, email):
    response = client.post(
        "/auth/login",
        data={"email": email, "password": "Test123!"},
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}


def test_advisor_center_generates_commercial_and_payment_alerts_without_duplicates(client, app):
    _login(client, "advisor-b9@test.local")

    first = client.get("/notifications/")
    assert first.status_code == 200
    assert b"Centro de Notificaciones" in first.data
    assert b"Seguimiento vencido" in first.data
    assert b"Pago vencido" in first.data

    with app.app_context():
        advisor = User.query.filter_by(email="advisor-b9@test.local").one()
        count_before = Notification.query.filter_by(user_id=advisor.id).count()

    second = client.get("/notifications/")
    assert second.status_code == 200

    with app.app_context():
        advisor = User.query.filter_by(email="advisor-b9@test.local").one()
        count_after = Notification.query.filter_by(user_id=advisor.id).count()
        assert count_after == count_before


def test_production_receives_operational_alerts_but_not_financial_alerts(client):
    _login(client, "production-b9@test.local")
    response = client.get("/notifications/")
    assert response.status_code == 200
    assert b"Tarea vencida B9" in response.data
    assert b"Ticket urgente" in response.data
    assert b"Proyecto bloqueado" in response.data
    assert b"Imprenta pendiente de aprobaci" in response.data
    assert b"Pago vencido" not in response.data


def test_user_can_mark_notification_read_and_unread(client, app):
    _login(client, "advisor-b9@test.local")
    client.get("/notifications/")

    with app.app_context():
        advisor = User.query.filter_by(email="advisor-b9@test.local").one()
        row = Notification.query.filter_by(user_id=advisor.id).first()
        notification_id = row.id
        assert row.read is False

    response = client.post(f"/notifications/{notification_id}/toggle", follow_redirects=False)
    assert response.status_code in {302, 303}
    with app.app_context():
        assert db.session.get(Notification, notification_id).read is True

    client.post(f"/notifications/{notification_id}/toggle")
    with app.app_context():
        assert db.session.get(Notification, notification_id).read is False


def test_user_cannot_modify_another_users_notification(client, app):
    _login(client, "admin-b9@test.local")
    client.get("/notifications/")

    with app.app_context():
        admin = User.query.filter_by(email="admin-b9@test.local").one()
        row = Notification.query.filter_by(user_id=admin.id).first()
        assert row is not None
        notification_id = row.id

    client.post("/auth/logout")
    _login(client, "advisor-b9@test.local")
    response = client.post(f"/notifications/{notification_id}/toggle", follow_redirects=False)
    assert response.status_code == 403


def test_preferences_disable_new_task_alerts_and_keep_history(client, app):
    _login(client, "production-b9@test.local")

    response = client.post(
        "/notifications/preferences",
        data={
            "category": [
                "commercial",
                "payments",
                "sales",
                "hr",
                "renewals",
                "support",
                "projects",
                "printing",
                "general",
            ]
        },
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}

    client.get("/notifications/")

    with app.app_context():
        production = User.query.filter_by(email="production-b9@test.local").one()
        task_alerts = Notification.query.filter(
            Notification.user_id == production.id,
            Notification.title.like("Tarea%"),
        ).count()
        assert task_alerts == 0

        # Historial manual anterior no se borra al cambiar preferencias.
        manual = Notification(
            user_id=production.id,
            title="Tarea histórica",
            message="Historial conservado.",
            link="/operations/tasks",
            priority="normal",
            read=True,
        )
        db.session.add(manual)
        db.session.commit()
        manual_id = manual.id

    client.post(
        "/notifications/preferences",
        data={"category": ["general"]},
    )

    with app.app_context():
        assert db.session.get(Notification, manual_id) is not None


def test_admin_can_change_role_default_preferences(client):
    _login(client, "admin-b9@test.local")
    response = client.post(
        "/notifications/role-preferences",
        data={
            "role_name": "production",
            "category": ["tasks", "projects", "support", "printing", "hr", "general"],
        },
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}

    response = client.get("/notifications/role-preferences/production")
    assert response.status_code == 200
    data = response.get_json()
    assert data["preferences"]["tasks"] is True
    assert data["preferences"]["payments"] is False


def test_filters_by_priority_and_state(client):
    _login(client, "production-b9@test.local")
    client.get("/notifications/")

    response = client.get("/notifications/?priority=urgente&state=unread")
    assert response.status_code == 200
    assert b"Urgente" in response.data or b"urgente" in response.data


def test_open_notification_marks_it_read(client, app):
    _login(client, "advisor-b9@test.local")
    client.get("/notifications/")

    with app.app_context():
        advisor = User.query.filter_by(email="advisor-b9@test.local").one()
        row = Notification.query.filter(
            Notification.user_id == advisor.id,
            Notification.link.like("/crm/%"),
        ).first()
        assert row is not None
        notification_id = row.id

    response = client.post(f"/notifications/{notification_id}/open", follow_redirects=False)
    assert response.status_code in {302, 303}

    with app.app_context():
        assert db.session.get(Notification, notification_id).read is True
