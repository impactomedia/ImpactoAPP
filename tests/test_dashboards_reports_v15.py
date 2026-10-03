from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app import create_app
from app.extensions import db
from app.models import (
    Client,
    Collaborator,
    Expense,
    Payment,
    PrintOrder,
    Project,
    Renewal,
    Role,
    Sale,
    Task,
    User,
)
from scripts.seed import seed_all


class TestConfig:
    TESTING = True
    SECRET_KEY = "block8-test"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    WTF_CSRF_TIME_LIMIT = None
    UPLOAD_FOLDER = str(Path(__file__).parent / "uploads_block8")
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

        admin = _create_user("admin", "admin-b8@test.local", "Admin B8", "Administración")
        advisor_user, advisor = _create_user("advisor", "advisor-b8@test.local", "Asesor B8", "Ventas")
        production_user, production = _create_user("production", "production-b8@test.local", "Producción B8", "Desarrollo")

        customer = Client(
            code="B8-CLI",
            business_name="Cliente Dashboard Propio",
            contact_name="Contacto",
            owner_id=advisor.id,
            record_type="cliente",
            pipeline_stage="venta_cerrada",
            client_status="activo",
            country="USA",
        )
        db.session.add(customer)
        db.session.flush()

        prospect = Client(
            code="B8-SEG",
            business_name="Prospecto Dashboard",
            contact_name="Contacto",
            owner_id=advisor.id,
            record_type="seguimiento",
            pipeline_stage="interesado",
            client_status="activo",
            country="USA",
        )
        db.session.add(prospect)
        db.session.flush()

        sale = Sale(
            sale_no="B8-VEN-001",
            client_id=customer.id,
            advisor_id=advisor.id,
            sale_date=date.today(),
            status="confirmada",
            currency="USD",
            total=Decimal("1000"),
            amount_paid=Decimal("500"),
            balance=Decimal("500"),
        )
        db.session.add(sale)
        db.session.flush()

        db.session.add(
            Payment(
                client_id=customer.id,
                sale_id=sale.id,
                effective_date=date.today(),
                amount=Decimal("500"),
                currency="USD",
                method="Zelle",
                status="confirmado",
            )
        )
        db.session.add(
            Expense(
                expense_date=date.today(),
                category="Software",
                description="Herramienta interna",
                amount=Decimal("125"),
                currency="USD",
                status="pagado",
            )
        )
        db.session.add(
            Renewal(
                client_id=customer.id,
                renewal_type="Plan de prueba",
                due_date=date.today() + timedelta(days=20),
                status="pendiente",
            )
        )

        project = Project(
            project_no="B8-PRJ-001",
            client_id=customer.id,
            name="Website B8",
            department="Desarrollo",
            status="en_produccion",
            progress=50,
            starts_on=date.today(),
            due_on=date.today() + timedelta(days=5),
            coordinator_id=production.id,
        )
        db.session.add(project)
        db.session.flush()

        task = Task(
            title="Tarea visible B8",
            client_id=customer.id,
            project_id=project.id,
            assignee_id=production.id,
            priority="alta",
            status="pendiente",
            due_at=datetime.utcnow() + timedelta(days=1),
        )
        db.session.add(task)

        db.session.add(
            PrintOrder(
                order_no="B8-IMP-001",
                client_id=customer.id,
                responsible_id=production.id,
                provider="Proveedor",
                status="produccion",
                total_cost=Decimal("333"),
                total_sale=Decimal("777"),
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
        code=f"B8-{role_name.upper()}-{user.id}",
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


def test_admin_receives_management_dashboard(client):
    _login(client, "admin-b8@test.local")
    response = client.get("/dashboard/")
    assert response.status_code == 200
    assert b"Dashboard gerencial" in response.data
    assert b"Ingresos cobrados" in response.data
    assert b"Egresos" in response.data
    assert b"Cuentas por cobrar" in response.data


def test_advisor_receives_personal_dashboard_and_own_renewal(client):
    _login(client, "advisor-b8@test.local")
    response = client.get("/dashboard/")
    assert response.status_code == 200
    assert b"Dashboard del asesor" in response.data
    assert b"Cliente Dashboard Propio" in response.data
    assert b"Ventas del mes" in response.data
    assert b"Renovaciones" in response.data


def test_production_dashboard_is_operational_and_finance_free(client):
    _login(client, "production-b8@test.local")
    response = client.get("/dashboard/")
    assert response.status_code == 200
    assert b"Dashboard operativo" in response.data
    assert b"Tarea visible B8" in response.data
    assert b"Ingresos cobrados" not in response.data
    assert b"Egresos" not in response.data
    assert b"Comision estimada" not in response.data


def test_production_report_has_operations_but_no_financial_indicators(client):
    _login(client, "production-b8@test.local")
    response = client.get("/reports/")
    assert response.status_code == 200
    assert b"Proyectos, tareas y carga" in response.data
    assert b"Ingresos cobrados" not in response.data
    assert b"Egresos" not in response.data
    assert b"Comisiones" not in response.data
    assert b"USD 333.00" not in response.data
    assert b"USD 777.00" not in response.data


def test_production_cannot_force_csv_export(client):
    _login(client, "production-b8@test.local")
    response = client.get("/reports/export.csv?report=tasks", follow_redirects=False)
    assert response.status_code == 403
    response = client.get("/reports/export.csv?report=payments", follow_redirects=False)
    assert response.status_code == 403


def test_admin_can_export_csv_and_open_printable_report(client):
    _login(client, "admin-b8@test.local")
    csv_response = client.get("/reports/export.csv?report=sales")
    assert csv_response.status_code == 200
    assert b"B8-VEN-001" in csv_response.data

    print_response = client.get("/reports/print")
    assert print_response.status_code == 200
    assert b"Imprimir / Guardar como PDF" in print_response.data
    assert b"Administraci" in print_response.data
