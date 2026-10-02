from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app import create_app
from app.extensions import db
from app.models import (
    Client,
    ClientContract,
    Collaborator,
    PrintOrder,
    ProductService,
    Project,
    Role,
    Sale,
    User,
)
from app.nexora_models import SaleOperationMeta
from app.client_v2_models import ClientInstallment
from scripts.seed import seed_all


class TestConfig:
    TESTING = True
    SECRET_KEY = "nexora-additional-test"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    WTF_CSRF_TIME_LIMIT = None
    UPLOAD_FOLDER = str(Path(__file__).parent / "uploads_nexora_additional")
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

        admin_role = Role.query.filter_by(name="admin").one()
        admin = User(
            name="Admin Nexora",
            email="admin-additional@test.local",
            role=admin_role,
            active=True,
        )
        admin.set_password("Test123!")
        db.session.add(admin)

        advisor_role = Role.query.filter_by(name="advisor").one()
        advisor_user = User(
            name="Asesor Nexora",
            email="advisor-additional@test.local",
            role=advisor_role,
            active=True,
        )
        advisor_user.set_password("Test123!")
        db.session.add(advisor_user)
        db.session.flush()

        advisor = Collaborator(
            user_id=advisor_user.id,
            code="NX-ADD-ADV",
            job_title="Asesor",
            department="Ventas",
            status="activo",
            join_date=date.today(),
        )
        db.session.add(advisor)
        db.session.flush()

        for idx in range(1, 5):
            db.session.add(
                Client(
                    code=f"NX-ADD-{idx}",
                    business_name=f"Compra adicional {idx}",
                    contact_name="Contacto",
                    owner_id=advisor.id,
                    record_type="seguimiento",
                    pipeline_stage="interesado",
                    client_status="activo",
                    country="USA",
                )
            )

        material = ProductService.query.filter_by(name="Material Impreso").one()
        material.base_price = Decimal("200")
        website = ProductService.query.filter_by(name="Website").one()
        website.base_price = Decimal("500")

        db.session.commit()
    yield app


@pytest.fixture()
def client(app):
    return app.test_client()


def login(client):
    response = client.post(
        "/auth/login",
        data={"email": "admin-additional@test.local", "password": "Test123!"},
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}


def record_ids(app, code):
    with app.app_context():
        customer = Client.query.filter_by(code=code).one()
        material = ProductService.query.filter_by(name="Material Impreso").one()
        website = ProductService.query.filter_by(name="Website").one()
        return customer.id, material.id, website.id


def test_cash_additional_purchase_converts_followup_without_creating_plan(client, app):
    client_id, material_id, _ = record_ids(app, "NX-ADD-1")
    login(client)

    response = client.post(
        "/sales/new",
        data={
            "client_id": client_id,
            "sale_date": date.today().isoformat(),
            "product_id[]": [str(material_id)],
            "description[]": ["500 Business Cards"],
            "quantity[]": ["1"],
            "unit_price[]": ["200"],
            "discount[]": ["0"],
            "payment_mode": "contado",
            "initial_payment": "200",
            "initial_payment_date": date.today().isoformat(),
            "payment_method": "Zelle",
        },
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}

    with app.app_context():
        customer = db.session.get(Client, client_id)
        sale = Sale.query.filter_by(client_id=client_id).one()
        meta = SaleOperationMeta.query.filter_by(sale_id=sale.id).one()

        assert customer.record_type == "cliente"
        assert customer.client_status == "activo"
        assert meta.operation_type == "additional_purchase"
        assert meta.payment_mode == "contado"
        assert Decimal(str(sale.total)) == Decimal("200.00")
        assert Decimal(str(sale.amount_paid)) == Decimal("200.00")
        assert Decimal(str(sale.balance)) == Decimal("0.00")
        assert ClientContract.query.filter_by(sale_id=sale.id).count() == 0
        assert PrintOrder.query.filter_by(sale_id=sale.id).count() == 1


