from datetime import date, timedelta
from io import BytesIO
from pathlib import Path
from decimal import Decimal

import pytest

from app import create_app
from app.extensions import db
from app.models import AccountReceivable, Attachment, Client, Collaborator, Role, Sale, SaleItem, User
from app.client_v2_models import ClientCollectionNote, ClientDocumentMeta, ClientInstallment
from app.services import add_payment, recalc_sale
from scripts.seed import seed_all


class TestConfig:
    TESTING = True
    SECRET_KEY = "test-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    WTF_CSRF_TIME_LIMIT = None
    UPLOAD_FOLDER = str(Path(__file__).parent / "uploads_phase2")
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
        _create_user("advisor", "advisor-p2@test.local", "Asesor P2")
        _create_user("advisor", "other-p2@test.local", "Otro P2")
        _create_user("production", "production-p2@test.local", "Producción P2")
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
        code=f"P2-{user.id:03d}",
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


def _make_sale(app, owner_email="advisor-p2@test.local", business="Cliente Fase 2", total="1000"):
    with app.app_context():
        owner = User.query.filter_by(email=owner_email).one().collaborator
        customer = Client(
            code=f"P2-C-{Client.query.count()+1}",
            business_name=business,
            contact_name="Contacto",
            owner_id=owner.id,
            record_type="cliente",
            pipeline_stage="venta_cerrada",
            client_status="activo",
        )
        db.session.add(customer)
        db.session.flush()
        sale = Sale(
            sale_no=f"P2-S-{Sale.query.count()+1}",
            client_id=customer.id,
            advisor_id=owner.id,
            sale_date=date.today(),
            status="confirmada",
            currency="USD",
            total=Decimal(total),
            amount_paid=0,
            balance=Decimal(total),
        )
        db.session.add(sale)
        db.session.flush()
        db.session.add(
            SaleItem(
                sale_id=sale.id,
                description="Servicio",
                quantity=1,
                list_price=Decimal(total),
                discount=0,
                unit_price=Decimal(total),
                total=Decimal(total),
            )
        )
        receivable = AccountReceivable(
            client_id=customer.id,
            sale_id=sale.id,
            total_amount=Decimal(total),
            paid_amount=0,
            due_date=date.today() + timedelta(days=30),
            status="al_dia",
        )
        db.session.add(receivable)
        db.session.commit()
        return customer.id, sale.id


def test_installment_cannot_exceed_unscheduled_balance(client, app):
    client_id, sale_id = _make_sale(app, total="1000")
    _login(client, "advisor-p2@test.local")

    ok = client.post(
        f"/clients/{client_id}/installments",
        data={"sale_id": sale_id, "amount": "800", "due_date": (date.today()+timedelta(days=10)).isoformat()},
        follow_redirects=False,
    )
    too_much = client.post(
        f"/clients/{client_id}/installments",
        data={"sale_id": sale_id, "amount": "300", "due_date": (date.today()+timedelta(days=20)).isoformat()},
        follow_redirects=False,
    )
    assert ok.status_code in {302, 303}
    assert too_much.status_code in {302, 303}
    with app.app_context():
        rows = ClientInstallment.query.filter_by(sale_id=sale_id).all()
        assert len(rows) == 1
        assert rows[0].amount == Decimal("800.00")


def test_confirmed_payment_is_allocated_and_reversal_recalculates_installments(client, app):
    client_id, sale_id = _make_sale(app, total="1000")
    _login(client, "advisor-p2@test.local")
    for amount, days in [("400", 10), ("600", 20)]:
        response = client.post(
            f"/clients/{client_id}/installments",
            data={"sale_id": sale_id, "amount": amount, "due_date": (date.today()+timedelta(days=days)).isoformat()},
            follow_redirects=False,
        )
        assert response.status_code in {302, 303}

    with app.app_context():
        sale = db.session.get(Sale, sale_id)
        payment = add_payment(sale, Decimal("500"), "zelle", registered_by_id=None)
        db.session.commit()
        rows = ClientInstallment.query.filter_by(sale_id=sale_id).order_by(ClientInstallment.sequence).all()
        assert rows[0].paid_amount == Decimal("400.00")
        assert rows[0].status == "pagada"
        assert rows[1].paid_amount == Decimal("100.00")
        assert rows[1].status == "parcial"

        payment.status = "reversado"
        recalc_sale(sale)
        db.session.commit()
        rows = ClientInstallment.query.filter_by(sale_id=sale_id).order_by(ClientInstallment.sequence).all()
        assert rows[0].paid_amount == Decimal("0.00")
        assert rows[1].paid_amount == Decimal("0.00")
        assert rows[0].status == "pendiente"
        assert rows[1].status == "pendiente"


