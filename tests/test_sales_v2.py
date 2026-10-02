from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app import create_app
from app.extensions import db
from app.models import (
    AccountReceivable,
    Client,
    Collaborator,
    ProductService,
    Project,
    Role,
    Sale,
    User,
)
from app.client_v2_models import ClientInstallment
from scripts.seed import seed_all


class TestConfig:
    TESTING = True
    SECRET_KEY = "sales-v2-test"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    WTF_CSRF_TIME_LIMIT = None
    UPLOAD_FOLDER = str(Path(__file__).parent / "uploads_sales_v2")
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
        _create_user("admin", "admin-sales@test.local", "Administración Sales")
        _, advisor = _create_user("advisor", "advisor-sales@test.local", "Asesor Sales")
        _create_user("production", "production-sales@test.local", "Producción Sales")

        customer = Client(
            code="SALES-V2-CLI",
            business_name="Cliente Sales V2",
            contact_name="Contacto",
            owner_id=advisor.id,
            record_type="cliente",
            pipeline_stage="venta_cerrada",
            client_status="activo",
        )
        db.session.add(customer)

        followup = Client(
            code="SALES-V2-SEG",
            business_name="Seguimiento Sales V2",
            contact_name="Contacto Seguimiento",
            owner_id=advisor.id,
            record_type="seguimiento",
            pipeline_stage="interesado",
            client_status="activo",
        )
        db.session.add(followup)
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
        code=f"SV2-{user.id:03d}",
        job_title=name,
        department="Ventas",
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


def _base_sale_data(app):
    with app.app_context():
        customer = Client.query.filter_by(code="SALES-V2-CLI").one()
        advisor = User.query.filter_by(email="advisor-sales@test.local").one().collaborator
        product = ProductService.query.filter_by(name="Website").one()
        return customer.id, advisor.id, product.id


def test_new_sale_page_loads_for_admin(client, app):
    _login(client, "admin-sales@test.local")
    response = client.get("/sales/new")
    assert response.status_code == 200
    assert b"Nueva compra adicional" in response.data
    assert b"Forma de pago" in response.data
    assert b"Cliente Sales V2" in response.data
    assert b"Seguimiento Sales V2" in response.data
    assert b"NIO" not in response.data


def test_production_cannot_open_new_sale(client):
    _login(client, "production-sales@test.local")
    response = client.get("/sales/new", follow_redirects=False)
    assert response.status_code == 403


def test_create_sale_with_initial_payment_and_custom_installments(client, app):
    client_id, advisor_id, product_id = _base_sale_data(app)
    _login(client, "admin-sales@test.local")

    due1 = date.today() + timedelta(days=15)
    due2 = date.today() + timedelta(days=45)

    response = client.post(
        "/sales/new",
        data={
            "client_id": client_id,
            "advisor_id": advisor_id,
            "sale_date": date.today().isoformat(),
            "currency": "USD",
            "product_id[]": [str(product_id)],
            "description[]": ["Website profesional"],
            "quantity[]": ["1"],
            "unit_price[]": ["1000"],
            "discount[]": ["0"],
            "initial_payment": "200",
            "payment_method": "Zelle",
            "initial_payment_date": date.today().isoformat(),
            "initial_payment_reference": "ZELLE-001",
            "payment_plan_mode": "custom",
            "installment_amount[]": ["400", "400"],
            "installment_due_date[]": [due1.isoformat(), due2.isoformat()],
            "installment_notes[]": ["Segunda parte", "Pago final"],
            "notes": "Venta Sales V2",
        },
        follow_redirects=False,
    )

    assert response.status_code in {302, 303}

    with app.app_context():
        sale = Sale.query.one()
        assert Decimal(str(sale.total)) == Decimal("1000.00")
        assert Decimal(str(sale.amount_paid)) == Decimal("200.00")
        assert Decimal(str(sale.balance)) == Decimal("800.00")
        assert sale.notes == "Venta Sales V2"
        assert sale.payments[0].reference == "ZELLE-001"

        receivable = AccountReceivable.query.filter_by(sale_id=sale.id).one()
        assert receivable.due_date == due1

        installments = ClientInstallment.query.filter_by(sale_id=sale.id).order_by(ClientInstallment.sequence).all()
        assert len(installments) == 2
        assert [Decimal(str(row.amount)) for row in installments] == [Decimal("400.00"), Decimal("400.00")]
        assert all(Decimal(str(row.base_paid_amount)) == Decimal("200.00") for row in installments)

        assert Project.query.filter_by(sale_id=sale.id).count() == 1


def test_sale_discount_is_applied_per_unit(client, app):
    client_id, advisor_id, product_id = _base_sale_data(app)
    _login(client, "admin-sales@test.local")

    response = client.post(
        "/sales/new",
        data={
            "client_id": client_id,
            "advisor_id": advisor_id,
            "sale_date": date.today().isoformat(),
            "currency": "USD",
            "product_id[]": [str(product_id)],
            "description[]": ["Servicio con descuento"],
            "quantity[]": ["2"],
            "unit_price[]": ["500"],
            "discount[]": ["50"],
            "initial_payment": "900",
            "payment_method": "transferencia",
            "initial_payment_date": date.today().isoformat(),
            "payment_plan_mode": "single",
            "notes": "",
        },
        follow_redirects=False,
    )

    assert response.status_code in {302, 303}
    with app.app_context():
        sale = Sale.query.one()
        item = sale.items[0]
        assert Decimal(str(item.list_price)) == Decimal("500.00")
        assert Decimal(str(item.discount)) == Decimal("50.00")
        assert Decimal(str(item.unit_price)) == Decimal("450.00")
        assert Decimal(str(sale.total)) == Decimal("900.00")
        assert Decimal(str(sale.balance)) == Decimal("0.00")
        assert ClientInstallment.query.filter_by(sale_id=sale.id).count() == 0


