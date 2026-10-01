from datetime import date
from pathlib import Path

import pytest

from app import create_app
from app.extensions import db
from app.models import Client, ClientContract, Collaborator, ProductService, Project, Role, Task, User
from app.client_v2_models import ClientContractDetail, ClientOperationalProfile, ClientPlatform
from scripts.seed import seed_all


class TestConfig:
    TESTING = True
    SECRET_KEY = "test-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    WTF_CSRF_TIME_LIMIT = None
    UPLOAD_FOLDER = str(Path(__file__).parent / "uploads")
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
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        seed_all()
        _create_user("advisor", "advisor-v2@test.local", "Asesor V2")
        _create_user("advisor", "other-v2@test.local", "Otro asesor V2")
        _create_user("production", "production-v2@test.local", "Producción V2")
        db.session.commit()
    yield app


@pytest.fixture()
def client(app):
    return app.test_client()


def _create_user(role_name, email, name):
    role = Role.query.filter_by(name=role_name).one()
    user = User(name=name, email=email, role=role, active=True)
    user.set_password("Test123!")
    db.session.add(user)
    db.session.flush()
    collaborator = Collaborator(
        user_id=user.id,
        code=f"V2-{user.id:03d}",
        job_title=name,
        department="Pruebas",
        status="activo",
        join_date=date.today(),
    )
    db.session.add(collaborator)
    db.session.flush()
    return user, collaborator


def _login(client, email):
    response = client.post(
        "/auth/login",
        data={"email": email, "password": "Test123!"},
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}


def _make_client(app, owner_email, code, business_name):
    with app.app_context():
        owner = User.query.filter_by(email=owner_email).one().collaborator
        row = Client(
            code=code,
            business_name=business_name,
            contact_name="Contacto",
            owner_id=owner.id,
            record_type="cliente",
            pipeline_stage="venta_cerrada",
            client_status="activo",
        )
        db.session.add(row)
        db.session.commit()
        return row.id


def test_v2_models_do_not_store_plaintext_password_fields(app):
    with app.app_context():
        operational_columns = {column.name for column in ClientOperationalProfile.__table__.columns}
        platform_columns = {column.name for column in ClientPlatform.__table__.columns}
        assert "password" not in operational_columns
        assert "password" not in platform_columns
        assert "secret" not in operational_columns
        assert "secret" not in platform_columns


def test_advisor_can_update_operational_profile_for_own_client(client, app):
    client_id = _make_client(app, "advisor-v2@test.local", "V2-OWN", "Cliente V2 Propio")
    _login(client, "advisor-v2@test.local")

    response = client.post(
        f"/clients/{client_id}/operational-profile",
        data={
            "attention_days": "Lunes a sábado",
            "business_hours": "8am - 5pm",
            "experience_text": "10 años",
            "coverage_text": "50 millas",
            "payment_methods": "Efectivo, Zelle",
            "estimate_policy": "Estimados gratis",
            "languages": "Español e inglés",
            "operational_email": "OPERATIONS@EXAMPLE.COM",
            "services_to_promote": "Landscaping\nTree Removal",
            "brand_colors": "Verde, negro",
        },
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}

    with app.app_context():
        profile = ClientOperationalProfile.query.filter_by(client_id=client_id).one()
        assert profile.attention_days == "Lunes a sábado"
        assert profile.operational_email == "operations@example.com"
        assert profile.coverage_text == "50 millas"
        assert profile.languages == "Español e inglés"


def test_production_cannot_edit_operational_profile(client, app):
    client_id = _make_client(app, "advisor-v2@test.local", "V2-PROD", "Cliente Solo Lectura")
    _login(client, "production-v2@test.local")
    response = client.post(
        f"/clients/{client_id}/operational-profile",
        data={"attention_days": "No autorizado"},
        follow_redirects=False,
    )
    assert response.status_code == 403

    with app.app_context():
        assert ClientOperationalProfile.query.filter_by(client_id=client_id).first() is None


def test_advisor_cannot_edit_other_advisors_v2_profile(client, app):
    client_id = _make_client(app, "other-v2@test.local", "V2-OTHER", "Cliente V2 Ajeno")
    _login(client, "advisor-v2@test.local")
    response = client.post(
        f"/clients/{client_id}/operational-profile",
        data={"attention_days": "No autorizado"},
        follow_redirects=False,
    )
    assert response.status_code == 403


def test_platform_upsert_keeps_one_row_per_platform(client, app):
    client_id = _make_client(app, "advisor-v2@test.local", "V2-PLAT", "Cliente Plataformas")
    _login(client, "advisor-v2@test.local")

    first = client.post(
        f"/clients/{client_id}/platforms",
        data={"platform_key": "youtube", "url": "youtube.com/example", "status": "activo"},
        follow_redirects=False,
    )
    second = client.post(
        f"/clients/{client_id}/platforms",
        data={"platform_key": "youtube", "url": "https://youtube.com/updated", "status": "activo"},
        follow_redirects=False,
    )
    assert first.status_code in {302, 303}
    assert second.status_code in {302, 303}

    with app.app_context():
        rows = ClientPlatform.query.filter_by(client_id=client_id, platform_key="youtube").all()
        assert len(rows) == 1
        assert rows[0].url == "https://youtube.com/updated"


def test_contract_creation_generates_v2_snapshot(client, app):
    client_id = _make_client(app, "advisor-v2@test.local", "V2-CONTRACT", "Cliente Contrato V2")
    with app.app_context():
        product = ProductService.query.filter_by(name="Golden").one()
        product_id = product.id

    _login(client, "advisor-v2@test.local")
    response = client.post(
        f"/clients/{client_id}/contracts",
        data={
            "product_id": product_id,
            "status": "activo",
            "starts_on": date.today().isoformat(),
            "agreed_price": "2000",
            "benefits_snapshot": "Website\nGoogle Business Profile",
            "courtesies_snapshot": "Business Cards",
            "principal": "on",
        },
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}

    with app.app_context():
        row = (
            ClientContractDetail.query
            .join(ClientContract, ClientContractDetail.contract_id == ClientContract.id)
            .filter(ClientContract.client_id == client_id)
            .one()
        )
        assert row.modality_snapshot == "dueno"
        assert row.maintenance_snapshot == "no_incluido"
        assert "Website" in row.benefits_snapshot
        assert row.courtesies_snapshot == "Business Cards"
        assert row.contract.principal is True


def test_production_client_v2_hides_unassigned_projects_and_tasks(client, app):
    client_id = _make_client(app, "advisor-v2@test.local", "V2-SCOPE", "Cliente Operaciones V2")
    with app.app_context():
        other = User.query.filter_by(email="other-v2@test.local").one().collaborator
        project = Project(
            project_no="V2-PRJ-HIDDEN",
            client_id=client_id,
            name="Proyecto Confidencial V2",
            coordinator_id=other.id,
            status="en_produccion",
            progress=25,
            starts_on=date.today(),
        )
        db.session.add(project)
        db.session.flush()
        db.session.add(
            Task(
                title="Tarea Confidencial V2",
                client_id=client_id,
                project_id=project.id,
                assignee_id=other.id,
                status="pendiente",
                priority="media",
            )
        )
        db.session.commit()

    _login(client, "production-v2@test.local")
    response = client.get(f"/clients/{client_id}")
    assert response.status_code == 200
    assert b"Proyecto Confidencial V2" not in response.data
    assert b"Tarea Confidencial V2" not in response.data
