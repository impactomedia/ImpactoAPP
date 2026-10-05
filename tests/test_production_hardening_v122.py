from datetime import date
from io import BytesIO
from pathlib import Path

import pytest
from werkzeug.datastructures import FileStorage

from app import create_app
from app.extensions import db
from app.helpers import save_upload
from app.models import Client, Collaborator, Role, User
from scripts.seed import seed_all


class TestConfig:
    TESTING = True
    SECRET_KEY = "block15-test"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    WTF_CSRF_TIME_LIMIT = None
    MAX_CONTENT_LENGTH = 2 * 1024 * 1024
    MAX_FILE_UPLOAD_MB = 1
    LIST_PAGE_SIZE = 25
    LIST_MAX_PAGE_SIZE = 100
    UPLOAD_FOLDER = "/tmp/nexora-v122-uploads"
    BACKUP_DIR = "/tmp/nexora-v122-backups"
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
        admin = User(
            name="Admin Hardening",
            email="admin-hardening@test.local",
            role=admin_role,
            active=True,
        )
        admin.set_password("Test123!")
        db.session.add(admin)
        db.session.flush()

        collaborator = Collaborator(
            user_id=admin.id,
            code="HARD-ADM",
            job_title="Administración",
            department="Administración",
            status="activo",
            join_date=date(2026, 1, 1),
        )
        db.session.add(collaborator)
        db.session.flush()

        for number in range(1, 32):
            db.session.add(
                Client(
                    code=f"HARD-{number:03d}",
                    business_name=f"Hardening Client {number:03d}",
                    contact_name=f"Contacto {number:03d}",
                    email=f"hardening{number:03d}@example.test",
                    country="USA",
                    owner_id=collaborator.id,
                    record_type="cliente",
                    pipeline_stage="venta_cerrada",
                    client_status="activo",
                )
            )

        db.session.commit()

    yield app


@pytest.fixture()
def client(app):
    return app.test_client()


def _login(client):
    response = client.post(
        "/auth/login",
        data={
            "email": "admin-hardening@test.local",
            "password": "Test123!",
        },
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}


def test_healthcheck_pings_database(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.get_json()["status"] == "ok"
    assert response.headers.get("X-Request-ID")


def test_authenticated_html_has_no_store_and_security_headers(client):
    _login(client)
    response = client.get("/clients/?q=Hardening")
    assert response.status_code == 200
    assert "no-store" in response.headers.get("Cache-Control", "")
    assert response.headers.get("X-Request-ID")
    assert "default-src 'self'" in response.headers.get(
        "Content-Security-Policy",
        "",
    )


def test_clients_main_list_is_server_side_paginated(client):
    _login(client)

    first = client.get("/clients/?q=Hardening")
    assert first.status_code == 200
    assert first.data.count(b"Hardening Client") == 25
    assert b"31" in first.data
    assert b"Pagina 1 de 2" in first.data or b"P\xc3\xa1gina 1 de 2" in first.data

    second = client.get("/clients/?q=Hardening&page=2")
    assert second.status_code == 200
    assert second.data.count(b"Hardening Client") == 6


def test_upload_signature_rejects_spoofed_pdf(app):
    with app.app_context():
        fake = FileStorage(
            stream=BytesIO(b"esto no es un PDF"),
            filename="contrato.pdf",
            content_type="application/pdf",
        )
        with pytest.raises(ValueError):
            save_upload(fake, prefix="hardening")


def test_upload_signature_accepts_pdf_magic_and_saves_atomically(app):
    with app.app_context():
        file_storage = FileStorage(
            stream=BytesIO(b"%PDF-1.4\n% Nexora test\n"),
            filename="contrato.pdf",
            content_type="application/pdf",
        )
        stored = save_upload(file_storage, prefix="hardening")
        assert stored.startswith("uploads/hardening_")
        saved = Path(app.config["UPLOAD_FOLDER"]) / Path(stored).name
        assert saved.is_file()
        assert saved.read_bytes().startswith(b"%PDF-")


def test_upload_size_limit_is_per_file(app):
    with app.app_context():
        oversized = FileStorage(
            stream=BytesIO(b"a" * (1024 * 1024 + 1)),
            filename="evidencia.txt",
            content_type="text/plain",
        )
        with pytest.raises(ValueError):
            save_upload(oversized, prefix="hardening")


def test_mobile_hardening_assets_are_served(client):
    css = client.get("/static/css/hardening.css")
    js = client.get("/static/js/hardening.js")

    assert css.status_code == 200
    assert js.status_code == 200
    assert b"mobile-card-table" in css.data
    # El JavaScript usa DOMStringMap: cell.dataset.label genera
    # el atributo HTML data-label en cada celda.
    assert b"dataset.label" in js.data
