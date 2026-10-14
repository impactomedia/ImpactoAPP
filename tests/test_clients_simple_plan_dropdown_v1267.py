"""Validación del filtro único y legible de tipos de planes de clientes."""
from datetime import date
from pathlib import Path

import pytest
from app import create_app
from app.extensions import db
from app.models import Client, ClientContract, PrintOrder, ProductService, Role, User
from scripts.seed import seed_all


class Config:
    TESTING = True
    SECRET_KEY = "test-filtro-unico-1267"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    UPLOAD_FOLDER = str(Path(__file__).parent / "uploads_simple_plan")
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
    app = create_app(Config)
    with app.app_context():
        db.create_all()
        seed_all()
        admin = User(
            name="Administrador Planes", email="filter-plan-1267@test.local",
            role=Role.query.filter_by(name="admin").one(), active=True,
        )
        admin.set_password("ExamplePassword2026!")
        db.session.add(admin)
        products = [
            ("Impacto Esencial - 6 Meses", "paquete", "Esencial"),
            ("Impacto Golden", "paquete", "Golden"),
            ("Impacto VIP", "paquete", "VIP"),
            ("Impacto Premium Especial", "paquete", "Premium"),
            ("Community Manager Silver - Solo Redes Sociales", "community_manager", "Community"),
            ("Website - Creación", "servicio", "Digital"),
            ("Otro Paquete Empresarial 2026", "paquete", "Otros"),
        ]
        for pos, (product_name, category, label) in enumerate(products, 1):
            product = ProductService.query.filter_by(name=product_name).first()
            if not product:
                product = ProductService(name=product_name, category=category, active=True)
                db.session.add(product)
            client = Client(
                code=f"FILTER-1267-{pos}", business_name=f"Cuenta {label}",
                contact_name="Persona ejemplo", record_type="cliente",
                client_status="activo", pipeline_stage="venta_cerrada",
            )
            db.session.add(client)
            db.session.flush()
            db.session.add(ClientContract(
                client_id=client.id, product_id=product.id,
                status="activo", starts_on=date.today(),
                agreed_price=100,
            ))
        print_client = Client(
            code="FILTER-1267-8", business_name="Cuenta Imprenta",
            contact_name="Persona ejemplo", record_type="cliente",
            client_status="activo", pipeline_stage="venta_cerrada",
        )
        db.session.add(print_client)
        db.session.flush()
        db.session.add(PrintOrder(
            order_no="IMP-FILTER-1267", client_id=print_client.id,
            status="diseno", total_sale=100,
        ))
        db.session.commit()
    yield app


@pytest.fixture()
def browser(app):
    client = app.test_client()
    response = client.post("/auth/login", data={
        "email": "filter-plan-1267@test.local",
        "password": "ExamplePassword2026!",
    })
    assert response.status_code in (302, 303)
    return client


def test_single_dropdown_and_no_extra_classification(browser):
    body = browser.get("/clients/").get_data(as_text=True)
    assert body.count('name="plan_type"') == 1
    assert 'name="commercial_plan_id"' not in body
    assert "Clasificación por tipo de plan" not in body
    assert "Plan / producto específico" not in body
    for name in (
        "Esencial", "Golden", "VIP", "Premium",
        "Community Manager", "Imprenta", "Servicio digital", "Otros planes",
    ):
        assert name in body


@pytest.mark.parametrize("plan,expected", [
    ("esencial", "Cuenta Esencial"),
    ("golden", "Cuenta Golden"),
    ("vip", "Cuenta VIP"),
    ("premium", "Cuenta Premium"),
    ("community_manager", "Cuenta Community"),
    ("servicio_digital", "Cuenta Digital"),
    ("imprenta", "Cuenta Imprenta"),
    ("otros_planes", "Cuenta Otros"),
])
def test_each_plan_filters_the_matching_client(browser, plan, expected):
    response = browser.get(f"/clients/?plan_type={plan}")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert expected in body
    assert "Cuenta Imprenta" not in body if expected != "Cuenta Imprenta" else "Cuenta Digital" not in body


def test_existing_client_search_and_pagination_still_work(browser):
    response = browser.get("/clients/?q=Cuenta&plan_type=esencial&page=1")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Cuenta Esencial" in body
    assert "Cuenta Golden" not in body


def test_unknown_filter_does_not_hide_customers(browser):
    page = browser.get("/clients/?plan_type=invalid").get_data(as_text=True)
    assert "Cuenta Esencial" in page
    assert "Cuenta Imprenta" in page
