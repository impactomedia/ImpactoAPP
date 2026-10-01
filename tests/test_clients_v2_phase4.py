import json
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape
import zipfile

import pytest

from app import create_app
from app.extensions import db
from app.models import (
    Client,
    ClientContract,
    Collaborator,
    Renewal,
    Role,
    Sale,
    User,
)
from app.client_excel_parser import parse_operational_workbook
from app.client_v2_models import (
    ClientImportBatch,
    ClientOperationalProfile,
    ClientPlatform,
)
from scripts.seed import seed_all


class TestConfig:
    TESTING = True
    SECRET_KEY = "test-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    WTF_CSRF_TIME_LIMIT = None
    UPLOAD_FOLDER = str(Path(__file__).parent / "uploads_phase4")
    MAX_CONTENT_LENGTH = 20 * 1024 * 1024
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
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        seed_all()
        _create_user(
            "admin",
            "admin-p4@test.local",
            "Administrador P4",
        )
        _create_user(
            "advisor",
            "jessy-p4@test.local",
            "Jessenia Montalvan",
        )
        _create_user(
            "advisor",
            "advisor-p4@test.local",
            "Asesor P4",
        )
        db.session.commit()
    yield app


@pytest.fixture()
def client(app):
    return app.test_client()


def _create_user(role_name, email, name):
    role = Role.query.filter_by(name=role_name).one()
    user = User(
        name=name,
        email=email,
        role=role,
        active=True,
    )
    user.set_password("Test123!")
    db.session.add(user)
    db.session.flush()

    collaborator = Collaborator(
        user_id=user.id,
        code=f"P4-{user.id:03d}",
        job_title=name,
        department="Pruebas",
        status="activo",
    )
    db.session.add(collaborator)
    db.session.flush()
    return user, collaborator


def _login(client, email):
    response = client.post(
        "/auth/login",
        data={
            "email": email,
            "password": "Test123!",
        },
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}


def _cell(ref, value):
    if isinstance(value, (int, float)):
        return f'<c r="{ref}"><v>{value}</v></c>'
    return (
        f'<c r="{ref}" t="inlineStr"><is><t>'
        f'{escape(str(value))}'
        f'</t></is></c>'
    )


