from datetime import date, datetime, timedelta
from pathlib import Path
import hashlib
import re

import pytest

from app import create_app
from app.extensions import db
from app.models import (
    CatalogItem,
    Client,
    Collaborator,
    PasswordReset,
    Payment,
    Role,
    Sale,
    SupportTicket,
    User,
)
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
        _create_user("advisor", "advisor-a@test.local", "Asesor A")
        _create_user("advisor", "advisor-b@test.local", "Asesor B")
        _create_user("supervisor", "supervisor@test.local", "Supervisor")
        _create_user("production", "production@test.local", "Producción")
        _create_user("admin", "admin-test@test.local", "Admin Test")
        db.session.commit()

        supervisor = User.query.filter_by(email="supervisor@test.local").one().collaborator
        advisor_a = User.query.filter_by(email="advisor-a@test.local").one().collaborator
        advisor_a.supervisor_id = supervisor.id
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
        code=f"AUD-{user.id:03d}",
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
    return response


def test_login_rejects_external_next_redirect(client):
    response = client.post(
        "/auth/login?next=https://evil.example/phishing",
        data={"email": "advisor-a@test.local", "password": "Test123!"},
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}
    assert response.headers["Location"].endswith("/dashboard/")


def test_profile_password_change_requires_current_password(client, app):
    _login(client, "advisor-a@test.local")
    response = client.post(
        "/auth/profile",
        data={
            "name": "Asesor A",
            "current_password": "incorrecta",
            "new_password": "Nueva123!",
            "confirm_password": "Nueva123!",
        },
        follow_redirects=False,
    )
    assert response.status_code == 200
    with app.app_context():
        user = User.query.filter_by(email="advisor-a@test.local").one()
        assert user.check_password("Test123!")
        assert not user.check_password("Nueva123!")


def test_password_reset_invalidates_other_open_tokens(client, app):
    raw_one = "token-one"
    raw_two = "token-two"
    with app.app_context():
        user = User.query.filter_by(email="advisor-a@test.local").one()
        expires = datetime.utcnow() + timedelta(hours=1)
        db.session.add_all([
            PasswordReset(user_id=user.id, token_hash=hashlib.sha256(raw_one.encode()).hexdigest(), expires_at=expires),
            PasswordReset(user_id=user.id, token_hash=hashlib.sha256(raw_two.encode()).hexdigest(), expires_at=expires),
        ])
        db.session.commit()

    response = client.post(f"/auth/reset/{raw_one}", data={"password": "Reset123!"}, follow_redirects=False)
    assert response.status_code in {302, 303}

    with app.app_context():
        user = User.query.filter_by(email="advisor-a@test.local").one()
        assert PasswordReset.query.filter_by(user_id=user.id, used_at=None).count() == 0


def test_advisor_cannot_assign_new_prospect_to_other_advisor(client, app):
    with app.app_context():
        other = User.query.filter_by(email="advisor-b@test.local").one().collaborator
        other_id = other.id

    _login(client, "advisor-a@test.local")
    response = client.post(
        "/crm/new",
        data={
            "business_name": "Prospecto Seguro",
            "contact_name": "Contacto",
            "owner_id": str(other_id),
            "priority": "alta",
        },
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}

    with app.app_context():
        advisor = User.query.filter_by(email="advisor-a@test.local").one().collaborator
        prospect = Client.query.filter_by(business_name="Prospecto Seguro").one()
        assert prospect.owner_id == advisor.id


def test_draft_quote_does_not_move_pipeline_to_sent(client, app):
    with app.app_context():
        advisor = User.query.filter_by(email="advisor-a@test.local").one().collaborator
        prospect = Client(
            code="AUD-Q-1",
            business_name="Cotización Borrador",
            contact_name="Contacto",
            owner_id=advisor.id,
            record_type="seguimiento",
            pipeline_stage="interesado",
        )
        db.session.add(prospect)
        db.session.commit()
        prospect_id = prospect.id

    _login(client, "advisor-a@test.local")
    response = client.post(
        f"/crm/{prospect_id}/quotes/new",
        data={
            "currency": "USD",
            "discount": "0",
            "description[]": ["Servicio"],
            "quantity[]": ["1"],
            "unit_price[]": ["100"],
            "product_id[]": [""],
        },
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}

    with app.app_context():
        prospect = db.session.get(Client, prospect_id)
        assert prospect.pipeline_stage == "interesado"
        assert prospect.quotes[0].status == "borrador"


