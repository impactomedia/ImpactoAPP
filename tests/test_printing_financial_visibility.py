"""Los costos internos de imprenta no son información comercial ni operativa general."""
from decimal import Decimal
from pathlib import Path

import pytest

from app import create_app
from app.extensions import db
from app.models import Client, PrintOrder, Role, User
from scripts.seed import seed_all


class TestConfig:
    TESTING = True
    SECRET_KEY = "printing-financial-visibility-test"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    UPLOAD_FOLDER = str(Path(__file__).parent / "uploads_print_visibility")
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
def prepared():
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        seed_all()
        production_role = Role.query.filter_by(name="production").one()
        user = User(
            name="Producción Imprenta",
            email="production-printing@test.local",
            active=True,
            role=production_role,
        )
        user.set_password("Testing123!")
        client = Client(
            code="PRINT-TEST",
            business_name="Cliente Test Imprenta",
            contact_name="Contacto Test",
            record_type="cliente",
        )
        db.session.add_all([user, client])
        db.session.flush()
        order = PrintOrder(
            order_no="PRINT-CASE-01",
            client_id=client.id,
            total_cost=Decimal("7654.32"),
            total_sale=Decimal("4321.09"),
            status="diseno",
        )
        db.session.add(order)
        db.session.commit()
        yield app, order.id
        db.session.remove()


def _login(browser, email, password):
    response = browser.post("/auth/login", data={
        "email": email, "password": password,
    }, follow_redirects=False)
    assert response.status_code in (302, 303)


def test_printing_order_cost_only_visible_to_financial_roles(prepared):
    app, order_id = prepared
    browser = app.test_client()
    _login(browser, "production-printing@test.local", "Testing123!")
    for path in ("/printing/", f"/printing/{order_id}"):
        response = browser.get(path)
        assert response.status_code == 200, path
        html = response.get_data(as_text=True)
        assert "4,321.09" in html
        assert "7,654.32" not in html
        assert "Costo de producción" not in html

    browser.post("/auth/logout")
    _login(browser, "admin@impactomedia.local", "ChangeMe123!")
    for path in ("/printing/", f"/printing/{order_id}"):
        response = browser.get(path)
        assert response.status_code == 200, path
        html = response.get_data(as_text=True)
        assert "4,321.09" in html
        assert "7,654.32" in html
        assert "Costo de producción" in html