def _xlsx_bytes(
    business="Empresa Demo",
    address="Dirección importada",
):
    cells = {
        "B1": "#1",
        "B2": "3 MESES",
        "B3": "Cliente Demo",
        "B4": "COMPAÑÍA:",
        "C4": business,
        "B5": "ASESOR:",
        "C5": "JESSENIA",
        "B6": "CONTACTO PPAL:",
        "C6": "(305) 555-1212",
        "B7": "# SECUNDARIO:",
        "C7": "(305) 555-3434",
        "B8": "CLAVE:",
        "C8": "SuperSecretOne",
        "B9": "CORREO:",
        "C9": "demo@example.com",
        "B10": "DIRECCIÓN:",
        "C10": address,
        "B11": "WEBSITE:",
        "C11": "example.com",
        "B12": "GOOGLE",
        "C12": "Pendiente de verificación",
        "B13": "FECHA ACTIVACIÓN DEL PLAN",
        "C13": "01/10/2026",
        "B14": "FECHA EXPIRACIÓN DEL PLAN",
        "C14": "01/01/2027",
        "B15": "ACTIVACIÓN DEL DOMINIO WEB:",
        "C15": "Dominio:01/10/26 - Hosting:02/10/26",
        "B17": "BENEFICIOS",
        "B18": "Website\nGoogle Business Profile",
        "B19": "CORTESÍA",
        "C19": "500 Business Cards",
        "B21": "PLATAFORMAS",
        "C21": "LINKS",
        "B22": "Facebook",
        "C22": "https://facebook.com/demo",
        "B24": "Youtube",
        "C24": "https://youtube.com/@demo",
        "B35": "DÍAS DE ATENCIÓN:",
        "C35": "Lunes a sábado",
        "B36": "HORARIOS:",
        "C36": "8am - 5pm",
        "B37": "TIEMPO DE EXPERIENCIA:",
        "C37": "10 años",
        "B38": "MILLAJE DE COBERTURA:",
        "C38": 50,
        "B39": "MÉTODOS DE PAGO:",
        "C39": "Efectivo, Zelle",
        "B40": "¿ESTIMACIONES GRATIS?:",
        "C40": "Sí",
        "B41": "CORREO OPERATIVO (US):",
        "C41": "ops@example.com",
        "B42": "CONTRASEÑA DEL CORREO:",
        "C42": "SuperSecretTwo",
        "B43": "CORREO CORPORATIVO",
        "C43": "info@example.com",
        "B44": "SERVICIOS A EXPONER:",
        "C44": "Landscaping\nTree Removal",
        "B45": "STATUS DE LOGOTIPO:",
        "C45": "Creado por la compañía",
        "B46": "COLORES:",
        "C46": "Verde, negro",
        "B47": "1era INVERSIÓN:",
        "B48": "PROYECTO ACTIVO:",
        "C48": "3 MESES",
        "B49": "COSTO DEL PROYECTO:",
        "C49": 550,
        "B50": "INVERCIÓN PAGADA",
        "C50": 550,
        "B52": "STATUS DE PAGO",
        "C52": "SOLVENTE",
    }

    rows = {}
    for ref, value in cells.items():
        row_number = int("".join(ch for ch in ref if ch.isdigit()))
        rows.setdefault(row_number, []).append(_cell(ref, value))

    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetData>'
        + "".join(
            f'<row r="{row}">{"".join(rows[row])}</row>'
            for row in sorted(rows)
        )
        + '</sheetData></worksheet>'
    )

    workbook_xml = '''<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
 <sheets>
  <sheet name="CLIENTES" sheetId="1" r:id="rId1"/>
  <sheet name="TAREAS" sheetId="2" r:id="rId2"/>
  <sheet name="CONTRASEÑAS" sheetId="3" r:id="rId3"/>
 </sheets>
</workbook>'''

    rels_xml = '''<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Id="rId1" Target="worksheets/sheet1.xml"/>
 <Relationship Id="rId2" Target="worksheets/sheet2.xml"/>
 <Relationship Id="rId3" Target="worksheets/sheet3.xml"/>
</Relationships>'''

    stream = BytesIO()
    with zipfile.ZipFile(
        stream,
        "w",
        zipfile.ZIP_DEFLATED,
    ) as zf:
        zf.writestr("xl/workbook.xml", workbook_xml)
        zf.writestr(
            "xl/_rels/workbook.xml.rels",
            rels_xml,
        )
        zf.writestr(
            "xl/worksheets/sheet1.xml",
            sheet_xml,
        )

    return stream.getvalue()


def _upload_batch(client, workbook_bytes):
    response = client.post(
        "/clients/import-workbook/",
        data={
            "file": (
                BytesIO(workbook_bytes),
                "fichas_operativas.xlsx",
            )
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}


def _execute_latest_batch(client, **options):
    with client.application.app_context():
        batch_id = (
            ClientImportBatch.query
            .order_by(ClientImportBatch.id.desc())
            .first()
            .id
        )

    data = {
        "confirm_import": "on",
        "create_missing": "on",
        "import_contracts": "on",
    }
    data.update(options)

    response = client.post(
        f"/clients/import-workbook/{batch_id}/execute",
        data=data,
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}
    return batch_id


def test_parser_excludes_password_sheet_and_secret_fields():
    payload = parse_operational_workbook(
        _xlsx_bytes(),
        "fichas_operativas.xlsx",
    )
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
    )

    assert payload["client_count"] == 1
    assert "TAREAS" in payload["excluded_sheets"]
    assert "CONTRASEÑAS" in payload["excluded_sheets"]
    assert payload["security"]["secret_fields_imported"] is False
    assert payload["security"]["secret_fields_skipped"] == 2
    assert "SuperSecretOne" not in serialized
    assert "SuperSecretTwo" not in serialized


