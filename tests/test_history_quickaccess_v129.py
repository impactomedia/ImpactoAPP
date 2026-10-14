"""Historial visible y usable desde fichas de seguimiento y clientes."""
from datetime import datetime
from pathlib import Path

import pytest

from app import create_app
from app.extensions import db
from app.models import Client, ClientComment, Interaction, Role, User
from scripts.seed import seed_all


class Config:
    TESTING = True
    SECRET_KEY = "historial-test-key"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    UPLOAD_FOLDER = str(Path(__file__).parent / "uploads_history_drawer")
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
        role = Role.query.filter_by(name="admin").one()
        user = User(name="Admin Historial", email="historial@test.local", role=role, active=True)
        user.set_password("PasswordTest987")
        db.session.add(user)
        db.session.flush()
        customer = Client(
            code="CLI-HISTORY-01", business_name="Cliente de ejemplo",
            contact_name="Persona de prueba", record_type="cliente",
            pipeline_stage="venta_cerrada", client_status="activo"
        )
        prospect = Client(
            code="SEG-HISTORY-02", business_name="Seguimiento de ejemplo",
            contact_name="Persona prospecto", record_type="seguimiento",
            pipeline_stage="nuevo", client_status="activo"
        )
        db.session.add_all([customer, prospect])
        db.session.flush()
        db.session.add(ClientComment(
            client_id=customer.id, user_id=user.id,
            process_type="Seguimiento / CRM",
            body="Comentario visible al abrir el panel"
        ))
        db.session.add(Interaction(
            client_id=prospect.id, user_id=user.id,
            interaction_type="nota", occurred_at=datetime.utcnow(),
            subject="Nota del prospecto", notes="Detalle de llamada importante"
        ))
        db.session.commit()
    yield app


@pytest.fixture()
def browser(app):
    browser = app.test_client()
    result = browser.post("/auth/login", data={
        "email": "historial@test.local", "password": "PasswordTest987"
    })
    assert result.status_code in (302, 303)
    return browser


def ids(app):
    with app.app_context():
        return (
            Client.query.filter_by(code="CLI-HISTORY-01").one().id,
            Client.query.filter_by(code="SEG-HISTORY-02").one().id
        )


def test_client_button_and_history_tabs(browser, app):
    customer_id, _ = ids(app)
    response = browser.get(f"/clients/{customer_id}")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Historial y seguimiento" in html
    assert 'id="clientHistoryDrawer"' in html
    assert 'id="historyCommentsPane"' in html
    assert 'id="historyActivityPane"' in html
    assert "Comentario visible al abrir el panel" in html


def test_prospect_button_internal_notes_and_activity(browser, app):
    _, prospect_id = ids(app)
    response = browser.get(f"/crm/{prospect_id}")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'data-bs-target="#clientHistoryDrawer"' in html
    assert "Nota del prospecto" in html
    assert "Detalle de llamada importante" in html
    assert "Timeline automático" in html


def test_client_comment_redirects_to_history(browser, app):
    customer_id, _ = ids(app)
    response = browser.post(f"/clients/{customer_id}/comment", data={
        "process_type": "Seguimiento / CRM",
        "body": "Actualización para próxima llamada"
    })
    assert response.status_code == 302
    assert response.headers["Location"].endswith("#historial")


def test_prospect_note_redirects_to_history(browser, app):
    _, prospect_id = ids(app)
    response = browser.post(f"/crm/{prospect_id}/interaction", data={
        "interaction_type": "nota",
        "subject": "Próxima llamada",
        "notes": "Llamar la semana entrante",
        "from_history": "1"
    })
    assert response.status_code == 302
    assert response.headers["Location"].endswith("#historial")
