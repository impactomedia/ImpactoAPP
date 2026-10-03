from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app import create_app
from app.client_v2_models import ClientOperationalProfile
from app.extensions import db
from app.models import (
    Client,
    ClientContract,
    Collaborator,
    Interaction,
    Notification,
    ProductService,
    Quote,
    Renewal,
    Role,
    Sale,
    Task,
    User,
)
from scripts.seed import seed_all


class TestConfig:
    TESTING = True
    SECRET_KEY = "renewals-v14-test"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    WTF_CSRF_TIME_LIMIT = None
    UPLOAD_FOLDER = str(Path(__file__).parent / "uploads_renewals_v14")
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
        admin = User(name="Admin Renovaciones", email="admin-ren@test.local", role=admin_role, active=True)
        admin.set_password("Test123!")
        db.session.add(admin)

        advisor_role = Role.query.filter_by(name="advisor").one()
        advisor_user = User(name="Asesor Renovaciones", email="advisor-ren@test.local", role=advisor_role, active=True)
        advisor_user.set_password("Test123!")
        db.session.add(advisor_user)
        db.session.flush()

        advisor = Collaborator(
            user_id=advisor_user.id,
            code="REN-ADV",
            job_title="Asesor",
            department="Ventas",
            status="activo",
            join_date=date.today(),
        )
        db.session.add(advisor)
        db.session.flush()

        customer = Client(
            code="REN-CLI",
            business_name="Cliente Renovable",
            contact_name="Contacto",
            owner_id=advisor.id,
            record_type="cliente",
            pipeline_stage="venta_cerrada",
            client_status="activo",
            country="USA",
        )
        db.session.add(customer)
        db.session.flush()

        product = ProductService.query.filter_by(name="6 Meses").one()
        product.base_price = Decimal("750")
        product.currency = "USD"

        contract = ClientContract(
            client_id=customer.id,
            product_id=product.id,
            status="activo",
            starts_on=date.today() - timedelta(days=150),
            ends_on=date.today() + timedelta(days=30),
            agreed_price=Decimal("750"),
            principal=True,
            notes="Contrato de prueba.",
        )
        db.session.add(contract)

        db.session.add(
            ClientOperationalProfile(
                client_id=customer.id,
                domain_name="cliente-renovable.com",
                domain_renews_on=date.today() + timedelta(days=15),
                hosting_renews_on=date.today() + timedelta(days=60),
            )
        )
        db.session.commit()

    yield app


@pytest.fixture()
def client(app):
    return app.test_client()


def _login(client, email="admin-ren@test.local"):
    response = client.post(
        "/auth/login",
        data={"email": email, "password": "Test123!"},
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}


def test_center_syncs_contract_domain_hosting_and_creates_alert_work(client, app):
    _login(client)
    response = client.get("/renewals/")
    assert response.status_code == 200
    assert b"Cliente Renovable" in response.data
    assert b"Dominio" in response.data
    assert b"Hosting" in response.data
    assert b"6 Meses" in response.data

    with app.app_context():
        customer = Client.query.filter_by(code="REN-CLI").one()
        rows = Renewal.query.filter_by(client_id=customer.id).all()
        assert {row.renewal_type for row in rows} >= {"6 Meses", "Dominio", "Hosting"}

        contract_row = next(row for row in rows if row.renewal_type == "6 Meses")
        task = Task.query.filter(
            Task.client_id == customer.id,
            Task.task_type == "renovacion",
            Task.description.like(f"%[RENOVACION:{contract_row.id}]%"),
        ).first()
        assert task is not None
        assert task.assignee_id == customer.owner_id

        owner_user_id = customer.owner.user_id
        assert Notification.query.filter_by(user_id=owner_user_id).count() >= 1


