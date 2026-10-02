from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app import create_app
from app.extensions import db
from app.models import (
    AccountReceivable,
    Client,
    ClientContract,
    Collaborator,
    ProductService,
    Role,
    Sale,
    User,
)
from scripts.seed import seed_all


class TestConfig:
    TESTING = True
    SECRET_KEY = "nexora-principal-test"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    WTF_CSRF_TIME_LIMIT = None
    UPLOAD_FOLDER = str(Path(__file__).parent / "uploads_nexora_principal")
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

        role = Role.query.filter_by(name="admin").one()
        user = User(
            name="Admin Nexora",
            email="admin-nexora@test.local",
            role=role,
            active=True,
        )
        user.set_password("Test123!")
        db.session.add(user)
        db.session.flush()

        advisor_role = Role.query.filter_by(name="advisor").one()
        advisor_user = User(
            name="Asesor Nexora",
            email="advisor-nexora@test.local",
            role=advisor_role,
            active=True,
        )
        advisor_user.set_password("Test123!")
        db.session.add(advisor_user)
        db.session.flush()

        advisor = Collaborator(
            user_id=advisor_user.id,
            code="NX-ADV",
            job_title="Asesor",
            department="Ventas",
            status="activo",
            join_date=date.today(),
        )
        db.session.add(advisor)
        db.session.flush()

        client = Client(
            code="NX-SEG",
            business_name="Cliente Nexora",
            contact_name="Contacto",
            owner_id=advisor.id,
            record_type="seguimiento",
            pipeline_stage="interesado",
            client_status="activo",
            country="USA",
        )
        db.session.add(client)

        six = ProductService.query.filter_by(name="6 Meses").one()
        golden = ProductService.query.filter_by(name="Golden").one()
        premium = ProductService.query.filter_by(name="Premium").one()
        six.base_price = Decimal("750")
        golden.base_price = Decimal("2000")
        premium.base_price = Decimal("3500")
        six.currency = golden.currency = premium.currency = "USD"

        db.session.commit()

    yield app


@pytest.fixture()
def client(app):
    return app.test_client()


def login(client):
    response = client.post(
        "/auth/login",
        data={"email": "admin-nexora@test.local", "password": "Test123!"},
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}


def principal_ids(app):
    with app.app_context():
        customer = Client.query.filter_by(code="NX-SEG").one()
        six = ProductService.query.filter_by(name="6 Meses").one()
        golden = ProductService.query.filter_by(name="Golden").one()
        premium = ProductService.query.filter_by(name="Premium").one()
        return customer.id, six.id, golden.id, premium.id


def test_first_principal_service_uses_fixed_catalog_price(client, app):
    client_id, six_id, _, _ = principal_ids(app)
    login(client)

    response = client.post(
        f"/sales/principal/{client_id}",
        data={
            "product_id": six_id,
            "sale_date": date.today().isoformat(),
            "initial_payment": "750",
            "initial_payment_date": date.today().isoformat(),
            "payment_method": "Zelle",
            "payment_plan_mode": "single",
            "courtesies": "500 Business Cards",
        },
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}

    with app.app_context():
        customer = db.session.get(Client, client_id)
        sale = Sale.query.one()
        contract = ClientContract.query.filter_by(client_id=client_id, principal=True).one()

        assert customer.record_type == "cliente"
        assert customer.pipeline_stage == "venta_cerrada"
        assert customer.country == "USA"
        assert sale.currency == "USD"
        assert Decimal(str(sale.total)) == Decimal("750.00")
        assert Decimal(str(sale.amount_paid)) == Decimal("750.00")
        assert Decimal(str(sale.balance)) == Decimal("0.00")
        assert Decimal(str(contract.agreed_price)) == Decimal("750.00")
        assert contract.product.name == "6 Meses"
        assert contract.v2_detail.courtesies_snapshot == "500 Business Cards"


