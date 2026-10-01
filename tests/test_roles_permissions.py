from datetime import date
from pathlib import Path

import pytest

from app import create_app
from app.extensions import db
from app.models import Client, Collaborator, Role, Sale, User
from scripts.seed import seed_all


class TestConfig:
    TESTING = True
    SECRET_KEY = "test-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    WTF_CSRF_TIME_LIMIT = None
    UPLOAD_FOLDER = str(Path(__file__).parent / "uploads")
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
        _create_test_users()
    yield app


@pytest.fixture()
def client(app):
    return app.test_client()


def _create_test_users():
    role_names = ["superadmin", "admin", "manager", "hr", "supervisor", "advisor", "production", "finance", "audit"]
    for index, role_name in enumerate(role_names, start=1):
        role = Role.query.filter_by(name=role_name).one()
        email = f"{role_name}@test.local"
        user = User(name=role.label, email=email, role=role, active=True)
        user.set_password("Test123!")
        db.session.add(user)
        db.session.flush()

        if role_name != "audit":
            db.session.add(
                Collaborator(
                    user_id=user.id,
                    code=f"TEST-{index:03d}",
                    job_title=role.label,
                    department="Pruebas",
                    status="activo",
                    join_date=date.today(),
                )
            )
    db.session.commit()


def _login(client, role_name):
    response = client.post(
        "/auth/login",
        data={"email": f"{role_name}@test.local", "password": "Test123!"},
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}


def _logout(client):
    client.post("/auth/logout")


MODULE_PATHS = {
    "crm": "/crm/",
    "clients": "/clients/",
    "sales": "/sales/",
    "projects": "/operations/projects",
    "tasks": "/operations/tasks",
    "printing": "/printing/",
    "finance": "/finance/",
    "hr": "/hr/",
    "support": "/support/",
    "reports": "/reports/",
}

ALLOWED_MODULES = {
    "superadmin": set(MODULE_PATHS),
    "admin": set(MODULE_PATHS),
    "manager": set(MODULE_PATHS),
    "hr": {"tasks", "hr", "reports"},
    "supervisor": {"crm", "clients", "sales", "projects", "tasks", "printing", "hr", "support", "reports"},
    "advisor": {"crm", "clients", "sales", "projects", "tasks", "printing", "support", "reports"},
    "production": {"clients", "projects", "tasks", "printing", "support", "reports"},
    "finance": {"clients", "sales", "finance", "reports"},
    "audit": {"reports"},
}


@pytest.mark.parametrize("role_name", list(ALLOWED_MODULES))
def test_module_access_matrix(client, role_name):
    _login(client, role_name)
    allowed = ALLOWED_MODULES[role_name]

    for module, path in MODULE_PATHS.items():
        response = client.get(path, follow_redirects=False)
        expected = 200 if module in allowed else 403
        assert response.status_code == expected, (role_name, module, path, response.status_code)

    _logout(client)


@pytest.mark.parametrize("role_name", ["superadmin", "admin", "manager", "hr", "supervisor", "advisor", "production", "finance"])
def test_employee_self_service_is_available(client, role_name):
    _login(client, role_name)
    assert client.get("/hr/my-day", follow_redirects=False).status_code == 200
    assert client.get("/hr/leave", follow_redirects=False).status_code == 200
    _logout(client)


def test_audit_role_can_open_audit_log_but_not_settings_admin(client):
    _login(client, "audit")
    assert client.get("/settings/audit").status_code == 200
    assert client.get("/settings/", follow_redirects=False).status_code == 403
    assert client.get("/settings/roles", follow_redirects=False).status_code == 403


def test_hr_can_open_payroll_but_not_finance_dashboard(client):
    _login(client, "hr")
    assert client.get("/finance/payroll", follow_redirects=False).status_code == 200
    assert client.get("/finance/", follow_redirects=False).status_code == 403


def test_restricted_write_actions_are_denied(client):
    _login(client, "production")
    assert client.post("/sales/new", data={}, follow_redirects=False).status_code == 403
    _logout(client)

    _login(client, "finance")
    assert client.post("/clients/new", data={}, follow_redirects=False).status_code == 403
    _logout(client)

    _login(client, "audit")
    assert client.post("/support/", data={}, follow_redirects=False).status_code == 403


def test_supervisor_has_no_sensitive_hr_permission(app):
    with app.app_context():
        supervisor = User.query.filter_by(email="supervisor@test.local").one()
        assert supervisor.has_permission("hr.view")
        assert supervisor.has_permission("attendance.edit")
        assert supervisor.has_permission("leave.approve")
        assert not supervisor.has_permission("hr.edit")
        assert not supervisor.has_permission("hr.sensitive")


def test_finance_and_production_client_permissions_are_limited(app):
    with app.app_context():
        finance = User.query.filter_by(email="finance@test.local").one()
        production = User.query.filter_by(email="production@test.local").one()

        assert finance.has_permission("clients.view")
        assert not finance.has_permission("clients.create")
        assert not finance.has_permission("clients.edit")

        assert production.has_permission("clients.view")
        assert not production.has_permission("clients.create")
        assert not production.has_permission("clients.edit")


def test_advisor_cannot_open_another_advisors_crm_or_sale(client, app):
    with app.app_context():
        advisor = User.query.filter_by(email="advisor@test.local").one().collaborator

        other_user = User(name="Otro asesor", email="other-advisor@test.local", role=Role.query.filter_by(name="advisor").one(), active=True)
        other_user.set_password("Test123!")
        db.session.add(other_user)
        db.session.flush()
        other_advisor = Collaborator(user_id=other_user.id, code="TEST-OTHER", job_title="Asesor", status="activo")
        db.session.add(other_advisor)
        db.session.flush()

        own_client = Client(code="CLI-OWN", business_name="Cliente propio", contact_name="Contacto", owner_id=advisor.id, record_type="seguimiento")
        other_client = Client(code="CLI-OTHER", business_name="Cliente ajeno", contact_name="Contacto", owner_id=other_advisor.id, record_type="seguimiento")
        db.session.add_all([own_client, other_client])
        db.session.flush()

        own_sale = Sale(sale_no="VEN-OWN", client_id=own_client.id, advisor_id=advisor.id, sale_date=date.today(), total=100, amount_paid=0, balance=100)
        other_sale = Sale(sale_no="VEN-OTHER", client_id=other_client.id, advisor_id=other_advisor.id, sale_date=date.today(), total=100, amount_paid=0, balance=100)
        db.session.add_all([own_sale, other_sale])
        db.session.commit()

        own_client_id = own_client.id
        other_client_id = other_client.id
        own_sale_id = own_sale.id
        other_sale_id = other_sale.id

    _login(client, "advisor")
    assert client.get(f"/crm/{own_client_id}", follow_redirects=False).status_code == 200
    assert client.get(f"/crm/{other_client_id}", follow_redirects=False).status_code == 403
    assert client.get(f"/sales/{own_sale_id}", follow_redirects=False).status_code == 200
    assert client.get(f"/sales/{other_sale_id}", follow_redirects=False).status_code == 403


def test_advisor_cannot_use_unscoped_global_task_kanban(client):
    _login(client, "advisor")
    assert client.get("/operations/tasks/kanban", follow_redirects=False).status_code == 403
