from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app import create_app
from app.extensions import db
from app.models import Client, Collaborator, Payment, Permission, Role, Sale, SystemSetting, User
from app.security_controls import (
    save_permission_scope,
    save_temporary_client_grant,
    set_two_factor,
)
from scripts.seed import seed_all


class TestConfig:
    TESTING = True
    SECRET_KEY = "block10-test-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    WTF_CSRF_TIME_LIMIT = None
    UPLOAD_FOLDER = str(Path(__file__).parent / "uploads_block10")
    MAX_CONTENT_LENGTH = 10 * 1024 * 1024
    COMPANY_NAME = "Impacto Media Agency"
    COMPANY_TIMEZONE = "America/Managua"
    SMTP_HOST = "smtp.test.local"
    SMTP_PORT = 587
    SMTP_USER = "test@test.local"
    SMTP_PASSWORD = "secret"
    SMTP_FROM = "test@test.local"
    SMTP_USE_TLS = False
    SESSION_COOKIE_SECURE = False
    REMEMBER_COOKIE_SECURE = False


@pytest.fixture()
def app():
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        seed_all()

        admin = _user("admin", "admin-b10@test.local", "Admin B10", "Administración")
        advisor = _user("advisor", "advisor-b10@test.local", "Asesor B10", "Ventas")
        other = _user("advisor", "other-b10@test.local", "Otro Asesor B10", "Ventas")

        own_client = Client(
            code="B10-OWN",
            business_name="Cliente Propio B10",
            contact_name="Contacto",
            owner_id=advisor.collaborator.id,
            record_type="cliente",
            client_status="activo",
            pipeline_stage="venta_cerrada",
        )
        other_client = Client(
            code="B10-OTHER",
            business_name="Cliente Ajeno B10",
            contact_name="Contacto",
            owner_id=other.collaborator.id,
            record_type="cliente",
            client_status="activo",
            pipeline_stage="venta_cerrada",
        )
        db.session.add_all([own_client, other_client])
        db.session.flush()

        sale = Sale(
            sale_no="B10-VEN-001",
            client_id=own_client.id,
            advisor_id=advisor.collaborator.id,
            sale_date=date.today(),
            status="confirmada",
            currency="USD",
            total=Decimal("100"),
            amount_paid=Decimal("100"),
            balance=Decimal("0"),
        )
        db.session.add(sale)
        db.session.flush()
        payment = Payment(
            client_id=own_client.id,
            sale_id=sale.id,
            effective_date=date.today(),
            amount=Decimal("100"),
            currency="USD",
            method="Zelle",
            status="confirmado",
        )
        db.session.add(payment)
        db.session.commit()

    yield app


@pytest.fixture()
def client(app):
    return app.test_client()


def _user(role_name, email, name, department):
    role = Role.query.filter_by(name=role_name).one()
    user = User(name=name, email=email, role=role, active=True)
    user.set_password("Test123!")
    db.session.add(user)
    db.session.flush()

    collaborator = Collaborator(
        user_id=user.id,
        code=f"B10-{user.id}",
        job_title=name,
        department=department,
        status="activo",
        join_date=date.today(),
    )
    db.session.add(collaborator)
    db.session.flush()
    return user


def _login(client, email, password="Test123!"):
    return client.post(
        "/auth/login",
        data={"email": email, "password": password},
        follow_redirects=False,
    )


def _set_setting(key, value):
    row = SystemSetting.query.filter_by(key=key).first()
    if not row:
        row = SystemSetting(key=key)
        db.session.add(row)
    row.value = str(value)
    db.session.commit()


def test_configurable_failed_login_lock(app, client):
    with app.app_context():
        _set_setting("security_failed_attempt_limit", 3)
        _set_setting("security_lock_minutes", 30)

    for _ in range(3):
        response = _login(client, "advisor-b10@test.local", "wrong-password")
        assert response.status_code == 200

    with app.app_context():
        user = User.query.filter_by(email="advisor-b10@test.local").one()
        assert user.failed_attempts == 3
        assert user.locked_until is not None
        assert user.locked_until > datetime.utcnow() + timedelta(minutes=20)


