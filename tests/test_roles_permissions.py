from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from app import create_app
from app.extensions import db
from app.models import Client, Collaborator, Project, Role, Sale, Task, User
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
    role_names = [
        "superadmin",
        "admin",
        "manager",
        "hr",
        "supervisor",
        "advisor",
        "development_coordinator",
        "production",
        "finance",
        "audit",
    ]
    for index, role_name in enumerate(role_names, start=1):
        role = Role.query.filter_by(name=role_name).one()
        email = f"{role_name}@test.local"
        user = User(name=role.label, email=email, role=role, active=True)
        user.set_password("Test123!")
        db.session.add(user)
        db.session.flush()

        if role_name != "audit":
            department = "Desarrollo" if role_name in {"development_coordinator", "production"} else "Pruebas"
            db.session.add(
                Collaborator(
                    user_id=user.id,
                    code=f"TEST-{index:03d}",
                    job_title=role.label,
                    department=department,
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
    "development_coordinator": {"projects", "tasks"},
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


@pytest.mark.parametrize(
    "role_name",
    [
        "superadmin",
        "admin",
        "manager",
        "hr",
        "supervisor",
        "advisor",
        "development_coordinator",
        "production",
        "finance",
    ],
)
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
    assert client.post("/operations/projects/new", data={}, follow_redirects=False).status_code == 403
    assert client.post("/operations/tasks/new", data={}, follow_redirects=False).status_code == 403
    _logout(client)

    _login(client, "advisor")
    assert client.post("/operations/tasks/new", data={}, follow_redirects=False).status_code == 403
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
        coordinator = User.query.filter_by(email="development_coordinator@test.local").one()

        assert finance.has_permission("clients.view")
        assert not finance.has_permission("clients.create")
        assert not finance.has_permission("clients.edit")

        assert production.has_permission("clients.view")
        assert production.has_permission("clients.comment")
        assert production.has_permission("clients.files")
        assert not production.has_permission("clients.create")
        assert not production.has_permission("clients.edit")
        assert production.has_permission("projects.progress")
        assert production.has_permission("tasks.update")
        assert not production.has_permission("projects.edit")
        assert not production.has_permission("tasks.edit")

        assert coordinator.has_permission("clients.view")
        assert coordinator.has_permission("clients.assign")
        assert coordinator.has_permission("clients.comment")
        assert coordinator.has_permission("clients.files")
        assert coordinator.has_permission("projects.edit")
        assert coordinator.has_permission("tasks.edit")
        assert coordinator.has_permission("development.manage")
        assert not coordinator.has_permission("crm.view")
        assert not coordinator.has_permission("sales.view")
        assert not coordinator.has_permission("finance.view")
        assert not coordinator.has_permission("finance.payroll")
        assert not coordinator.has_permission("reports.view")


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

        own_followup = Client(code="SEG-OWN", business_name="Seguimiento propio", contact_name="Contacto", owner_id=advisor.id, record_type="seguimiento")
        other_followup = Client(code="SEG-OTHER", business_name="Seguimiento ajeno", contact_name="Contacto", owner_id=other_advisor.id, record_type="seguimiento")
        own_client = Client(code="CLI-OWN", business_name="Cliente propio", contact_name="Contacto", owner_id=advisor.id, record_type="cliente", pipeline_stage="venta_cerrada", client_status="activo")
        other_client = Client(code="CLI-OTHER", business_name="Cliente ajeno", contact_name="Contacto", owner_id=other_advisor.id, record_type="cliente", pipeline_stage="venta_cerrada", client_status="activo")
        db.session.add_all([own_followup, other_followup, own_client, other_client])
        db.session.flush()

        own_sale = Sale(sale_no="VEN-OWN", client_id=own_client.id, advisor_id=advisor.id, sale_date=date.today(), total=100, amount_paid=0, balance=100)
        other_sale = Sale(sale_no="VEN-OTHER", client_id=other_client.id, advisor_id=other_advisor.id, sale_date=date.today(), total=100, amount_paid=0, balance=100)
        db.session.add_all([own_sale, other_sale])
        db.session.commit()

        own_followup_id = own_followup.id
        other_followup_id = other_followup.id
        own_client_id = own_client.id
        other_client_id = other_client.id
        own_sale_id = own_sale.id
        other_sale_id = other_sale.id

    _login(client, "advisor")
    assert client.get(f"/crm/{own_followup_id}", follow_redirects=False).status_code == 200
    assert client.get(f"/crm/{other_followup_id}", follow_redirects=False).status_code == 403
    assert client.get(f"/clients/{own_client_id}", follow_redirects=False).status_code == 200
    assert client.get(f"/clients/{other_client_id}", follow_redirects=False).status_code == 403
    assert client.get(f"/sales/{own_sale_id}", follow_redirects=False).status_code == 200
    assert client.get(f"/sales/{other_sale_id}", follow_redirects=False).status_code == 403


def test_advisor_cannot_use_unscoped_global_task_kanban(client):
    _login(client, "advisor")
    assert client.get("/operations/tasks/kanban", follow_redirects=False).status_code == 403


def test_production_cannot_use_coordination_kanban(client):
    _login(client, "production")
    assert client.get("/operations/tasks/kanban", follow_redirects=False).status_code == 403


def test_advisor_client_list_is_scoped_to_own_portfolio(client, app):
    with app.app_context():
        advisor = User.query.filter_by(email="advisor@test.local").one().collaborator
        other_user = User(name="Asesor externo", email="scope-other@test.local", role=Role.query.filter_by(name="advisor").one(), active=True)
        other_user.set_password("Test123!")
        db.session.add(other_user)
        db.session.flush()
        other_advisor = Collaborator(user_id=other_user.id, code="SCOPE-OTHER", job_title="Asesor", status="activo")
        db.session.add(other_advisor)
        db.session.flush()

        db.session.add(Client(code="CLI-SCOPE-OWN", business_name="Negocio Visible", contact_name="Contacto", owner_id=advisor.id, record_type="cliente"))
        db.session.add(Client(code="CLI-SCOPE-OTHER", business_name="Negocio Oculto", contact_name="Contacto", owner_id=other_advisor.id, record_type="cliente"))
        db.session.commit()

    _login(client, "advisor")
    response = client.get("/clients/")
    assert response.status_code == 200
    assert b"Negocio Visible" in response.data
    assert b"Negocio Oculto" not in response.data


def test_production_client_page_does_not_offer_write_controls(client, app):
    with app.app_context():
        row = Client(code="CLI-PROD-VIEW", business_name="Cliente Produccion", contact_name="Contacto", record_type="cliente")
        db.session.add(row)
        db.session.commit()
        client_id = row.id

    _login(client, "production")
    response = client.get(f"/clients/{client_id}")
    assert response.status_code == 200
    assert b"Editar ficha" not in response.data
    assert b"Asignar servicio" not in response.data
    assert b"Adjuntar archivo" not in response.data
    assert b"Total pagado" not in response.data


def test_development_coordinator_panel_scope_and_reassignment(client, app):
    with app.app_context():
        coordinator = User.query.filter_by(email="development_coordinator@test.local").one().collaborator
        developer = User.query.filter_by(email="production@test.local").one().collaborator

        dev_client = Client(
            code="CLI-DEV-COORD",
            business_name="Cliente Desarrollo Visible",
            contact_name="Contacto",
            record_type="cliente",
            client_status="activo",
        )
        seo_client = Client(
            code="CLI-SEO-HIDDEN",
            business_name="Cliente SEO Oculto",
            contact_name="Contacto",
            record_type="cliente",
            client_status="activo",
        )
        db.session.add_all([dev_client, seo_client])
        db.session.flush()

        dev_project = Project(
            project_no="PRJ-DEV-COORD",
            client_id=dev_client.id,
            name="Website Desarrollo Visible",
            department="Desarrollo",
            status="en_produccion",
            progress=35,
            starts_on=date.today(),
            due_on=date.today() + timedelta(days=7),
            coordinator_id=coordinator.id,
        )
        seo_project = Project(
            project_no="PRJ-SEO-HIDDEN",
            client_id=seo_client.id,
            name="SEO Oculto Coordinador Desarrollo",
            department="SEO",
            status="en_produccion",
            progress=20,
            starts_on=date.today(),
            due_on=date.today() + timedelta(days=7),
        )
        db.session.add_all([dev_project, seo_project])
        db.session.flush()

        task = Task(
            title="Home Website sin asignar",
            client_id=dev_client.id,
            project_id=dev_project.id,
            priority="media",
            status="pendiente",
            due_at=datetime.utcnow() + timedelta(days=2),
        )
        hidden_task = Task(
            title="SEO fuera de Desarrollo",
            client_id=seo_client.id,
            project_id=seo_project.id,
            priority="alta",
            status="pendiente",
        )
        db.session.add_all([task, hidden_task])
        db.session.commit()
        task_id = task.id
        dev_client_id = dev_client.id
        developer_id = developer.id

    _login(client, "development_coordinator")

    panel = client.get("/operations/development")
    assert panel.status_code == 200
    assert b"Website Desarrollo Visible" in panel.data
    assert b"SEO Oculto Coordinador Desarrollo" not in panel.data
    assert b"Home Website sin asignar" in panel.data
    assert b"SEO fuera de Desarrollo" not in panel.data

    projects = client.get("/operations/projects")
    assert projects.status_code == 200
    assert b"Website Desarrollo Visible" in projects.data
    assert b"SEO Oculto Coordinador Desarrollo" not in projects.data

    mine = client.get("/clients/mine")
    assert mine.status_code == 200
    assert b"Cliente Desarrollo Visible" in mine.data
    assert b"Cliente SEO Oculto" not in mine.data

    assert client.get("/clients/", follow_redirects=False).status_code == 403
    assert client.get("/sales/", follow_redirects=False).status_code == 403
    assert client.get("/finance/", follow_redirects=False).status_code == 403
    assert client.get("/reports/", follow_redirects=False).status_code == 403

    due_value = (datetime.utcnow() + timedelta(days=5)).strftime("%Y-%m-%dT%H:%M")
    update = client.post(
        f"/operations/tasks/{task_id}/update",
        data={
            "status": "en_proceso",
            "assignee_id": developer_id,
            "priority": "urgente",
            "due_at": due_value,
        },
        follow_redirects=False,
    )
    assert update.status_code in {302, 303}

    with app.app_context():
        row = db.session.get(Task, task_id)
        assert row.assignee_id == developer_id
        assert row.priority == "urgente"
        assert row.status == "en_proceso"

    coordination = client.get(f"/clients/{dev_client_id}/coordination")
    assert coordination.status_code == 200
    assert b"Total pagado" not in coordination.data
    assert b"Ventas y pagos" not in coordination.data


def test_production_only_opens_directly_assigned_tasks(client, app):
    with app.app_context():
        production = User.query.filter_by(email="production@test.local").one().collaborator
        production_role = Role.query.filter_by(name="production").one()
        other_user = User(
            name="Otro desarrollador",
            email="other-production@test.local",
            role=production_role,
            active=True,
        )
        other_user.set_password("Test123!")
        db.session.add(other_user)
        db.session.flush()
        other = Collaborator(
            user_id=other_user.id,
            code="DEV-OTHER",
            job_title="Desarrollador",
            department="Desarrollo",
            status="activo",
        )
        db.session.add(other)
        db.session.flush()

        customer = Client(
            code="CLI-DEV-TASKS",
            business_name="Cliente Tareas Desarrollo",
            contact_name="Contacto",
            record_type="cliente",
            client_status="activo",
        )
        db.session.add(customer)
        db.session.flush()
        project = Project(
            project_no="PRJ-DEV-TASKS",
            client_id=customer.id,
            name="Proyecto con varias tareas",
            department="Desarrollo",
            coordinator_id=production.id,
            status="en_produccion",
        )
        db.session.add(project)
        db.session.flush()
        own_task = Task(
            title="Tarea directa del desarrollador",
            client_id=customer.id,
            project_id=project.id,
            assignee_id=production.id,
            status="pendiente",
        )
        other_task = Task(
            title="Tarea de otro desarrollador",
            client_id=customer.id,
            project_id=project.id,
            assignee_id=other.id,
            status="pendiente",
        )
        db.session.add_all([own_task, other_task])
        db.session.commit()
        own_id = own_task.id
        other_id = other_task.id

    _login(client, "production")
    assert client.get(f"/operations/tasks/{own_id}", follow_redirects=False).status_code == 200
    assert client.get(f"/operations/tasks/{other_id}", follow_redirects=False).status_code == 403
