from datetime import date, datetime, time
from pathlib import Path

import pytest

from app import create_app
from app.extensions import db
from app.models import (
    AttendanceMark,
    Client,
    Collaborator,
    Expense,
    Interaction,
    Payable,
    Project,
    Role,
    Sale,
    ScheduleAssignment,
    Task,
    User,
    WorkSchedule,
)
from scripts.seed import seed_all


class TestConfig:
    TESTING = True
    SECRET_KEY = "block13-test"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    WTF_CSRF_TIME_LIMIT = None
    UPLOAD_FOLDER = str(Path(__file__).parent / "uploads_block13")
    BACKUP_DIR = str(Path(__file__).parent / "backups_block13")
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
            name="Admin B13",
            email="admin-b13@test.local",
            role=admin_role,
            active=True,
        )
        admin.set_password("Test123!")
        db.session.add(admin)
        db.session.flush()
        db.session.add(
            Collaborator(
                user_id=admin.id,
                code="B13-ADM",
                job_title="Administración",
                department="Administración",
                status="activo",
                join_date=date(2026, 1, 1),
            )
        )

        advisor_a = User(
            name="Asesor A B13",
            email="advisor-a-b13@test.local",
            role=advisor_role,
            active=True,
        )
        advisor_a.set_password("Test123!")
        db.session.add(advisor_a)
        db.session.flush()
        collab_a = Collaborator(
            user_id=advisor_a.id,
            code="B13-A",
            job_title="Asesor",
            department="Ventas",
            status="activo",
            join_date=date(2026, 1, 1),
        )
        db.session.add(collab_a)

        advisor_b = User(
            name="Asesor B B13",
            email="advisor-b-b13@test.local",
            role=advisor_role,
            active=True,
        )
        advisor_b.set_password("Test123!")
        db.session.add(advisor_b)
        db.session.flush()
        collab_b = Collaborator(
            user_id=advisor_b.id,
            code="B13-B",
            job_title="Asesor",
            department="Ventas",
            status="activo",
            join_date=date(2026, 1, 1),
        )
        db.session.add(collab_b)
        db.session.flush()

        schedule = WorkSchedule(
            name="Horario B13",
            workdays="0,1,2,3,4",
            start_time=time(8, 0),
            end_time=time(17, 0),
            lunch_minutes=60,
            break_minutes=30,
            tolerance_minutes=10,
            active=True,
        )
        db.session.add(schedule)
        db.session.flush()
        db.session.add(
            ScheduleAssignment(
                collaborator_id=collab_a.id,
                schedule_id=schedule.id,
                starts_on=date(2026, 1, 1),
            )
        )

        client_a = Client(
            code="CLI-B13-A",
            business_name="Cliente Agenda A",
            contact_name="Contacto A",
            email="cliente-a@test.local",
            country="USA",
            owner_id=collab_a.id,
            record_type="seguimiento",
            pipeline_stage="seguimiento",
            client_status="activo",
        )
        client_b = Client(
            code="CLI-B13-B",
            business_name="Cliente Agenda B",
            contact_name="Contacto B",
            email="cliente-b@test.local",
            country="USA",
            owner_id=collab_b.id,
            record_type="seguimiento",
            pipeline_stage="seguimiento",
            client_status="activo",
        )
        db.session.add_all([client_a, client_b])
        db.session.flush()

        db.session.add_all([
            Interaction(
                client_id=client_a.id,
                user_id=advisor_a.id,
                interaction_type="llamada",
                subject="Seguimiento visible",
                next_followup_at=datetime(2026, 10, 10, 9, 0),
            ),
            Interaction(
                client_id=client_b.id,
                user_id=advisor_b.id,
                interaction_type="llamada",
                subject="Seguimiento oculto",
                next_followup_at=datetime(2026, 10, 10, 10, 0),
            ),
            Sale(
                sale_no="SALE-B13-A",
                client_id=client_a.id,
                advisor_id=collab_a.id,
                sale_date=date(2026, 10, 5),
                status="confirmada",
                currency="USD",
                total=500,
                amount_paid=500,
                balance=0,
            ),
            AttendanceMark(
                collaborator_id=collab_a.id,
                mark_type="entrada",
                marked_at=datetime(2026, 10, 5, 14, 25),  # 08:25 America/Managua
            ),
            AttendanceMark(
                collaborator_id=collab_a.id,
                mark_type="salida",
                marked_at=datetime(2026, 10, 6, 0, 0),  # 18:00 America/Managua del 05/10
            ),
            Expense(
                expense_date=date(2026, 10, 1),
                category="software",
                beneficiary="Proveedor SaaS",
                description="Suscripción mensual",
                amount=99,
                currency="USD",
                status="pagado",
                recurring=True,
                next_due_date=date(2026, 11, 1),
            ),
            Payable(
                provider="Hosting B13",
                concept="Hosting anual",
                amount=240,
                paid_amount=0,
                currency="USD",
                due_date=date(2026, 11, 15),
                status="pendiente",
                recurring=True,
            ),
        ])

        project = Project(
            project_no="PRJ-B13-A",
            client_id=client_a.id,
            name="Proyecto calendario B13",
            status="en_produccion",
            progress=50,
            starts_on=date(2026, 10, 1),
            due_on=date(2026, 10, 20),
        )
        db.session.add(project)
        db.session.flush()
        db.session.add(
            Task(
                title="Tarea calendario B13",
                task_type="cliente",
                client_id=client_a.id,
                project_id=project.id,
                assignee_id=collab_a.id,
                priority="media",
                status="pendiente",
                due_at=datetime(2026, 10, 21, 17, 0),
            )
        )

        db.session.commit()

    yield app