def test_session_expires_after_configured_inactivity(app, client):
    with app.app_context():
        _set_setting("security_session_timeout_minutes", 5)

    assert _login(client, "admin-b10@test.local").status_code in {302, 303}

    with client.session_transaction() as session:
        session["security_last_activity"] = (
            datetime.utcnow() - timedelta(minutes=6)
        ).timestamp()

    response = client.get("/dashboard/", follow_redirects=False)
    assert response.status_code in {302, 303}
    assert "/auth/login" in response.headers["Location"]


def test_optional_email_2fa_requires_code(app, client, monkeypatch):
    with app.app_context():
        user = User.query.filter_by(email="advisor-b10@test.local").one()
        set_two_factor(user, True)
        db.session.commit()

    monkeypatch.setattr("app.blueprints.auth.send_email", lambda *args, **kwargs: True)
    monkeypatch.setattr("app.security_controls.secrets.randbelow", lambda _n: 23456)

    response = _login(client, "advisor-b10@test.local")
    assert response.status_code in {302, 303}
    assert "/auth/verify-2fa" in response.headers["Location"]

    # Password correcto aún no autentica sin el segundo factor.
    response = client.get("/dashboard/", follow_redirects=False)
    assert response.status_code in {302, 303}

    response = client.post(
        "/auth/verify-2fa",
        data={"code": "123456"},
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}
    assert "/dashboard" in response.headers["Location"]


def test_admin_deactivation_revokes_existing_session(app):
    advisor_client = app.test_client()
    admin_client = app.test_client()

    assert _login(advisor_client, "advisor-b10@test.local").status_code in {302, 303}
    assert _login(admin_client, "admin-b10@test.local").status_code in {302, 303}

    with app.app_context():
        advisor = User.query.filter_by(email="advisor-b10@test.local").one()
        advisor_id = advisor.id

    response = admin_client.post(
        f"/settings/security/users/{advisor_id}/status",
        data={"status": "inactive", "reason": "Prueba de revocación inmediata"},
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}

    response = advisor_client.get("/dashboard/", follow_redirects=False)
    assert response.status_code in {302, 303}
    assert "/auth/login" in response.headers["Location"]

    with app.app_context():
        assert db.session.get(User, advisor_id).active is False


def test_payment_reversal_requires_reason(app, client):
    assert _login(client, "admin-b10@test.local").status_code in {302, 303}

    with app.app_context():
        payment_id = Payment.query.filter_by(status="confirmado").one().id

    response = client.post(
        f"/sales/payments/{payment_id}/reverse",
        data={},
        follow_redirects=False,
    )
    assert response.status_code == 400


def test_security_admin_can_unlock_account(app, client):
    with app.app_context():
        user = User.query.filter_by(email="advisor-b10@test.local").one()
        user.failed_attempts = 5
        user.locked_until = datetime.utcnow() + timedelta(minutes=15)
        db.session.commit()
        user_id = user.id

    assert _login(client, "admin-b10@test.local").status_code in {302, 303}
    response = client.post(
        f"/settings/security/users/{user_id}/unlock",
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}

    with app.app_context():
        user = db.session.get(User, user_id)
        assert user.failed_attempts == 0
        assert user.locked_until is None


