from datetime import date
from io import BytesIO
from pathlib import Path
import json
import zipfile

import pytest

from app import create_app
from app.data_exchange import parse_tabular_file
from app.extensions import db
from app.models import Client, Collaborator, Role, User
from app.client_v2_models import ClientImportBatch
from scripts.backup import create_backup
from scripts.restore_backup import validate_backup
from scripts.seed import seed_all


class TestConfig:
    TESTING = True
    SECRET_KEY = "block12-test"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    WTF_CSRF_TIME_LIMIT = None
    UPLOAD_FOLDER = str(Path(__file__).parent / "uploads_block12")
    BACKUP_DIR = str(Path(__file__).parent / "backups_block12")
    MAX_CONTENT_LENGTH = 10 * 1024 * 1024
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
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'backup_source.db'}")

    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        seed_all()

        admin_role = Role.query.filter_by(name="admin").one()
        advisor_role = Role.query.filter_by(name="advisor").one()

        admin = User(
            name="Admin B12",
            email="admin-b12@test.local",
            role=admin_role,
            active=True,
        )
        admin.set_password("Test123!")
        db.session.add(admin)
        db.session.flush()
        admin_collab = Collaborator(
            user_id=admin.id,
            code="B12-ADM",
            job_title="Administración",
            department="Administración",
            status="activo",
            join_date=date.today(),
        )
        db.session.add(admin_collab)

        advisor_a = User(
            name="Asesor Uno",
            email="advisor-one-b12@test.local",
            role=advisor_role,
            active=True,
        )
        advisor_a.set_password("Test123!")
        db.session.add(advisor_a)
        db.session.flush()
        collab_a = Collaborator(
            user_id=advisor_a.id,
            code="B12-A01",
            job_title="Asesor",
            department="Ventas",
            status="activo",
            join_date=date.today(),
        )
        db.session.add(collab_a)

        advisor_b = User(
            name="Asesor Dos",
            email="advisor-two-b12@test.local",
            role=advisor_role,
            active=True,
        )
        advisor_b.set_password("Test123!")
        db.session.add(advisor_b)
        db.session.flush()
        collab_b = Collaborator(
            user_id=advisor_b.id,
            code="B12-A02",
            job_title="Asesor",
            department="Ventas",
            status="activo",
            join_date=date.today(),
        )
        db.session.add(collab_b)
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