@pytest.fixture()
def client(app):
    return app.test_client()


def _login(client, email):
    response = client.post(
        "/auth/login",
        data={"email": email, "password": "Test123!"},
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}


def test_planning_index_is_available_to_advisor(client):
    _login(client, "advisor-a-b13@test.local")
    response = client.get("/planning/")
    assert response.status_code == 200
    assert b"Planificaci" in response.data
    assert b"Agenda CRM" in response.data


def test_crm_agenda_respects_advisor_portfolio(client):
    _login(client, "advisor-a-b13@test.local")
    response = client.get("/planning/crm-agenda?period=2026-10")
    assert response.status_code == 200
    assert b"Seguimiento visible" in response.data
    assert b"Seguimiento oculto" not in response.data


def test_admin_can_set_goal_and_sales_progress_is_visible(client, app):
    _login(client, "admin-b13@test.local")

    with app.app_context():
        collaborator_id = User.query.filter_by(
            email="advisor-a-b13@test.local"
        ).one().collaborator.id

    response = client.post(
        "/planning/sales-goals",
        data={
            "period": "2026-10",
            "collaborator_id": str(collaborator_id),
            "goal_amount": "1000",
        },
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}

    response = client.get("/planning/sales-goals?period=2026-10")
    assert response.status_code == 200
    assert b"1,000" in response.data or b"$1,000" in response.data
    assert b"500" in response.data


def test_incident_and_overtime_are_derived_without_deleting_marks(client, app):
    _login(client, "admin-b13@test.local")

    incidents = client.get("/planning/hr-incidents?period=2026-10")
    assert incidents.status_code == 200
    assert b"Tardanza" in incidents.data

    overtime = client.get("/planning/overtime?period=2026-10")
    assert overtime.status_code == 200
    assert b"60 min" in overtime.data

    with app.app_context():
        before = AttendanceMark.query.count()

    with app.app_context():
        collaborator_id = User.query.filter_by(
            email="advisor-a-b13@test.local"
        ).one().collaborator.id

    response = client.post(
        "/planning/hr-incidents/resolve",
        data={
            "collaborator_id": str(collaborator_id),
            "day": "2026-10-05",
            "kind": "tardanza",
            "status": "justificada",
            "reason": "Prueba administrativa",
        },
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}

    with app.app_context():
        assert AttendanceMark.query.count() == before


def test_recurring_expenses_screen_consolidates_sources(client):
    _login(client, "admin-b13@test.local")
    response = client.get("/planning/recurring-expenses")
    assert response.status_code == 200
    assert b"Proveedor SaaS" in response.data
    assert b"Hosting B13" in response.data


def test_operations_calendar_respects_existing_visibility(client):
    _login(client, "advisor-a-b13@test.local")
    response = client.get("/planning/operations-calendar?period=2026-10")
    assert response.status_code == 200
    assert b"Proyecto calendario B13" in response.data
    assert b"Tarea calendario B13" in response.data
