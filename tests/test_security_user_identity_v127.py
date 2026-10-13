"""Pruebas de protección del correo y eliminación segura de usuarios."""
from datetime import date
from pathlib import Path

import pytest

from app import create_app
from app.extensions import db
from app.models import AuditLog, Client, Collaborator, Role, User
from scripts.seed import seed_all


class TestConfig:
    TESTING = True
    SECRET_KEY = "test-security-users"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    UPLOAD_FOLDER = str(Path(__file__).parent / "uploads_security_users")
    MAX_CONTENT_LENGTH = 10 * 1024 * 1024
    COMPANY_NAME = "Impacto Media Agency"
    COMPANY_TIMEZONE = "America/Managua"
    SMTP_HOST = None
    SMTP_PORT = 587
    SMTP_USER = None
    SMTP_PASSWORD = None
    SMTP_FROM = "test@example.com"
    SMTP_USE_TLS = False


@pytest.fixture
def app():
    flask_app = create_app(TestConfig)
    with flask_app.app_context():
        db.create_all()
        seed_all()
        admin = User(
            name="Administrador Prueba",
            email="security-admin@test.local",
            role=Role.query.filter_by(name="admin").one(),
            active=True,
        )
        admin.set_password("AdminPassword123!")
        employee = User(
            name="Colaborador Prueba",
            email="wrong-address@test.local",
            role=Role.query.filter_by(name="advisor").one(),
            active=True,
        )
        employee.set_password("EmployeePassword123!")
        db.session.add_all((admin, employee))
        db.session.flush()
        db.session.add(Collaborator(
            user_id=employee.id, code="TEST-ADV-001",
            corporate_email="wrong-address@test.local",
            job_title="Asesor", department="Ventas", status="activo",
            join_date=date.today(),
        ))
        db.session.commit()
    yield flask_app


@pytest.fixture
def client(app):
    browser = app.test_client()
    result = browser.post("/auth/login", data={
        "email": "security-admin@test.local",
        "password": "AdminPassword123!",
    }, follow_redirects=False)
    assert result.status_code in (302, 303)
    return browser


def account(email):
    return User.query.filter_by(email=email).one()


def form(user, **other):
    return dict(
        admin_password="AdminPassword123!",
        confirm_email=user.email,
        confirm_word="ELIMINAR",
        reason="Cuenta de prueba creada por error",
        understand_history="yes",
        **other,
    )


def test_admin_corrects_email_and_keeps_collaborator(client, app):
    with app.app_context():
        worker = account("wrong-address@test.local")
        user_id = worker.id
    response = client.post(f"/settings/security/users/{user_id}/email", data={
        "new_email": "correct-address@test.local",
        "admin_password": "AdminPassword123!",
        "reason": "Se digitó incorrectamente el correo",
    })
    assert response.status_code == 302
    with app.app_context():
        updated = account("correct-address@test.local")
        assert updated.id == user_id
        assert updated.collaborator.corporate_email == "correct-address@test.local"
        assert AuditLog.query.filter_by(action="editar_correo_usuario").count() == 1


def test_email_requires_password_and_unique_address(client, app):
    with app.app_context():
        worker_id = account("wrong-address@test.local").id
    for email, password in (
        ("security-admin@test.local", "AdminPassword123!"),
        ("correct-address@test.local", "WrongPassword!"),
        ("not-an-email", "AdminPassword123!"),
    ):
        response = client.post(f"/settings/security/users/{worker_id}/email", data={
            "new_email": email, "admin_password": password,
        })
        assert response.status_code == 302
        with app.app_context():
            assert account("wrong-address@test.local").id == worker_id


def test_delete_requires_all_confirmations(client, app):
    with app.app_context():
        worker_id = account("wrong-address@test.local").id
    attempts = (
        {"admin_password": "wrong"},
        {"confirm_word": "eliminar"},
        {"confirm_email": "other@test.local"},
        {"understand_history": ""},
        {"reason": "corto"},
    )
    for wrong in attempts:
        data = {
            "admin_password": "AdminPassword123!",
            "confirm_email": "wrong-address@test.local",
            "confirm_word": "ELIMINAR",
            "reason": "Cuenta de prueba creada por error",
            "understand_history": "yes",
        }
        data.update(wrong)
        result = client.post(f"/settings/security/users/{worker_id}/delete", data=data)
        assert result.status_code == 302
        with app.app_context():
            assert User.query.filter_by(id=worker_id).first() is not None


def test_unreferenced_user_and_collaborator_can_be_deleted(client, app):
    with app.app_context():
        worker = account("wrong-address@test.local")
        worker_id = worker.id
        collab_id = worker.collaborator.id
        values = dict(
            admin_password="AdminPassword123!",
            confirm_email=worker.email,
            confirm_word="ELIMINAR",
            reason="Cuenta de prueba creada por error",
            understand_history="yes",
        )
    result = client.post(f"/settings/security/users/{worker_id}/delete", data=values)
    assert result.status_code == 302
    with app.app_context():
        assert db.session.get(User, worker_id) is None
        assert db.session.get(Collaborator, collab_id) is None
        assert AuditLog.query.filter_by(action="eliminar_usuario_sin_historial").count() == 1


def test_delete_preserves_client_history(client, app):
    with app.app_context():
        worker = account("wrong-address@test.local")
        worker_id = worker.id
        db.session.add(Client(
            code="TEST-HIST-001", business_name="Empresa Test",
            contact_name="Contacto", owner_id=worker.collaborator.id,
            record_type="cliente", pipeline_stage="venta_cerrada",
            client_status="activo",
        ))
        db.session.commit()
        values = dict(
            admin_password="AdminPassword123!",
            confirm_email=worker.email, confirm_word="ELIMINAR",
            reason="Cuenta asociada a historial",
            understand_history="yes",
        )
    result = client.post(f"/settings/security/users/{worker_id}/delete", data=values)
    assert result.status_code == 302
    with app.app_context():
        assert db.session.get(User, worker_id) is not None
        assert Client.query.filter_by(code="TEST-HIST-001").count() == 1


def test_admin_cannot_delete_self_or_superadmin(client, app):
    with app.app_context():
        admin = account("security-admin@test.local")
        superadmin = User.query.join(Role).filter(Role.name == "superadmin").first()
        assert superadmin is not None
        ids = [(admin.id, admin.email), (superadmin.id, superadmin.email)]
    for target_id, email in ids:
        result = client.post(f"/settings/security/users/{target_id}/delete", data=dict(
            admin_password="AdminPassword123!",
            confirm_email=email, confirm_word="ELIMINAR",
            reason="Solicitud que debe ser rechazada",
            understand_history="yes",
        ))
        assert result.status_code == 302
        with app.app_context():
            assert db.session.get(User, target_id) is not None


def test_manager_cannot_use_sensitive_routes(app):
    with app.app_context():
        user = User(
            name="Gerente", email="manager-test@test.local",
            role=Role.query.filter_by(name="manager").one(), active=True,
        )
        user.set_password("ManagerPassword123!")
        db.session.add(user)
        db.session.commit()
        worker_id = account("wrong-address@test.local").id
    manager = app.test_client()
    assert manager.post("/auth/login", data={
        "email": "manager-test@test.local",
        "password": "ManagerPassword123!",
    }).status_code == 302
    assert manager.post(f"/settings/security/users/{worker_id}/email", data={
        "new_email": "other@test.local",
        "admin_password": "ManagerPassword123!",
    }).status_code == 403
    assert manager.post(f"/settings/security/users/{worker_id}/delete", data={
        "confirm_word": "ELIMINAR",
        "admin_password": "ManagerPassword123!",
    }).status_code == 403
