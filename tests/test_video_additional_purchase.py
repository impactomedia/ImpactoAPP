"""Video is a selectable additional purchase with an agreed price and one production task."""
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app import create_app
from app.extensions import db
from app.models import (
    Client, ClientContract, PrintOrder, ProductService, Project,
    Sale, SaleItem, Task,
)
from scripts.seed import seed_all


class Config:
    TESTING = True
    SECRET_KEY = "test-video-additional-purchase"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    UPLOAD_FOLDER = str(Path(__file__).parent / "uploads_video_purchase")
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
    app = create_app(Config)
    with app.app_context():
        db.create_all()
        seed_all()
        person = Client(
            code="VIDEO-CLIENT-001",
            business_name="Cliente Video",
            contact_name="Contacto",
            record_type="cliente",
            client_status="activo",
        )
        db.session.add(person)
        db.session.commit()
        yield app, person.id
        db.session.remove()


def _login(browser):
    response = browser.post(
        "/auth/login",
        data={"email": "admin@impactomedia.local", "password": "ChangeMe123!"},
    )
    assert response.status_code in (302, 303)


def test_video_is_idempotent_nonprincipal_catalog_product(prepared):
    app, _ = prepared
    with app.app_context():
        product = ProductService.query.filter_by(name="Video").one()
        assert product.active is True
        assert product.category == "servicio"
        assert product.is_physical is False
        assert product.renewal_required is False
        assert product.base_price is None
        product_id = product.id
        seed_all()
        db.session.flush()
        assert ProductService.query.filter_by(name="Video").count() == 1
        assert ProductService.query.filter_by(id=product_id).one().name == "Video"


def test_video_appears_in_additional_purchase_not_principal_list(prepared):
    app, client_id = prepared
    browser = app.test_client()
    _login(browser)
    with app.app_context():
        product_id = ProductService.query.filter_by(name="Video").one().id
    response = browser.get(f"/sales/new?client_id={client_id}")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert f'<option value="{product_id}" data-name="Video" data-price="">' in html
    assert "Nueva compra adicional" in html

    principal = browser.get(f"/sales/principal/{client_id}")
    assert principal.status_code == 200
    assert 'data-name="Video"' not in principal.get_data(as_text=True)


def test_video_purchase_keeps_client_plan_and_generates_one_task(prepared):
    app, client_id = prepared
    with app.app_context():
        video = ProductService.query.filter_by(name="Video").one()
        product_id = video.id
        plan = ProductService.query.filter_by(name="Impacto Esencial - 6 Meses").one()
        contract = ClientContract(
            client_id=client_id,
            product_id=plan.id,
            starts_on=date(2026, 9, 1),
            status="activo",
            principal=True,
            agreed_price=Decimal("750.00"),
        )
        db.session.add(contract)
        db.session.commit()
        contract_id = contract.id

    browser = app.test_client()
    _login(browser)
    response = browser.post("/sales/new", data={
        "client_id": str(client_id),
        "sale_date": "2026-11-03",
        "payment_mode": "contado",
        "payment_method": "Zelle",
        "initial_payment": "325.00",
        "initial_payment_date": "2026-11-03",
        "product_id[]": str(product_id),
        "description[]": "Video comercial con avatar",
        "quantity[]": "1",
        "unit_price[]": "325.00",
        "discount[]": "0",
    })
    assert response.status_code == 302

    with app.app_context():
        sale = Sale.query.filter_by(client_id=client_id).one()
        assert sale.sale_date == date(2026, 11, 3)
        assert sale.total == Decimal("325.00")
        assert sale.balance == Decimal("0.00")
        item = SaleItem.query.filter_by(sale_id=sale.id).one()
        assert item.product_id == product_id
        assert item.description == "Video comercial con avatar"
        assert item.unit_price == Decimal("325.00")

        project = Project.query.filter_by(sale_id=sale.id).one()
        assert project.client_id == client_id
        assert project.product_id == product_id
        assert project.department == "Producción"
        task = Task.query.filter_by(project_id=project.id).one()
        assert task.client_id == client_id
        assert task.task_type == "video"
        assert task.title == "Producción de video: Video comercial con avatar"
        assert task.status == "pendiente"
        assert task.due_at is None
        assert task.assignee_id is None
        assert PrintOrder.query.filter_by(sale_id=sale.id).count() == 0

        assert ClientContract.query.filter_by(client_id=client_id).count() == 1
        existing_contract = db.session.get(ClientContract, contract_id)
        assert existing_contract.principal is True
        assert existing_contract.status == "activo"
