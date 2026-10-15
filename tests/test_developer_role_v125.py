from pathlib import Path

import pytest

from app import create_app
from app.extensions import db
from app.models import Client, Collaborator, Role, Task, User
from scripts.seed import seed_all


class TestConfig:
    TESTING = True
    SECRET_KEY = "test-nexora-development-roles"
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
    application = create_app(TestConfig)
    with application.app_context():
        db.create_all()
        seed_all()
        developer_role = Role.query.filter_by(name="developer").one()
        advisor_role = Role.query.filter_by(name="advisor").one()
        for i, role in enumerate([developer_role, developer_role, advisor_role], start=1):
            u = User(name=f"Integrante {i}", email=f"role{i}@test.local", role=role, active=True)
            u.set_password("TestPass123!")
            db.session.add(u)
            db.session.flush()
            db.session.add(
                Collaborator(
                    user_id=u.id,
                    code=f"DEV-NEW-{i}",
                    job_title="Desarrollador" if role.name == "developer" else "Asesor(a) de Ventas",
                    department="Desarrollo" if role.name == "developer" else "Ventas",
                    status="activo",
                )
            )
        db.session.flush()
        dev1 = User.query.filter_by(email="role1@test.local").one().collaborator
        dev2 = User.query.filter_by(email="role2@test.local").one().collaborator
        advisor = User.query.filter_by(email="role3@test.local").one().collaborator
        c1 = Client(
            code="CLI-DEV-01", business_name="Cliente asignado",
            contact_name="Persona", record_type="cliente", client_status="activo",
            owner_id=advisor.id,
        )
        c2 = Client(
            code="CLI-DEV-02", business_name="Cliente no asignado",
            contact_name="Persona", record_type="cliente", client_status="activo",
            owner_id=advisor.id,
        )
        db.session.add_all([c1, c2])
        db.session.flush()
        t1 = Task(title="Actualizar sitio", client_id=c1.id, assignee_id=dev1.id, status="pendiente")
        t2 = Task(title="Tarea ajena", client_id=c2.id, assignee_id=dev2.id, status="pendiente")
        db.session.add_all([t1,t2])
        db.session.commit()
        application.config["TEST_NX_IDS"] = {"client1":c1.id,"client2":c2.id,"task1":t1.id,"task2":t2.id}
    yield application


@pytest.fixture()
def client(app):
    return app.test_client()


def login(client, index):
    resp=client.post("/auth/login",data={
        "email":f"role{index}@test.local","password":"TestPass123!"
    },follow_redirects=False)
    assert resp.status_code in (302,303)


def test_developer_exact_permissions(app):
    with app.app_context():
        user=User.query.filter_by(email="role1@test.local").one()
        assert user.role.name == "developer"
        allowed={"clients.view","tasks.view","tasks.update"}
        assert {p.code for p in user.role.permissions} == allowed
        assert user.has_permission("tasks.update")
        assert not user.has_permission("clients.edit")
        assert not user.has_permission("sales.view")
        assert not user.has_permission("projects.edit")
        assert not user.has_permission("tasks.edit")


def test_developer_can_read_only_assigned_client(client,app):
    ids=app.config["TEST_NX_IDS"]
    login(client,1)
    page=client.get("/clients/mine")
    assert page.status_code==200
    assert b"Cliente asignado" in page.data
    assert b"Cliente no asignado" not in page.data
    assert client.get(f"/clients/{ids['client1']}/coordination").status_code==200
    assert client.get(f"/clients/{ids['client2']}/coordination").status_code==403
    assert client.get(f"/clients/{ids['client1']}").status_code==403
    assert client.post(f"/clients/{ids['client1']}/coordination/comment",data={"body":"Seguimiento de desarrollo","process_type":"Desarrollo web"}).status_code==302
    assert client.post(f"/clients/{ids['client2']}/coordination/comment",data={"body":"Intento ajeno","process_type":"Desarrollo web"}).status_code==403
    with app.app_context():
        from app.models import ClientComment
        comment=ClientComment.query.filter_by(client_id=ids["client1"],body="Seguimiento de desarrollo").one()
        author=User.query.filter_by(email="role1@test.local").one()
        assert comment.user_id==author.id


def test_developer_can_update_only_own_task(client,app):
    ids=app.config["TEST_NX_IDS"]
    login(client,1)
    page=client.get("/operations/tasks")
    assert page.status_code==200
    assert b"Actualizar sitio" in page.data
    assert b"Tarea ajena" not in page.data
    assert client.get(f"/operations/tasks/{ids['task1']}").status_code==200
    assert client.get(f"/operations/tasks/{ids['task2']}").status_code==403
    response=client.post(
        f"/operations/tasks/{ids['task1']}/update",
        data={"status":"en_proceso"},
        follow_redirects=False
    )
    assert response.status_code in (302,303)
    assert client.post(f"/operations/tasks/{ids['task2']}/update",data={"status":"completada"}).status_code==403
    with app.app_context():
        assert db.session.get(Task,ids['task1']).status=="en_proceso"
        assert db.session.get(Task,ids['task2']).status=="pendiente"


def test_developer_cannot_enter_commercial_or_hr(client):
    login(client,1)
    for url in ("/crm/","/sales/","/finance/","/settings/","/hr/","/operations/projects","/operations/tasks/new"):
        assert client.get(url,follow_redirects=False).status_code==403,url
    assert client.post("/sales/new",data={},follow_redirects=False).status_code==403


def test_advisor_cannot_edit_other_client(client,app):
    ids=app.config["TEST_NX_IDS"]
    login(client,3)
    # El cliente ajeno de otra cartera no debe poderse abrir con su ID.
    with app.app_context():
        row=db.session.get(Client,ids["client2"])
        row.owner_id=User.query.filter_by(email="role2@test.local").one().collaborator.id
        db.session.commit()
    assert client.get(f"/clients/{ids['client1']}").status_code==200
    assert client.get(f"/clients/{ids['client2']}").status_code==403
