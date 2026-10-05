from datetime import date
from pathlib import Path

import pytest

from app import create_app
from app.extensions import db
from app.models import Collaborator, Role, SystemSetting, User
from scripts.seed import seed_all


class TestConfig:
    TESTING = True
    SECRET_KEY = "acceptance-test-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    WTF_CSRF_TIME_LIMIT = None
    UPLOAD_FOLDER = "/tmp/nexora-v123-uploads"
    BACKUP_DIR = "/tmp/nexora-v123-backups"
    MAX_CONTENT_LENGTH = 10 * 1024 * 1024
    MAX_FILE_UPLOAD_MB = 10
    LIST_PAGE_SIZE = 25
    LIST_MAX_PAGE_SIZE = 100
    COMPANY_NAME = "Impacto Media Agency"
    COMPANY_TIMEZONE = "America/Managua"
    SMTP_HOST = None
    SMTP_PORT = 587
    SMTP_USER = None
    SMTP_PASSWORD = None
    SMTP_FROM = "test@example.com"
    SMTP_USE_TLS = False
    SESSION_COOKIE_SECURE = False
    REMEMBER_COOKIE_SECURE = False


@pytest.fixture()
def app(tmp_path):
    TestConfig.UPLOAD_FOLDER = str(tmp_path / "uploads")
    TestConfig.BACKUP_DIR = str(tmp_path / "backups")

    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        seed_all()

        admin_role = Role.query.filter_by(name="admin").one()
        advisor_role = Role.query.filter_by(name="advisor").one()

        admin = User(
            name="Admin Acceptance",
            email="admin-acceptance@test.local",
            role=admin_role,
            active=True,
        )
        admin.set_password("Test123!")
        db.session.add(admin)
        db.session.flush()
        db.session.add(
            Collaborator(
                user_id=admin.id,
                code="ACC-ADM",
                job_title="Administración",
                department="Administración",
                status="activo",
                join_date=date(2026, 1, 1),
            )
        )

        advisor = User(
            name="Advisor Acceptance",
            email="advisor-acceptance@test.local",
            role=advisor_role,
            active=True,
        )
        advisor.set_password("Test123!")
        db.session.add(advisor)
        db.session.flush()
        db.session.add(
            Collaborator(
                user_id=advisor.id,
                code="ACC-ADV",
                job_title="Asesor",
                department="Ventas",
                status="activo",
                join_date=date(2026, 1, 1),
            )
        )

        db.session.commit()

    yield app


@pytest.fixture()
def client(app):
    return app.test_client()


def _login(client, email):
    response = client.post(
        "/auth/login",
        data={"email": email, "password": "Test123!"},
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}


def test_acceptance_center_loads_for_admin(client):
    _login(client, "admin-acceptance@test.local")
    response = client.get("/acceptance/")
    assert response.status_code == 200
    assert b"Aceptaci" in response.data
    assert b"Ambiente de pruebas separado" in response.data


def test_advisor_cannot_open_acceptance_center(client):
    _login(client, "advisor-acceptance@test.local")
    response = client.get("/acceptance/")
    assert response.status_code == 403


def test_admin_can_record_manual_acceptance_with_audit(client, app):
    _login(client, "admin-acceptance@test.local")
    response = client.post(
        "/acceptance/criterion/desktop_mobile",
        data={
            "status": "aprobado",
            "notes": "Probado en PC y móvil.",
        },
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}

    with app.app_context():
        row = SystemSetting.query.filter_by(
            key="acceptance:v1.23:desktop_mobile"
        ).one()
        assert '"status": "aprobado"' in row.value
        assert "Probado en PC y móvil." in row.value


def test_invalid_acceptance_status_is_rejected(client):
    _login(client, "admin-acceptance@test.local")
    response = client.post(
        "/acceptance/criterion/desktop_mobile",
        data={"status": "inventado", "notes": ""},
        follow_redirects=False,
    )
    assert response.status_code == 400


def test_unknown_acceptance_criterion_is_404(client):
    _login(client, "admin-acceptance@test.local")
    response = client.post(
        "/acceptance/criterion/no-existe",
        data={"status": "aprobado", "notes": ""},
        follow_redirects=False,
    )
    assert response.status_code == 404


def test_acceptance_report_exports_txt(client):
    _login(client, "admin-acceptance@test.local")
    response = client.get("/acceptance/export.txt")
    assert response.status_code == 200
    assert response.mimetype == "text/plain"
    assert b"REPORTE DE ACEPTACION FINAL" in response.data
    assert b"CRITERIOS MANUALES" in response.data
