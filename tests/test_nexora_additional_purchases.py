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



def _record_accessory_sale(browser, app, code="NX-ADD-1"):
    customer_id, material_id, _ = record_ids(app, code)
    response = browser.post("/sales/new", data={
        "client_id": customer_id,
        "sale_date": date.today().isoformat(),
        "product_id[]": [str(material_id)],
        "description[]": ["Compra histórica de accesorios"],
        "quantity[]": ["1"], "unit_price[]": ["200"], "discount[]": ["0"],
        "payment_mode": "contado", "initial_payment": "200",
        "initial_payment_date": date.today().isoformat(),
        "payment_method": "Zelle",
    })
    assert response.status_code == 302
    with app.app_context():
        sale = Sale.query.filter_by(client_id=customer_id).one()
        return sale.id, sale.items[0].id, sale.payments[0].id


def test_historical_accessories_sale_date_edit_is_audited_without_changing_payment(client, app):
    from app.models import AuditLog, Payment
    login(client)
    sale_id, item_id, payment_id = _record_accessory_sale(client, app)
    before = client.get(f"/sales/{sale_id}")
    assert before.status_code == 200
    assert f"/sales/{sale_id}/edit".encode() in before.data
    editor = client.get(f"/sales/{sale_id}/edit")
    assert editor.status_code == 200
    assert b'Fecha real de la venta' in editor.data
    assert b'name="item_quantity_' not in editor.data   # Venta cobrada; precio protegido.

    new_date = date(2026, 3, 20)
    response = client.post(f"/sales/{sale_id}/edit", data={
        "sale_date": new_date.isoformat(),
        "notes": "Venta histórica de accesorios de Corias",
        f"item_description_{item_id}": "Accesorios entregados en marzo",
        "reason": "Corrección de fecha histórica de la compra",
    })
    assert response.status_code == 302
    with app.app_context():
        sale = db.session.get(Sale, sale_id)
        assert sale.sale_date == new_date
        assert sale.items[0].description == "Accesorios entregados en marzo"
        assert Decimal(str(sale.total)) == Decimal("200")
        assert Decimal(str(sale.amount_paid)) == Decimal("200")
        assert db.session.get(Payment, payment_id).effective_date == date.today()
        row = AuditLog.query.filter_by(
            entity="Sale", entity_id=str(sale_id), action="editar_venta"
        ).one()
        assert row.user.name == "Admin Nexora"
        assert "Corrección de fecha histórica" in row.reason
        assert '2026-03-20' in row.after_json
        assert str(date.today()) in row.before_json

    detail = client.get(f"/sales/{sale_id}")
    assert b"Modificada" in detail.data
    assert b"Historial de correcciones" in detail.data
    assert b"Admin Nexora" in detail.data
    assert b"Accesorios entregados en marzo" in detail.data


def test_paid_sale_cannot_silently_change_amount_and_requires_reason(client, app):
    login(client)
    sale_id, item_id, _ = _record_accessory_sale(client, app)
    response = client.post(f"/sales/{sale_id}/edit", data={
        "sale_date": "2026-03-20",
        f"item_description_{item_id}": "Accesorios",
        f"item_price_{item_id}": "10",  # Intento de modificar importe protegido
        "notes": "",
        "reason": "Cambio indebido del importe de una venta cobrada",
    })
    assert response.status_code == 302
    with app.app_context():
        sale = db.session.get(Sale, sale_id)
        assert sale.sale_date == date.today()
        assert sale.items[0].description == "Compra histórica de accesorios"
        assert Decimal(str(sale.total)) == Decimal("200")

    no_reason = client.post(f"/sales/{sale_id}/edit", data={
        "sale_date": "2026-03-20",
        f"item_description_{item_id}": "Accesorios",
        "reason": "",
    })
    assert no_reason.status_code == 302
    with app.app_context():
        assert db.session.get(Sale, sale_id).sale_date == date.today()


def test_uncollected_unlinked_sale_amounts_can_be_corrected_with_audit(client, app):
    from app.models import AuditLog, SaleItem, AccountReceivable
    login(client)
    with app.app_context():
        customer = Client.query.filter_by(code="NX-ADD-4").one()
        row = Sale(sale_no="VEN-UNPAID-HIST",client_id=customer.id,
                   advisor_id=customer.owner_id, sale_date=date.today(),
                   status="confirmada", currency="USD", total=150, amount_paid=0,
                   balance=150)
        db.session.add(row)
        db.session.flush()
        item = SaleItem(sale_id=row.id, description="Accesorio simple",
                        quantity=1,list_price=150, discount=0,unit_price=150,total=150)
        db.session.add(item)
        db.session.add(AccountReceivable(client_id=customer.id, sale_id=row.id,
                                        total_amount=150, paid_amount=0,status="al_dia"))
        db.session.commit()
        sale_id, item_id = row.id, item.id
    html = client.get(f"/sales/{sale_id}/edit").get_data(as_text=True)
    assert f'name="item_price_{item_id}"' in html
    result = client.post(f"/sales/{sale_id}/edit",data={
        "sale_date": "2026-03-20",
        f"item_description_{item_id}": "Paquete de accesorios",
        f"item_quantity_{item_id}": "2",
        f"item_price_{item_id}": "100",
        f"item_discount_{item_id}": "10",
        "notes": "Corregido", "reason": "Ajuste de precio histórico",
    })
    assert result.status_code == 302
    with app.app_context():
        sale=db.session.get(Sale,sale_id)
        assert Decimal(str(sale.total)) == Decimal("180.00")
        assert Decimal(str(sale.balance)) == Decimal("180.00")
        assert Decimal(str(sale.receivable.total_amount)) == Decimal("180.00")
        assert AuditLog.query.filter_by(action="editar_venta",entity_id=str(sale_id)).count() == 1


def test_only_superadmin_corrects_payment_date_and_updates_income(client, app):
    from app.models import Payment, Income, AuditLog
    login(client)
    sale_id, item_id, payment_id = _record_accessory_sale(client, app)
    new_date = date(2026, 4, 9)
    result = client.post(f"/sales/payments/{payment_id}/correct-date", data={
        "effective_date":new_date.isoformat(),
        "reason":"El pago de Corias fue registrado con fecha incorrecta",
    })
    assert result.status_code == 403
    with app.app_context():
        chief = User(name="Super Nexora",email="chief-sale-edit@test.local",
                     role=Role.query.filter_by(name="superadmin").one(), active=True)
        chief.set_password("Test123!")
        db.session.add(chief)
        db.session.commit()
    client.post("/auth/logout")
    response=client.post("/auth/login",data={
        "email":"chief-sale-edit@test.local","password":"Test123!",
    })
    assert response.status_code == 302
    changed=client.post(f"/sales/payments/{payment_id}/correct-date",data={
        "effective_date":new_date.isoformat(),
        "reason":"Fecha real del cobro de accesorios de Corias",
    })
    assert changed.status_code == 302
    with app.app_context():
        payment=db.session.get(Payment,payment_id)
        income=Income.query.filter_by(sale_id=sale_id,status="confirmado").one()
        assert payment.effective_date == new_date
        assert income.effective_date == new_date
        assert Decimal(str(income.amount)) == Decimal("200")
        sale=db.session.get(Sale,sale_id)
        assert sale.sale_date == date.today()  # Dos fechas diferentes.
        assert AuditLog.query.filter_by(
            action="corregir_fecha_pago_venta",entity_id=str(sale_id)
        ).count() == 1
    assert b"Historial de correcciones" in client.get(f"/sales/{sale_id}").data