def test_upgrade_replaces_old_principal_and_recognizes_paid_amount(client, app):
    client_id, six_id, golden_id, _ = principal_ids(app)
    login(client)

    due = (date.today() + timedelta(days=30)).isoformat()
    response = client.post(
        f"/sales/principal/{client_id}",
        data={
            "product_id": six_id,
            "sale_date": date.today().isoformat(),
            "initial_payment": "500",
            "initial_payment_date": date.today().isoformat(),
            "payment_method": "Zelle",
            "payment_plan_mode": "single",
            "due_date": due,
        },
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}

    response = client.post(
        f"/sales/principal/{client_id}",
        data={
            "product_id": golden_id,
            "sale_date": date.today().isoformat(),
            "initial_payment": "0",
            "payment_method": "transferencia",
            "payment_plan_mode": "single",
            "due_date": due,
        },
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}

    with app.app_context():
        sales = Sale.query.order_by(Sale.id).all()
        assert len(sales) == 2
        old_sale, new_sale = sales

        contracts = ClientContract.query.filter_by(client_id=client_id).order_by(ClientContract.id).all()
        assert len(contracts) == 2
        old_contract, new_contract = contracts

        assert old_contract.status == "inactivo"
        assert old_contract.principal is False
        assert old_sale.status == "reemplazada"
        assert Decimal(str(old_sale.balance)) == Decimal("0.00")

        old_receivable = AccountReceivable.query.filter_by(sale_id=old_sale.id).one()
        assert old_receivable.status == "cancelado"
        assert Decimal(str(old_receivable.total_amount)) == Decimal("500.00")
        assert Decimal(str(old_receivable.paid_amount)) == Decimal("500.00")

        assert new_contract.status == "activo"
        assert new_contract.principal is True
        assert new_contract.product.name == "Golden"
        assert Decimal(str(new_contract.agreed_price)) == Decimal("2000.00")

        item = new_sale.items[0]
        assert Decimal(str(item.list_price)) == Decimal("2000.00")
        assert Decimal(str(item.discount)) == Decimal("500.00")
        assert Decimal(str(new_sale.total)) == Decimal("1500.00")
        assert Decimal(str(new_sale.balance)) == Decimal("1500.00")


def test_second_upgrade_carries_forward_previous_credit_plus_new_payments(client, app):
    client_id, six_id, golden_id, premium_id = principal_ids(app)
    login(client)

    due = (date.today() + timedelta(days=30)).isoformat()

    # 6 Meses: paga 500 de 750.
    client.post(
        f"/sales/principal/{client_id}",
        data={
            "product_id": six_id,
            "sale_date": date.today().isoformat(),
            "initial_payment": "500",
            "payment_method": "Zelle",
            "payment_plan_mode": "single",
            "due_date": due,
        },
    )

    # Golden: reconoce 500; paga 500 más.
    client.post(
        f"/sales/principal/{client_id}",
        data={
            "product_id": golden_id,
            "sale_date": date.today().isoformat(),
            "initial_payment": "500",
            "payment_method": "Zelle",
            "payment_plan_mode": "single",
            "due_date": due,
        },
    )

    # Premium debe reconocer 1,000 acumulados, no solo los 500 del Golden.
    response = client.post(
        f"/sales/principal/{client_id}",
        data={
            "product_id": premium_id,
            "sale_date": date.today().isoformat(),
            "initial_payment": "0",
            "payment_method": "transferencia",
            "payment_plan_mode": "single",
            "due_date": due,
        },
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}

    with app.app_context():
        sale = Sale.query.order_by(Sale.id.desc()).first()
        item = sale.items[0]
        assert Decimal(str(item.list_price)) == Decimal("3500.00")
        assert Decimal(str(item.discount)) == Decimal("1000.00")
        assert Decimal(str(sale.total)) == Decimal("2500.00")

        active = ClientContract.query.filter_by(
            client_id=client_id,
            principal=True,
            status="activo",
        ).one()
        assert active.product.name == "Premium"
        assert Decimal(str(active.agreed_price)) == Decimal("3500.00")