def test_delivery_purchase_creates_single_due_payment_and_project(client, app):
    client_id, _, website_id = record_ids(app, "NX-ADD-2")
    login(client)
    due = date.today() + timedelta(days=10)

    response = client.post(
        "/sales/new",
        data={
            "client_id": client_id,
            "sale_date": date.today().isoformat(),
            "product_id[]": [str(website_id)],
            "description[]": ["Website adicional"],
            "quantity[]": ["1"],
            "unit_price[]": ["500"],
            "discount[]": ["0"],
            "payment_mode": "entrega",
            "initial_payment": "200",
            "initial_payment_date": date.today().isoformat(),
            "payment_method": "Zelle",
            "due_date": due.isoformat(),
            "payment_terms": "Saldo al entregar el website.",
        },
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}

    with app.app_context():
        sale = Sale.query.filter_by(client_id=client_id).one()
        meta = sale.operation_meta
        installments = ClientInstallment.query.filter_by(sale_id=sale.id).all()

        assert meta.operation_type == "additional_purchase"
        assert meta.payment_mode == "entrega"
        assert meta.payment_terms == "Saldo al entregar el website."
        assert len(installments) == 1
        assert Decimal(str(installments[0].amount)) == Decimal("300.00")
        assert installments[0].due_date == due
        assert Project.query.filter_by(sale_id=sale.id).count() == 1
        assert ClientContract.query.filter_by(sale_id=sale.id).count() == 0


def test_financing_custom_purchase_creates_scheduled_payments(client, app):
    client_id, _, _ = record_ids(app, "NX-ADD-3")
    login(client)
    due1 = date.today() + timedelta(days=15)
    due2 = date.today() + timedelta(days=30)

    response = client.post(
        "/sales/new",
        data={
            "client_id": client_id,
            "sale_date": date.today().isoformat(),
            "product_id[]": [""],
            "description[]": ["Publicidad Meta Ads"],
            "quantity[]": ["1"],
            "unit_price[]": ["300"],
            "discount[]": ["0"],
            "payment_mode": "financiamiento",
            "payment_terms": "Dos pagos quincenales.",
            "initial_payment": "100",
            "initial_payment_date": date.today().isoformat(),
            "payment_method": "ACH",
            "installment_amount[]": ["100", "100"],
            "installment_due_date[]": [due1.isoformat(), due2.isoformat()],
            "installment_notes[]": ["Pago 1", "Pago 2"],
        },
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}

    with app.app_context():
        sale = Sale.query.filter_by(client_id=client_id).one()
        meta = sale.operation_meta
        installments = (
            ClientInstallment.query
            .filter_by(sale_id=sale.id)
            .order_by(ClientInstallment.sequence)
            .all()
        )

        assert meta.payment_mode == "financiamiento"
        assert meta.payment_terms == "Dos pagos quincenales."
        assert len(installments) == 2
        assert [Decimal(str(row.amount)) for row in installments] == [
            Decimal("100.00"),
            Decimal("100.00"),
        ]
        assert Project.query.filter_by(sale_id=sale.id).count() == 1
        assert ClientContract.query.filter_by(sale_id=sale.id).count() == 0


def test_cash_purchase_rejects_partial_initial_payment(client, app):
    client_id, _, website_id = record_ids(app, "NX-ADD-4")
    login(client)

    response = client.post(
        "/sales/new",
        data={
            "client_id": client_id,
            "sale_date": date.today().isoformat(),
            "product_id[]": [str(website_id)],
            "description[]": ["Website"],
            "quantity[]": ["1"],
            "unit_price[]": ["500"],
            "discount[]": ["0"],
            "payment_mode": "contado",
            "initial_payment": "100",
            "initial_payment_date": date.today().isoformat(),
            "payment_method": "Zelle",
        },
        follow_redirects=False,
    )
    assert response.status_code == 200

    with app.app_context():
        assert Sale.query.filter_by(client_id=client_id).count() == 0
        customer = db.session.get(Client, client_id)
        assert customer.record_type == "seguimiento"