def test_contact_is_saved_as_client_interaction(client, app):
    _login(client)
    client.get("/renewals/")

    with app.app_context():
        renewal_id = Renewal.query.filter_by(renewal_type="6 Meses").one().id

    response = client.post(
        f"/renewals/{renewal_id}/contact",
        data={
            "channel": "whatsapp",
            "result": "Interesado",
            "notes": "Cliente solicita propuesta actualizada.",
        },
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}

    with app.app_context():
        row = db.session.get(Renewal, renewal_id)
        assert row.status == "contactado"
        interaction = Interaction.query.filter_by(client_id=row.client_id).order_by(Interaction.id.desc()).first()
        assert interaction is not None
        assert interaction.interaction_type == "whatsapp"
        assert "propuesta actualizada" in interaction.notes


def test_renewal_quote_is_marked_and_not_a_normal_quote_flow(client, app):
    _login(client)
    client.get("/renewals/")

    with app.app_context():
        renewal_id = Renewal.query.filter_by(renewal_type="6 Meses").one().id

    response = client.post(
        f"/renewals/{renewal_id}/quote",
        data={"amount": "800"},
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}

    with app.app_context():
        quote = Quote.query.order_by(Quote.id.desc()).first()
        assert quote is not None
        assert Decimal(str(quote.total)) == Decimal("800.00")
        assert f"[RENOVACION:{renewal_id}]" in quote.notes


def test_confirming_contract_renewal_creates_new_sale_and_period(client, app):
    _login(client)
    client.get("/renewals/")

    with app.app_context():
        old_contract = ClientContract.query.one()
        old_contract_id = old_contract.id
        renewal_id = Renewal.query.filter_by(contract_id=old_contract_id).one().id

    response = client.post(
        f"/renewals/{renewal_id}/complete",
        data={
            "amount": "750",
            "initial_payment": "750",
            "payment_method": "Zelle",
        },
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}

    with app.app_context():
        old_contract = db.session.get(ClientContract, old_contract_id)
        renewal = db.session.get(Renewal, renewal_id)
        contracts = ClientContract.query.order_by(ClientContract.id).all()
        assert len(contracts) == 2
        new_contract = contracts[-1]

        assert old_contract.status == "inactivo"
        assert old_contract.principal is False
        assert renewal.status == "renovado"
        assert new_contract.status == "activo"
        assert new_contract.principal is True
        assert new_contract.starts_on > old_contract.ends_on
        assert Sale.query.count() == 1
        assert Decimal(str(Sale.query.one().amount_paid)) == Decimal("750.00")

        next_renewal = Renewal.query.filter_by(contract_id=new_contract.id, status="pendiente").first()
        assert next_renewal is not None

        pending_task = Task.query.filter(
            Task.description.like(f"%[RENOVACION:{renewal_id}]%"),
            Task.status.notin_(["completada", "cancelada"]),
        ).first()
        assert pending_task is None


def test_external_domain_renewal_updates_next_date_and_preserves_history(client, app):
    _login(client)
    client.get("/renewals/")

    with app.app_context():
        row = Renewal.query.filter_by(renewal_type="Dominio").one()
        renewal_id = row.id
        next_due = row.due_date + timedelta(days=365)

    response = client.post(
        f"/renewals/{renewal_id}/complete",
        data={
            "amount": "120",
            "initial_payment": "120",
            "payment_method": "transferencia",
            "next_due_date": next_due.isoformat(),
        },
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}

    with app.app_context():
        old = db.session.get(Renewal, renewal_id)
        profile = ClientOperationalProfile.query.one()
        assert old.status == "renovado"
        assert profile.domain_renews_on == next_due
        assert Renewal.query.filter_by(
            client_id=old.client_id,
            renewal_type="Dominio",
            due_date=next_due,
            status="pendiente",
        ).first() is not None
        assert Sale.query.count() == 1


def test_no_renewal_records_reason(client, app):
    _login(client)
    client.get("/renewals/")

    with app.app_context():
        renewal_id = Renewal.query.filter_by(renewal_type="Hosting").one().id

    response = client.post(
        f"/renewals/{renewal_id}/decline",
        data={"reason": "Cliente migrará el hosting a otro proveedor."},
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}

    with app.app_context():
        row = db.session.get(Renewal, renewal_id)
        assert row.status == "no_renueva"
        assert "otro proveedor" in row.notes