def test_payment_promise_updates_receivable_and_is_preserved_by_recalc(client, app):
    client_id, sale_id = _make_sale(app, total="750")
    _login(client, "advisor-p2@test.local")
    promise_date = date.today() + timedelta(days=7)
    response = client.post(
        f"/clients/{client_id}/collection-notes",
        data={
            "note_type": "promesa",
            "sale_id": sale_id,
            "promised_amount": "400",
            "promise_date": promise_date.isoformat(),
            "body": "Cliente promete completar el pago.",
        },
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}
    with app.app_context():
        note = ClientCollectionNote.query.filter_by(client_id=client_id).one()
        sale = db.session.get(Sale, sale_id)
        recalc_sale(sale)
        db.session.commit()
        assert note.note_type == "promesa"
        assert note.promised_amount == Decimal("400.00")
        assert note.status == "abierta"
        assert sale.receivable.promise_date == promise_date
        assert sale.receivable.status == "promesa_pago"


def test_client_document_gets_category_and_secure_download(client, app):
    client_id, _ = _make_sale(app, total="200")
    _login(client, "advisor-p2@test.local")
    response = client.post(
        f"/clients/{client_id}/attachment",
        data={
            "file": (BytesIO(b"documento de prueba"), "contrato.txt"),
            "category": "contrato",
            "description": "Acuerdo firmado",
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}

    with app.app_context():
        attachment = Attachment.query.filter_by(entity_type="Client", entity_id=client_id).one()
        meta = ClientDocumentMeta.query.filter_by(attachment_id=attachment.id).one()
        attachment_id = attachment.id
        assert meta.category == "contrato"
        assert meta.description == "Acuerdo firmado"

    download = client.get(f"/clients/{client_id}/attachments/{attachment_id}/download")
    assert download.status_code == 200
    assert download.data == b"documento de prueba"


def test_production_cannot_create_installments(client, app):
    client_id, sale_id = _make_sale(app, total="500")
    _login(client, "production-p2@test.local")
    response = client.post(
        f"/clients/{client_id}/installments",
        data={"sale_id": sale_id, "amount": "100", "due_date": date.today().isoformat()},
        follow_redirects=False,
    )
    assert response.status_code == 403


def test_advisor_cannot_open_other_client_statement(client, app):
    client_id, _ = _make_sale(app, owner_email="other-p2@test.local", business="Cliente Ajeno P2", total="500")
    _login(client, "advisor-p2@test.local")
    response = client.get(f"/clients/{client_id}/statement", follow_redirects=False)
    assert response.status_code == 403


def test_existing_initial_payment_is_not_reapplied_to_future_installments(client, app):
    client_id, sale_id = _make_sale(app, total="1000")
    with app.app_context():
        sale = db.session.get(Sale, sale_id)
        add_payment(sale, Decimal("300"), "zelle", registered_by_id=None)
        db.session.commit()
        assert sale.balance == Decimal("700.00")

    _login(client, "advisor-p2@test.local")
    response = client.post(
        f"/clients/{client_id}/installments",
        data={"sale_id": sale_id, "amount": "400", "due_date": (date.today()+timedelta(days=10)).isoformat()},
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}
    with app.app_context():
        row = ClientInstallment.query.filter_by(sale_id=sale_id).one()
        assert row.base_paid_amount == Decimal("300.00")
        assert row.paid_amount == Decimal("0.00")
        assert row.status == "pendiente"
