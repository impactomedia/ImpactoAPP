"""Edición auditada de comentarios y nombre visible de Nexora."""
from pathlib import Path

import pytest

from app import create_app
from app.extensions import db
from app.models import AuditLog, Client, ClientComment, Interaction, Role, User
from scripts.seed import seed_all


class TestConfig:
    TESTING = True
    SECRET_KEY = "test-edits-nexora"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    UPLOAD_FOLDER = str(Path(__file__).parent / "uploads_comment_edits")
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
    instance = create_app(TestConfig)
    with instance.app_context():
        db.create_all()
        seed_all()
        editor = User(
            name="Autor del comentario", email="editor@example.local",
            role=Role.query.filter_by(name="admin").one(), active=True,
        )
        editor.set_password("ExampleInitialKey123")
        db.session.add(editor)
        db.session.flush()
        customer = Client(
            code="EDIT-CLIENT-001", business_name="Cliente de prueba",
            contact_name="Persona", record_type="cliente",
            client_status="activo", pipeline_stage="venta_cerrada",
        )
        prospect = Client(
            code="EDIT-PROS-002", business_name="Prospecto de prueba",
            contact_name="Contacto", record_type="seguimiento",
            client_status="activo", pipeline_stage="nuevo",
        )
        db.session.add_all([customer, prospect])
        db.session.flush()
        db.session.add(ClientComment(
            client_id=customer.id, user_id=editor.id,
            process_type="Seguimiento / CRM", body="Mensaje original",
        ))
        db.session.add(Interaction(
            client_id=prospect.id, user_id=editor.id,
            interaction_type="nota", subject="Asunto original", notes="Nota original",
        ))
        db.session.commit()
    yield instance


@pytest.fixture()
def browser(app):
    instance = app.test_client()
    r = instance.post("/auth/login", data={
        "email": "editor@example.local",
        "password": "ExampleInitialKey123",
    })
    assert r.status_code in (302, 303)
    return instance


def ids(app):
    with app.app_context():
        return (
            Client.query.filter_by(code="EDIT-CLIENT-001").one().id,
            Client.query.filter_by(code="EDIT-PROS-002").one().id,
            ClientComment.query.filter_by(body="Mensaje original").one().id,
            Interaction.query.filter_by(notes="Nota original").one().id,
        )


def test_customer_comment_edit_and_audit(browser, app):
    customer_id, _, comment_id, _ = ids(app)
    r = browser.post(f"/clients/{customer_id}/comment/{comment_id}/edit", data={
        "body": "Mensaje actualizado",
        "process_type": "Seguimiento / CRM",
        "process_detail": "Corrección de llamada",
    })
    assert r.status_code == 302
    assert r.headers["Location"].endswith("#historial")
    with app.app_context():
        assert db.session.get(ClientComment, comment_id).body == "Mensaje actualizado"
        assert AuditLog.query.filter_by(
            entity="ClientComment", entity_id=str(comment_id),
            action="editar_comentario_cliente",
        ).count() == 1
    text = browser.get(f"/clients/{customer_id}").get_data(as_text=True)
    assert "Mensaje actualizado" in text
    assert "Editado" in text
    assert "Modificado por Autor del comentario" in text
    assert "Comentario interno editado" in text


def test_prospect_note_edit_and_audit(browser, app):
    _, prospect_id, _, note_id = ids(app)
    response = browser.post(f"/crm/{prospect_id}/interaction/{note_id}/edit", data={
        "subject": "Nuevo asunto",
        "notes": "Nota ajustada para contacto",
    })
    assert response.status_code == 302
    with app.app_context():
        note = db.session.get(Interaction, note_id)
        assert note.notes == "Nota ajustada para contacto"
        assert AuditLog.query.filter_by(
            entity="Interaction", entity_id=str(note_id),
            action="editar_nota_seguimiento",
        ).count() == 1
    text = browser.get(f"/crm/{prospect_id}").get_data(as_text=True)
    assert "Nota ajustada para contacto" in text
    assert "Editado" in text
    assert "Modificado por Autor del comentario" in text


def test_unchanged_comment_does_not_create_edit_event(browser, app):
    customer_id, _, comment_id, _ = ids(app)
    browser.post(f"/clients/{customer_id}/comment/{comment_id}/edit", data={
        "body": "Mensaje original", "process_type": "Seguimiento / CRM",
        "process_detail": "",
    })
    with app.app_context():
        assert AuditLog.query.filter_by(
            entity="ClientComment", entity_id=str(comment_id),
            action="editar_comentario_cliente",
        ).count() == 0


def test_empty_comment_rejected(browser, app):
    customer_id, _, comment_id, _ = ids(app)
    browser.post(f"/clients/{customer_id}/comment/{comment_id}/edit", data={
        "body": " ", "process_type": "Seguimiento / CRM",
    })
    with app.app_context():
        assert db.session.get(ClientComment, comment_id).body == "Mensaje original"


def test_branding_uses_nexora_and_preserves_icon(app, browser):
    page = app.test_client().get("/auth/login").get_data(as_text=True)
    assert "<title>Acceso | Nexora</title>" in page
    assert "Conectamos procesos. Impulsamos resultados." in page
    assert "impacto-nexora-icon.png" in page
    assert "Impacto Nexora" not in page
    customer_id, _, _, _ = ids(app)
    panel = browser.get(f"/clients/{customer_id}").get_data(as_text=True)
    assert "Conectamos procesos. Impulsamos resultados." in panel
    assert "Historial y seguimiento" in panel


def test_edit_requires_authenticated_user(app):
    customer_id, _, comment_id, _ = ids(app)
    r = app.test_client().post(
        f"/clients/{customer_id}/comment/{comment_id}/edit",
        data={"body": "No autorizado"},
    )
    assert r.status_code == 302
    assert "/auth/login" in r.headers["Location"]
