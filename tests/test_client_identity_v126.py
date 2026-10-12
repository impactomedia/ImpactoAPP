"""v1.26.0: ficha inicial, logo principal, permisos y reemplazo sin migración."""
import io
from datetime import date
from pathlib import Path

import pytest

from app import create_app
from app.client_v2_models import ClientDocumentMeta, ClientTeamAssignment
from app.extensions import db
from app.models import Attachment, Client, Collaborator, Role, User
from scripts.seed import seed_all

# PNG válido de 1x1 píxel, para pruebas sin dependencias externas.
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000b49444154789c636000020000050001a5f645400000000049454e44ae426082"
)


class TestConfig:
    TESTING = True
    SECRET_KEY = "test-client-identity-v126"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    WTF_CSRF_TIME_LIMIT = None
    COMPANY_NAME = "Impacto Media Agency"
    COMPANY_TIMEZONE = "America/Managua"
    SMTP_HOST = None
    SMTP_PORT = 587
    SMTP_USER = None
    SMTP_PASSWORD = None
    SMTP_FROM = "test@example.com"
    SMTP_USE_TLS = False
    MAX_CONTENT_LENGTH = 10 * 1024 * 1024
    MAX_FILE_UPLOAD_MB = 5


@pytest.fixture()
def app(tmp_path):
    class Config(TestConfig):
        UPLOAD_FOLDER = str(tmp_path / "uploads")
    app = create_app(Config)
    with app.app_context():
        db.create_all()
        seed_all()
        for role, email in (
            ("admin", "admin126@test.local"),
            ("advisor", "advisor126@test.local"),
            ("advisor", "other126@test.local"),
            ("developer", "developer126@test.local"),
        ):
            user = User(name=email, email=email, role=Role.query.filter_by(name=role).one(), active=True)
            user.set_password("TestPass123!")
            db.session.add(user)
            db.session.flush()
            db.session.add(Collaborator(user_id=user.id, code=f"T126-{user.id}", job_title="Test", department="Desarrollo" if role == "developer" else "Ventas", status="activo"))
        db.session.flush()
        advisor = User.query.filter_by(email="advisor126@test.local").one().collaborator
        other = User.query.filter_by(email="other126@test.local").one().collaborator
        a = Client(business_name="Cliente Uno", contact_name="Contacto Uno", code="T126-A", owner_id=advisor.id, record_type="cliente", client_status="activo", country="USA", email="a@example.com", phone="1112223333")
        b = Client(business_name="Cliente Dos", contact_name="Contacto Dos", code="T126-B", owner_id=other.id, record_type="cliente", client_status="activo", country="USA")
        db.session.add_all([a,b])
        db.session.flush()
        ids = {"a": a.id, "b": b.id}
        dev = User.query.filter_by(email="developer126@test.local").one().collaborator
        db.session.add(ClientTeamAssignment(client_id=a.id, collaborator_id=dev.id, role_in_client="Desarrollo", starts_on=date.today(), status="activa"))
        db.session.commit()
        app.config["TEST_CLIENT_IDS"] = ids
    return app


@pytest.fixture()
def web(app):
    return app.test_client()


def login(web, email):
    response = web.post("/auth/login", data={"email":email,"password":"TestPass123!"},follow_redirects=False)
    assert response.status_code in {302, 303}


def upload(web, client_id, content=PNG, filename="logo.png"):
    return web.post(
        f"/clients/{client_id}/brand-logo",
        data={"logo":(io.BytesIO(content),filename)},
        content_type="multipart/form-data",follow_redirects=False,
    )


def test_hero_visible_on_first_view(web,app):
    login(web,"advisor126@test.local")
    response=web.get(f"/clients/{app.config['TEST_CLIENT_IDS']['a']}")
    assert response.status_code == 200
    assert b"Informaci" in response.data
    assert b"Contacto Uno" in response.data
    assert b"Sin logo" in response.data


def test_upload_replace_and_remove_keep_history(web,app):
    client_id=app.config["TEST_CLIENT_IDS"]["a"]
    login(web,"advisor126@test.local")
    assert upload(web, client_id).status_code == 302
    with app.app_context():
        logo=ClientDocumentMeta.query.filter_by(client_id=client_id,category="logo_principal").one()
        first_id=logo.attachment_id
    view=web.get(f"/clients/{client_id}/brand-logo/{first_id}")
    assert view.status_code == 200
    assert view.mimetype == "image/png"
    assert view.data.startswith(b"\x89PNG")
    assert upload(web,client_id,filename="replacement.PNG").status_code == 302
    with app.app_context():
        rows=ClientDocumentMeta.query.filter_by(client_id=client_id).all()
        assert len(rows)==2
        assert [x.category for x in rows].count("logo_principal")==1
        assert [x.category for x in rows].count("branding")==1
        new_id=next(x.attachment_id for x in rows if x.category=="logo_principal")
    assert web.get(f"/clients/{client_id}/brand-logo/{first_id}").status_code == 404
    assert web.get(f"/clients/{client_id}/brand-logo/{new_id}").status_code == 200
    assert web.post(f"/clients/{client_id}/brand-logo/remove").status_code == 302
    assert web.get(f"/clients/{client_id}/brand-logo/{new_id}").status_code == 404
    with app.app_context():
        assert Attachment.query.filter_by(entity_type="Client",entity_id=client_id).count()==2


def test_invalid_image_rejected(web,app):
    cid=app.config["TEST_CLIENT_IDS"]["a"]
    login(web,"advisor126@test.local")
    assert upload(web,cid,b"not image","fake.png").status_code==302
    assert upload(web,cid,PNG,"fake.svg").status_code==302
    assert upload(web,cid,PNG+b"0"*(5*1024*1024+1),"huge.png").status_code==302
    with app.app_context():
        assert Attachment.query.filter_by(entity_type="Client",entity_id=cid).count()==0


def test_advisor_is_restricted_to_owned_clients(web,app):
    a=app.config["TEST_CLIENT_IDS"]["a"]
    b=app.config["TEST_CLIENT_IDS"]["b"]
    login(web,"admin126@test.local")
    assert upload(web,b).status_code==302
    with app.app_context():
        logo=ClientDocumentMeta.query.filter_by(client_id=b,category="logo_principal").one()
        file_id=logo.attachment_id
    web.post("/auth/logout")
    login(web,"advisor126@test.local")
    assert upload(web,b).status_code==403
    assert web.get(f"/clients/{b}/brand-logo/{file_id}").status_code==403
    assert web.get(f"/clients/{a}").status_code==200


def test_assigned_developer_can_see_but_not_modify(web,app):
    a=app.config["TEST_CLIENT_IDS"]["a"]
    b=app.config["TEST_CLIENT_IDS"]["b"]
    login(web,"admin126@test.local")
    assert upload(web,a).status_code==302
    with app.app_context():
        file_id=ClientDocumentMeta.query.filter_by(client_id=a,category="logo_principal").one().attachment_id
    web.post("/auth/logout")
    login(web,"developer126@test.local")
    assert web.get(f"/clients/{a}/coordination").status_code==200
    assert web.get(f"/clients/{a}/brand-logo/{file_id}").status_code==200
    assert upload(web,a).status_code==403
    assert web.post(f"/clients/{a}/brand-logo/remove").status_code==403
    assert web.get(f"/clients/{b}/coordination").status_code==403