def test_admin_upload_creates_sanitized_preview(client, app):
    _login(client, "admin-p4@test.local")
    _upload_batch(client, _xlsx_bytes())

    with app.app_context():
        batch = ClientImportBatch.query.one()
        serialized = json.dumps(
            batch.payload_json,
            ensure_ascii=False,
        )
        assert batch.status == "previewed"
        assert batch.client_count == 1
        assert "SuperSecretOne" not in serialized
        assert "SuperSecretTwo" not in serialized


def test_execute_import_creates_client_profile_platform_and_safe_contract(client, app):
    _login(client, "admin-p4@test.local")
    _upload_batch(client, _xlsx_bytes())
    batch_id = _execute_latest_batch(client)

    with app.app_context():
        customer = Client.query.filter_by(
            email="demo@example.com"
        ).one()
        assert customer.business_name == "Empresa Demo"
        assert customer.website == "https://example.com"
        assert customer.facebook == "https://facebook.com/demo"
        assert customer.owner.user.name == "Jessenia Montalvan"

        profile = ClientOperationalProfile.query.filter_by(
            client_id=customer.id
        ).one()
        assert profile.attention_days == "Lunes a sábado"
        assert profile.coverage_text == "50 millas"
        assert profile.operational_email == "ops@example.com"

        youtube = ClientPlatform.query.filter_by(
            client_id=customer.id,
            platform_key="youtube",
        ).one()
        assert youtube.url == "https://youtube.com/@demo"

        google = ClientPlatform.query.filter_by(
            client_id=customer.id,
            platform_key="google_business",
        ).one()
        assert google.status == "pendiente"

        contract = ClientContract.query.filter_by(
            client_id=customer.id
        ).one()
        assert contract.product.name == "3 Meses"
        assert Renewal.query.filter_by(
            client_id=customer.id,
            contract_id=contract.id,
        ).count() == 1

        assert Sale.query.filter_by(
            client_id=customer.id
        ).count() == 0

        batch = db.session.get(ClientImportBatch, batch_id)
        assert batch.status == "completed"
        assert batch.result_json["financial_rows_imported"] == 0


def test_reimport_same_workbook_does_not_duplicate_client_or_contract(client, app):
    _login(client, "admin-p4@test.local")

    first = _xlsx_bytes()
    _upload_batch(client, first)
    _execute_latest_batch(client)

    _upload_batch(client, first)
    _execute_latest_batch(client)

    with app.app_context():
        assert Client.query.filter_by(
            email="demo@example.com"
        ).count() == 1

        customer = Client.query.filter_by(
            email="demo@example.com"
        ).one()
        assert ClientContract.query.filter_by(
            client_id=customer.id
        ).count() == 1


def test_default_import_does_not_overwrite_existing_data(client, app):
    with app.app_context():
        owner = User.query.filter_by(
            email="advisor-p4@test.local"
        ).one().collaborator
        customer = Client(
            code="P4-EXISTING",
            business_name="Empresa Demo",
            contact_name="Contacto Actual",
            phone="(305) 555-1212",
            email="demo@example.com",
            address="Dirección original",
            owner_id=owner.id,
            record_type="cliente",
            pipeline_stage="venta_cerrada",
            client_status="activo",
        )
        db.session.add(customer)
        db.session.commit()

    _login(client, "admin-p4@test.local")
    _upload_batch(
        client,
        _xlsx_bytes(address="Dirección nueva"),
    )
    _execute_latest_batch(client)

    with app.app_context():
        customer = Client.query.filter_by(
            email="demo@example.com"
        ).one()
        assert customer.address == "Dirección original"
        assert customer.owner.user.email == "advisor-p4@test.local"


def test_advisor_cannot_access_operational_workbook_import(client):
    _login(client, "advisor-p4@test.local")
    response = client.get(
        "/clients/import-workbook/",
        follow_redirects=False,
    )
    assert response.status_code == 403