def test_custom_role_scope_is_safe_by_default(app, client):
    with app.app_context():
        permission = Permission.query.filter_by(code="clients.view").one()
        role = Role(
            name="custom_portfolio",
            label="Cartera personalizada",
            active=True,
        )
        role.permissions = [permission]
        db.session.add(role)
        db.session.flush()

        user = User(
            name="Usuario Custom",
            email="custom-b10@test.local",
            role=role,
            active=True,
        )
        user.set_password("Test123!")
        db.session.add(user)
        db.session.flush()
        collaborator = Collaborator(
            user_id=user.id,
            code="B10-CUSTOM",
            job_title="Custom",
            department="Ventas",
            status="activo",
            join_date=date.today(),
        )
        db.session.add(collaborator)
        db.session.flush()

        own = Client(
            code="B10-CUSTOM-OWN",
            business_name="Custom Visible",
            contact_name="Contacto",
            owner_id=collaborator.id,
            record_type="cliente",
            client_status="activo",
        )
        other = Client(
            code="B10-CUSTOM-OTHER",
            business_name="Custom Oculto",
            contact_name="Contacto",
            record_type="cliente",
            client_status="activo",
        )
        db.session.add_all([own, other])
        db.session.flush()
        save_permission_scope(role.name, permission.code, "own")
        db.session.commit()
        own_id = own.id
        other_id = other.id

    assert _login(client, "custom-b10@test.local").status_code in {302, 303}

    dashboard = client.get("/dashboard/")
    assert dashboard.status_code == 200
    assert b"Panel seguro" in dashboard.data
    assert b"Custom Oculto" not in dashboard.data

    # Un listado global no filtrado se bloquea para un rol personalizado scoped.
    assert client.get("/clients/", follow_redirects=False).status_code == 403
    assert client.get(f"/clients/{own_id}", follow_redirects=False).status_code == 200
    assert client.get(f"/clients/{other_id}", follow_redirects=False).status_code == 403


def test_enhanced_audit_filters_and_export(client):
    assert _login(client, "admin-b10@test.local").status_code in {302, 303}

    response = client.get("/settings/security/audit?action=login")
    assert response.status_code == 200
    assert b"Bit\xc3\xa1cora del sistema" in response.data

    response = client.get("/settings/security/audit.csv?action=login")
    assert response.status_code == 200
    assert response.mimetype.startswith("text/csv")


def test_temporary_scope_expires_automatically(app, client):
    with app.app_context():
        permission = Permission.query.filter_by(code="clients.view").one()
        role = Role(
            name="temporary_portfolio",
            label="Acceso temporal",
            active=True,
        )
        role.permissions = [permission]
        db.session.add(role)
        db.session.flush()

        user = User(
            name="Usuario Temporal",
            email="temporary-b10@test.local",
            role=role,
            active=True,
        )
        user.set_password("Test123!")
        db.session.add(user)
        db.session.flush()
        collaborator = Collaborator(
            user_id=user.id,
            code="B10-TEMP",
            job_title="Temporal",
            department="Ventas",
            status="activo",
            join_date=date.today(),
        )
        db.session.add(collaborator)
        db.session.flush()

        allowed_client = Client.query.filter_by(code="B10-OWN").one()
        denied_client = Client.query.filter_by(code="B10-OTHER").one()

        save_permission_scope(role.name, permission.code, "temporary")
        save_temporary_client_grant(
            user.id,
            permission.code,
            allowed_client.id,
            datetime.utcnow() + timedelta(hours=1),
        )
        db.session.commit()
        allowed_id = allowed_client.id
        denied_id = denied_client.id

    assert _login(client, "temporary-b10@test.local").status_code in {302, 303}
    assert client.get(f"/clients/{allowed_id}", follow_redirects=False).status_code == 200
    assert client.get(f"/clients/{denied_id}", follow_redirects=False).status_code == 403

    client.post("/auth/logout")
    with app.app_context():
        # Sobrescribe el mismo grant con expiración pasada para simular vencimiento.
        key = f"temporary_scope:{User.query.filter_by(email='temporary-b10@test.local').one().id}:clients.view:{allowed_id}"
        row = SystemSetting.query.filter_by(key=key).one()
        row.value = (datetime.utcnow() - timedelta(minutes=1)).isoformat()
        db.session.commit()

    assert _login(client, "temporary-b10@test.local").status_code in {302, 303}
    assert client.get(f"/clients/{allowed_id}", follow_redirects=False).status_code == 403
