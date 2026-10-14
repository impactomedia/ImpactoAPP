from datetime import date
from pathlib import Path
import pytest

from app import create_app
from app.extensions import db
from app.models import Client, ClientComment, Role, User
from scripts.seed import seed_all


class Config:
    TESTING = True
    SECRET_KEY = "test-quick-history"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    UPLOAD_FOLDER = str(Path(__file__).parent / "uploads_quick_history")
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
    app = create_app(Config)
    with app.app_context():
        db.create_all()
        seed_all()
        admin = User(
            name="Admin Historial",
            email="history-admin@test.local",
            role=Role.query.filter_by(name="admin").one(),
            active=True,
        )
        admin.set_password("HistoryTest123!")
        db.session.add(admin)
        db.session.flush()
        db.session.add_all([
            Client(
                code="QH-CLIENT", business_name="Cliente Historial",
                contact_name="Contacto", record_type="cliente",
                pipeline_stage="venta_cerrada", client_status="activo",
            ),
            Client(
                code="QH-LEAD", business_name="Seguimiento Historial",
                contact_name="Contacto", record_type="seguimiento",
                pipeline_stage="contactado", client_status="activo",
            ),
        ])
        db.session.commit()
    yield app


@pytest.fixture()
def browser(app):
    client = app.test_client()
    response = client.post("/auth/login", data={
        "email": "history-admin@test.local",
        "password": "HistoryTest123!",
    })
    assert response.status_code == 302
    return client


def test_client_history_is_easy_to_open(browser, app):
    with app.app_context():
        row = Client.query.filter_by(code="QH-CLIENT").one()
        cid = row.id
    response = browser.get(f"/clients/{cid}")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'data-client-history-open' in html
    assert 'id="clientHistoryTab"' in html
    assert 'id="historial"' in html
    assert 'Timeline automático' in html


def test_client_comment_keeps_history_open(browser, app):
    with app.app_context():
        cid = Client.query.filter_by(code="QH-CLIENT").one().id
    result = browser.post(f"/clients/{cid}/comment", data={
        "process_type": "Seguimiento / CRM",
        "body": "Nota para el equipo comercial",
    })
    assert result.status_code == 302
    assert result.headers["Location"].endswith("#historial")


def test_crm_history_includes_internal_comments(browser, app):
    with app.app_context():
        cid = Client.query.filter_by(code="QH-LEAD").one().id
    response = browser.get(f"/crm/{cid}")
    assert response.status_code == 200
    assert "Historial y comentarios" in response.get_data(as_text=True)

    result = browser.post(f"/crm/{cid}/internal-comment", data={
        "process_type": "Seguimiento / CRM",
        "body": "Nota interna sobre la propuesta",
    })
    assert result.status_code == 302
    assert result.headers["Location"].endswith("#historial")
    with app.app_context():
        assert ClientComment.query.filter_by(client_id=cid).count() == 1

    page = browser.get(f"/crm/{cid}")
    assert page.status_code == 200
    assert "Nota interna sobre la propuesta" in page.get_data(as_text=True)


def test_crm_empty_comment_is_rejected(browser, app):
    with app.app_context():
        cid = Client.query.filter_by(code="QH-LEAD").one().id
    response = browser.post(f"/crm/{cid}/internal-comment", data={"body": " "})
    assert response.status_code == 302
    with app.app_context():
        assert ClientComment.query.filter_by(client_id=cid).count() == 0
