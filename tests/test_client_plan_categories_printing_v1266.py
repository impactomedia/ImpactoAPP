"""Filtros de clientes por plan y cotización personalizada de imprenta."""
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app import create_app
from app.extensions import db
from app.models import (
    Client, ClientContract, PrintOrder, ProductService, Role, User,
)
from scripts.seed import seed_all


class TestConfig:
    TESTING = True
    SECRET_KEY = "tests-planes-imprenta"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    UPLOAD_FOLDER = str(Path(__file__).parent / "uploads_plan_print_test")
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
    server = create_app(TestConfig)
    with server.app_context():
        db.create_all()
        seed_all()
        admin = User(name="Administrador", email="plans-test@test.local",
                     role=Role.query.filter_by(name="admin").one(), active=True)
        admin.set_password("SafeTestPassword2026")
        db.session.add(admin)
        db.session.flush()
        owner = Client(
            code="PL-CLIENT-1", business_name="Cliente Esencial",
            contact_name="Ejemplo Uno", record_type="cliente",
            client_status="activo", pipeline_stage="venta_cerrada",
        )
        custom = Client(
            code="PL-CLIENT-2", business_name="Cliente Imprenta",
            contact_name="Ejemplo Dos", record_type="cliente",
            client_status="activo", pipeline_stage="venta_cerrada",
        )
        plain = Client(
            code="PL-CLIENT-3", business_name="Cliente Sin Plan",
            contact_name="Ejemplo Tres", record_type="cliente",
            client_status="activo", pipeline_stage="venta_cerrada",
        )
        db.session.add_all([owner, custom, plain])
        db.session.flush()
        package = ProductService.query.filter_by(name="Impacto Esencial - 6 Meses").one()
        db.session.add(ClientContract(
            client_id=owner.id, product_id=package.id,
            status="activo", starts_on=date.today(), principal=True,
            agreed_price=Decimal("750.00"),
        ))
        db.session.add(ProductService(
            name="Paquete Imprenta 1000 tarjetas",
            category="imprenta", base_price=Decimal("120.00"),
            active=True, is_physical=True,
        ))
        db.session.commit()
    yield server


@pytest.fixture()
def browser(app):
    c = app.test_client()
    response = c.post("/auth/login", data={
        "email": "plans-test@test.local", "password": "SafeTestPassword2026",
    })
    assert response.status_code in (302, 303)
    return c


def client_id(app, code):
    with app.app_context():
        return Client.query.filter_by(code=code).one().id


def order_data(client, **extra):
    data = {
        "client_id": str(client),
        "item_name[]": ["Tarjetas doble cara", "Flyers personalizados"],
        "quantity[]": ["2", "10"],
        "unit_price[]": ["125.50", "3.25"],
        "catalog_product_id[]": ["", ""],
        "specification[]": ["1000 unidades 2 caras", "Tamaño 4x6"],
        "total_cost": "90.00",
        "total_sale": "1.00",
    }
    data.update(extra)
    return data


def test_client_index_lists_plan_names_and_filters_by_type(browser, app):
    html = browser.get("/clients/").get_data(as_text=True)
    assert "Clientes por plan contratado" in html
    assert "Impacto Esencial - 6 Meses" in html
    assert "Imprenta personalizada" in html
    assert "Sin plan activo registrado" in html
    page = browser.get("/clients/?plan_type=paquete").get_data(as_text=True)
    assert "Cliente Esencial" in page
    assert "Cliente Imprenta" not in page
    assert "Cliente Sin Plan" not in page


def test_filter_specific_plan(browser, app):
    with app.app_context():
        product_id = ProductService.query.filter_by(name="Impacto Esencial - 6 Meses").one().id
    body = browser.get(f"/clients/?commercial_plan_id={product_id}").get_data(as_text=True)
    assert "Cliente Esencial" in body
    assert "Cliente Imprenta" not in body


def test_custom_print_order_price_is_calculated_on_server(browser, app):
    cid = client_id(app, "PL-CLIENT-2")
    result = browser.post("/printing/new", data=order_data(cid))
    assert result.status_code == 302
    with app.app_context():
        order = PrintOrder.query.filter_by(client_id=cid).one()
        assert order.total_sale == Decimal("283.50")
        assert order.total_cost == Decimal("90.00")
        assert len(order.items) == 2
        assert "Precio unitario acordado: USD 125.50" in order.items[0].specification
        assert "Subtotal: USD 251.00" in order.items[0].specification
        order_id = order.id

    detail = browser.get(f"/printing/{order_id}").get_data(as_text=True)
    assert "USD 283.50" in detail
    assert "Precio unitario acordado" in detail
    filtered = browser.get("/clients/?plan_type=imprenta").get_data(as_text=True)
    assert "Cliente Imprenta" in filtered
    assert "Cliente Esencial" not in filtered
    no_plan = browser.get("/clients/?plan_type=sin_plan").get_data(as_text=True)
    assert "Cliente Sin Plan" in no_plan
    assert "Cliente Imprenta" not in no_plan


def test_catalog_product_is_selectable_and_price_can_be_overridden(browser, app):
    cid = client_id(app, "PL-CLIENT-2")
    with app.app_context():
        product = ProductService.query.filter_by(name="Paquete Imprenta 1000 tarjetas").one()
        product_id = product.id
    form_html = browser.get("/printing/new").get_data(as_text=True)
    assert "Paquete Imprenta 1000 tarjetas" in form_html
    assert "data-price" in form_html
    response = browser.post("/printing/new", data={
        "client_id": str(cid),
        "catalog_product_id[]": str(product_id),
        "item_name[]": "1000 tarjetas especiales",
        "quantity[]": "2",
        "unit_price[]": "130.00",
        "specification[]": "Premium",
        "total_cost": "100",
    })
    assert response.status_code == 302
    with app.app_context():
        order = PrintOrder.query.filter_by(client_id=cid).one()
        assert order.total_sale == Decimal("260.00")


def test_invalid_price_does_not_create_print_order(browser, app):
    cid = client_id(app, "PL-CLIENT-2")
    response = browser.post("/printing/new", data=order_data(cid, **{
        "unit_price[]": ["-8.00", "3.25"],
    }))
    assert response.status_code == 200
    with app.app_context():
        assert PrintOrder.query.filter_by(client_id=cid).count() == 0


def test_printing_order_has_separate_cost_and_client_price(browser, app):
    body = browser.get("/printing/new").get_data(as_text=True)
    assert "Costo interno de producción" in body
    assert "Precio unitario USD" in body
    assert "Precio total al cliente" in body
    assert "Paquetes y productos personalizados" in body