def test_custom_installments_must_equal_remaining_balance(client, app):
    client_id, advisor_id, product_id = _base_sale_data(app)
    _login(client, "admin-sales@test.local")

    response = client.post(
        "/sales/new",
        data={
            "client_id": client_id,
            "advisor_id": advisor_id,
            "sale_date": date.today().isoformat(),
            "currency": "USD",
            "product_id[]": [str(product_id)],
            "description[]": ["Website"],
            "quantity[]": ["1"],
            "unit_price[]": ["1000"],
            "discount[]": ["0"],
            "initial_payment": "200",
            "payment_method": "transferencia",
            "payment_plan_mode": "custom",
            "installment_amount[]": ["300", "300"],
            "installment_due_date[]": [
                (date.today() + timedelta(days=15)).isoformat(),
                (date.today() + timedelta(days=30)).isoformat(),
            ],
            "installment_notes[]": ["", ""],
        },
        follow_redirects=False,
    )

    assert response.status_code == 200
    with app.app_context():
        assert Sale.query.count() == 0


def test_inactive_product_cannot_be_submitted_manually(client, app):
    client_id, advisor_id, product_id = _base_sale_data(app)
    with app.app_context():
        product = db.session.get(ProductService, product_id)
        product.active = False
        db.session.commit()

    _login(client, "admin-sales@test.local")
    response = client.post(
        "/sales/new",
        data={
            "client_id": client_id,
            "advisor_id": advisor_id,
            "sale_date": date.today().isoformat(),
            "currency": "USD",
            "product_id[]": [str(product_id)],
            "description[]": ["Producto inactivo"],
            "quantity[]": ["1"],
            "unit_price[]": ["100"],
            "discount[]": ["0"],
            "initial_payment": "100",
            "payment_method": "efectivo",
            "payment_plan_mode": "single",
        },
        follow_redirects=False,
    )

    assert response.status_code == 200
    with app.app_context():
        assert Sale.query.count() == 0


def test_single_balance_requires_due_date(client, app):
    client_id, advisor_id, product_id = _base_sale_data(app)
    _login(client, "admin-sales@test.local")

    response = client.post(
        "/sales/new",
        data={
            "client_id": client_id,
            "advisor_id": advisor_id,
            "sale_date": date.today().isoformat(),
            "currency": "USD",
            "product_id[]": [str(product_id)],
            "description[]": ["Website"],
            "quantity[]": ["1"],
            "unit_price[]": ["1000"],
            "discount[]": ["0"],
            "initial_payment": "200",
            "payment_method": "transferencia",
            "payment_plan_mode": "single",
            "due_date": "",
        },
        follow_redirects=False,
    )

    assert response.status_code == 200
    with app.app_context():
        assert Sale.query.count() == 0


def test_sale_converts_followup_to_client_and_forces_usd(client, app):
    with app.app_context():
        followup = Client.query.filter_by(code="SALES-V2-SEG").one()
        advisor = User.query.filter_by(email="advisor-sales@test.local").one().collaborator
        product = ProductService.query.filter_by(name="Website").one()
        followup_id = followup.id
        advisor_id = advisor.id
        product_id = product.id

    _login(client, "admin-sales@test.local")
    response = client.post(
        "/sales/new",
        data={
            "client_id": followup_id,
            "advisor_id": advisor_id,
            "sale_date": date.today().isoformat(),
            "currency": "NIO",  # intento manipulado: Nexora debe forzar USD
            "product_id[]": [str(product_id)],
            "description[]": ["Website profesional"],
            "quantity[]": ["1"],
            "unit_price[]": ["750"],
            "discount[]": ["0"],
            "initial_payment": "750",
            "payment_method": "Zelle",
            "initial_payment_date": date.today().isoformat(),
            "payment_plan_mode": "single",
        },
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}

    with app.app_context():
        followup = db.session.get(Client, followup_id)
        sale = Sale.query.filter_by(client_id=followup_id).one()
        assert followup.record_type == "cliente"
        assert followup.pipeline_stage == "venta_cerrada"
        assert followup.client_status == "activo"
        assert followup.country == "USA"
        assert sale.currency == "USD"


def test_direct_client_creation_redirects_to_followup(client):
    _login(client, "admin-sales@test.local")
    response = client.get("/clients/new", follow_redirects=False)
    assert response.status_code in {302, 303}
    assert "/crm/new" in response.headers["Location"]


def test_new_followup_is_forced_to_usa(client, app):
    _login(client, "admin-sales@test.local")
    response = client.post(
        "/crm/new",
        data={
            "business_name": "USA Followup LLC",
            "contact_name": "John Doe",
            "phone": "5551234567",
            "email": "john@example.com",
            "preferred_channel": "Llamada",
            "industry": "Construction",
            "source": "Referido",
            "priority": "media",
            "country": "Nicaragua",  # intento manipulado
        },
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}
    with app.app_context():
        row = Client.query.filter_by(business_name="USA Followup LLC").one()
        assert row.record_type == "seguimiento"
        assert row.country == "USA"