def test_support_rejects_inactive_responsible(client, app):
    with app.app_context():
        advisor = User.query.filter_by(email="advisor-a@test.local").one().collaborator
        customer = Client(
            code="AUD-S-1",
            business_name="Cliente Soporte",
            contact_name="Contacto",
            owner_id=advisor.id,
            record_type="cliente",
            client_status="activo",
        )
        inactive_user, inactive = _create_user("production", "inactive@test.local", "Inactivo")
        inactive.status = "inactivo"
        db.session.add(customer)
        db.session.commit()
        customer_id = customer.id
        inactive_id = inactive.id

    _login(client, "advisor-a@test.local")
    response = client.post(
        "/support/",
        data={
            "client_id": customer_id,
            "subject": "Prueba",
            "priority": "normal",
            "ticket_type": "soporte",
            "responsible_id": inactive_id,
        },
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}
    with app.app_context():
        assert SupportTicket.query.filter_by(subject="Prueba").count() == 0


def test_report_export_respects_selected_dates(client, app):
    with app.app_context():
        advisor = User.query.filter_by(email="advisor-a@test.local").one().collaborator
        customer = Client(
            code="AUD-R-1",
            business_name="Cliente Reporte",
            contact_name="Contacto",
            owner_id=advisor.id,
            record_type="cliente",
        )
        db.session.add(customer)
        db.session.flush()
        inside = Sale(sale_no="VEN-IN-RANGE", client_id=customer.id, advisor_id=advisor.id, sale_date=date.today(), total=100, amount_paid=0, balance=100)
        outside = Sale(sale_no="VEN-OUT-RANGE", client_id=customer.id, advisor_id=advisor.id, sale_date=date.today() - timedelta(days=90), total=100, amount_paid=0, balance=100)
        db.session.add_all([inside, outside])
        db.session.commit()

    _login(client, "admin-test@test.local")
    start = (date.today() - timedelta(days=2)).isoformat()
    end = date.today().isoformat()
    response = client.get(f"/reports/export.csv?report=sales&starts={start}&ends={end}")
    assert response.status_code == 200
    assert b"VEN-IN-RANGE" in response.data
    assert b"VEN-OUT-RANGE" not in response.data


def test_duplicate_catalog_is_handled_without_server_error(client, app):
    with app.app_context():
        if not CatalogItem.query.filter_by(category="audit_test", code="uno").first():
            db.session.add(CatalogItem(category="audit_test", code="uno", label="Uno", active=True, sort_order=0))
            db.session.commit()

    _login(client, "admin-test@test.local")
    response = client.post(
        "/settings/catalogs",
        data={"category": "audit_test", "code": "uno", "label": "Duplicado"},
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}
    with app.app_context():
        assert CatalogItem.query.filter_by(category="audit_test", code="uno").count() == 1


def test_anonymous_static_upload_url_is_not_public(client):
    response = client.get("/static/uploads/example.pdf", follow_redirects=False)
    assert response.status_code == 404


def test_all_post_forms_include_csrf_token():
    templates = Path(__file__).resolve().parents[1] / "app" / "templates"
    post_form = re.compile(r'<form\b[^>]*method=["\']post["\'][^>]*>(.*?)</form>', re.I | re.S)
    missing = []
    for path in templates.rglob("*.html"):
        content = path.read_text(encoding="utf-8")
        for index, match in enumerate(post_form.finditer(content), start=1):
            if "csrf_token" not in match.group(1):
                missing.append(f"{path.relative_to(templates)} formulario #{index}")
    assert not missing, missing