def _minimal_xlsx():
    workbook = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
      xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
      <sheets><sheet name="Datos" sheetId="1" r:id="rId1"/></sheets>
    </workbook>"""

    rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
      <Relationship Id="rId1"
        Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"
        Target="worksheets/sheet1.xml"/>
    </Relationships>"""

    sheet = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
      <sheetData>
        <row r="1">
          <c r="A1" t="inlineStr"><is><t>business_name</t></is></c>
          <c r="B1" t="inlineStr"><is><t>contact_name</t></is></c>
          <c r="C1" t="inlineStr"><is><t>email</t></is></c>
        </row>
        <row r="2">
          <c r="A2" t="inlineStr"><is><t>Cliente XLSX</t></is></c>
          <c r="B2" t="inlineStr"><is><t>Contacto XLSX</t></is></c>
          <c r="C2" t="inlineStr"><is><t>xlsx@example.com</t></is></c>
        </row>
      </sheetData>
    </worksheet>"""

    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("xl/workbook.xml", workbook)
        zf.writestr("xl/_rels/workbook.xml.rels", rels)
        zf.writestr("xl/worksheets/sheet1.xml", sheet)
    return buffer.getvalue()


def test_generic_xlsx_parser_reads_first_sheet():
    parsed = parse_tabular_file(_minimal_xlsx(), "plantilla.xlsx")
    assert parsed["format"] == "xlsx"
    assert parsed["rows"][0]["business_name"] == "Cliente XLSX"
    assert parsed["rows"][0]["email"] == "xlsx@example.com"


def test_csv_preview_detects_duplicate_and_rejected(client, app):
    with app.app_context():
        advisor = User.query.filter_by(email="advisor-one-b12@test.local").one().collaborator
        db.session.add(
            Client(
                code="CLI-DUP",
                business_name="Duplicado LLC",
                contact_name="Contacto",
                phone="4075550000",
                email="dup@example.com",
                country="USA",
                owner_id=advisor.id,
                record_type="seguimiento",
                pipeline_stage="nuevo",
                client_status="activo",
            )
        )
        db.session.commit()

    _login(client, "admin-b12@test.local")
    csv_data = (
        "business_name,contact_name,phone,email\n"
        "Duplicado LLC,Uno,4075550000,dup@example.com\n"
        ",Sin negocio,4075551111,rejected@example.com\n"
        "Nuevo LLC,Nuevo,4075552222,nuevo@example.com\n"
    ).encode()

    response = client.post(
        "/data/import",
        data={"file": (BytesIO(csv_data), "clientes.csv")},
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}

    with app.app_context():
        batch = ClientImportBatch.query.order_by(ClientImportBatch.id.desc()).first()
        assert batch.payload_json["kind"] == "general_contacts_v12"
        assert batch.payload_json["summary"] == {
            "ready": 1,
            "duplicate": 1,
            "rejected": 1,
        }


def test_confirm_import_applies_bulk_owner_source_stage(client, app):
    _login(client, "admin-b12@test.local")
    csv_data = (
        "business_name,contact_name,phone,email\n"
        "Prospecto Importado,Contacto,4075553333,importado@example.com\n"
    ).encode()

    client.post(
        "/data/import",
        data={"file": (BytesIO(csv_data), "prospectos.csv")},
        content_type="multipart/form-data",
    )

    with app.app_context():
        batch = ClientImportBatch.query.order_by(ClientImportBatch.id.desc()).first()
        batch_id = batch.id
        advisor_id = User.query.filter_by(
            email="advisor-one-b12@test.local"
        ).one().collaborator.id

    response = client.post(
        f"/data/import/{batch_id}/execute",
        data={
            "confirm_import": "on",
            "record_type": "seguimiento",
            "owner_id": str(advisor_id),
            "source": "Referido",
            "pipeline_stage": "seguimiento",
        },
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}

    with app.app_context():
        row = Client.query.filter_by(email="importado@example.com").one()
        assert row.record_type == "seguimiento"
        assert row.owner_id == advisor_id
        assert row.source == "Referido"
        assert row.pipeline_stage == "seguimiento"

        batch = db.session.get(ClientImportBatch, batch_id)
        assert batch.status == "completed"
        assert batch.result_json["created"] == 1


def test_advisor_export_only_contains_own_clients(client, app):
    with app.app_context():
        a = User.query.filter_by(email="advisor-one-b12@test.local").one().collaborator
        b = User.query.filter_by(email="advisor-two-b12@test.local").one().collaborator
        db.session.add_all([
            Client(
                code="CLI-A",
                business_name="Cliente Propio",
                contact_name="A",
                email="a-client@example.com",
                country="USA",
                owner_id=a.id,
                record_type="cliente",
                pipeline_stage="venta_cerrada",
                client_status="activo",
            ),
            Client(
                code="CLI-B",
                business_name="Cliente Ajeno",
                contact_name="B",
                email="b-client@example.com",
                country="USA",
                owner_id=b.id,
                record_type="cliente",
                pipeline_stage="venta_cerrada",
                client_status="activo",
            ),
        ])
        db.session.commit()

    _login(client, "advisor-one-b12@test.local")
    response = client.get("/data/export/clients.csv")
    assert response.status_code == 200
    text = response.data.decode("utf-8-sig")
    assert "Cliente Propio" in text
    assert "Cliente Ajeno" not in text


def test_sqlite_backup_archive_is_valid(app, tmp_path, monkeypatch):
    source = tmp_path / "backup_source.db"
    source.write_bytes(b"SQLite format 3\\x00dummy")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{source}")
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setenv("UPLOAD_FOLDER", str(tmp_path / "uploads"))

    path = create_backup()
    manifest = validate_backup(path)

    assert path.exists()
    assert manifest["database_engine"] == "sqlite"
    assert manifest["database_file"].endswith(".sqlite3")
