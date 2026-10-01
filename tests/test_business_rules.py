from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app import create_app
from app.extensions import db
from app.models import (
    AttendanceMark,
    Client,
    Collaborator,
    Commission,
    LeaveRequest,
    PayrollLine,
    PayrollPeriod,
    Role,
    Sale,
    SaleItem,
    User,
)
from app.services import add_payment, recalc_sale
from scripts.seed import seed_all


class TestConfig:
    TESTING = True
    SECRET_KEY = "test-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    WTF_CSRF_TIME_LIMIT = None
    UPLOAD_FOLDER = str(Path(__file__).parent / "uploads")
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
        _user_with_collaborator("advisor", "advisor@test.local", "Asesor")
        _user_with_collaborator("advisor", "other@test.local", "Otro asesor")
        _user_with_collaborator("production", "production@test.local", "Producción")
        _user_with_collaborator("hr", "hr@test.local", "RRHH")
        _user_with_collaborator("finance", "finance@test.local", "Finanzas")
        db.session.commit()
    yield app


@pytest.fixture()
def client(app):
    return app.test_client()


def _user_with_collaborator(role_name, email, name):
    role = Role.query.filter_by(name=role_name).one()
    user = User(name=name, email=email, role=role, active=True)
    user.set_password("Test123!")
    db.session.add(user)
    db.session.flush()
    collaborator = Collaborator(
        user_id=user.id,
        code=f"T-{user.id:03d}",
        job_title=name,
        department="Pruebas",
        status="activo",
        join_date=date.today(),
        vacation_balance=10,
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


def test_advisor_sales_list_is_scoped(client, app):
    with app.app_context():
        advisor = User.query.filter_by(email="advisor@test.local").one().collaborator
        other = User.query.filter_by(email="other@test.local").one().collaborator
        own_client = Client(code="OWN", business_name="Venta Visible", contact_name="A", owner_id=advisor.id, record_type="cliente")
        other_client = Client(code="OTHER", business_name="Venta Oculta", contact_name="B", owner_id=other.id, record_type="cliente")
        db.session.add_all([own_client, other_client])
        db.session.flush()
        db.session.add_all([
            Sale(sale_no="VEN-OWN", client_id=own_client.id, advisor_id=advisor.id, sale_date=date.today(), total=100, amount_paid=0, balance=100),
            Sale(sale_no="VEN-OTHER", client_id=other_client.id, advisor_id=other.id, sale_date=date.today(), total=100, amount_paid=0, balance=100),
        ])
        db.session.commit()

    _login(client, "advisor@test.local")
    response = client.get("/sales/")
    assert response.status_code == 200
    assert b"VEN-OWN" in response.data
    assert b"VEN-OTHER" not in response.data


def test_production_report_hides_financial_indicators(client):
    _login(client, "production@test.local")
    response = client.get("/reports/")
    assert response.status_code == 200
    assert b"Egresos" not in response.data
    assert b"Cobrado" not in response.data
    assert b"Comisiones" not in response.data


def test_attendance_rejects_out_of_order_mark(client, app):
    _login(client, "production@test.local")
    response = client.post("/hr/my-day/mark", data={"mark_type": "salida"}, follow_redirects=False)
    assert response.status_code in {302, 303}

    with app.app_context():
        collaborator = User.query.filter_by(email="production@test.local").one().collaborator
        assert AttendanceMark.query.filter_by(collaborator_id=collaborator.id).count() == 0


def test_leave_approval_is_idempotent(client, app):
    with app.app_context():
        employee = User.query.filter_by(email="production@test.local").one().collaborator
        request_row = LeaveRequest(
            collaborator_id=employee.id,
            leave_type="vacaciones",
            start_date=date.today() + timedelta(days=5),
            end_date=date.today() + timedelta(days=6),
            days=2,
            status="pendiente",
        )
        db.session.add(request_row)
        db.session.commit()
        leave_id = request_row.id

    _login(client, "hr@test.local")
    first = client.post(f"/hr/leave/{leave_id}/aprobar", follow_redirects=False)
    second = client.post(f"/hr/leave/{leave_id}/aprobar", follow_redirects=False)
    assert first.status_code in {302, 303}
    assert second.status_code in {302, 303}

    with app.app_context():
        employee = User.query.filter_by(email="production@test.local").one().collaborator
        assert Decimal(str(employee.vacation_balance)) == Decimal("8.00")


def test_payment_cannot_exceed_sale_balance(app):
    with app.app_context():
        advisor = User.query.filter_by(email="advisor@test.local").one().collaborator
        customer = Client(code="PAY", business_name="Pago Test", contact_name="A", owner_id=advisor.id, record_type="cliente")
        db.session.add(customer)
        db.session.flush()
        sale = Sale(sale_no="VEN-PAY", client_id=customer.id, advisor_id=advisor.id, sale_date=date.today(), total=0, amount_paid=0, balance=0)
        db.session.add(sale)
        db.session.flush()
        db.session.add(SaleItem(sale_id=sale.id, description="Servicio", quantity=1, unit_price=100, total=100, list_price=100, discount=0))
        db.session.flush()
        recalc_sale(sale)
        assert sale.balance == Decimal("100")
        with pytest.raises(ValueError):
            add_payment(sale, 150, "transferencia")


def test_payroll_pay_does_not_pay_commission_outside_period(client, app):
    with app.app_context():
        advisor = User.query.filter_by(email="advisor@test.local").one().collaborator
        customer = Client(code="COM", business_name="Comision Test", contact_name="A", owner_id=advisor.id, record_type="cliente")
        db.session.add(customer)
        db.session.flush()
        sale_inside = Sale(sale_no="VEN-C1", client_id=customer.id, advisor_id=advisor.id, sale_date=date.today(), total=100, amount_paid=100, balance=0)
        sale_outside = Sale(sale_no="VEN-C2", client_id=customer.id, advisor_id=advisor.id, sale_date=date.today(), total=100, amount_paid=100, balance=0)
        db.session.add_all([sale_inside, sale_outside])
        db.session.flush()
        inside = Commission(sale_id=sale_inside.id, advisor_id=advisor.id, amount=10, status="aprobada")
        outside = Commission(sale_id=sale_outside.id, advisor_id=advisor.id, amount=20, status="aprobada")
        db.session.add_all([inside, outside])
        db.session.flush()
        inside.created_at = datetime.utcnow()
        outside.created_at = datetime.utcnow() - timedelta(days=60)

        period = PayrollPeriod(name="Test", starts_on=date.today() - timedelta(days=5), ends_on=date.today(), status="borrador")
        db.session.add(period)
        db.session.flush()
        db.session.add(PayrollLine(period_id=period.id, collaborator_id=advisor.id, base_salary=0, commissions=10, bonuses=0, deductions=0, total=10))
        db.session.commit()
        period_id = period.id
        inside_id = inside.id
        outside_id = outside.id

    _login(client, "finance@test.local")
    response = client.post(f"/finance/payroll/{period_id}/pay", follow_redirects=False)
    assert response.status_code in {302, 303}

    with app.app_context():
        assert db.session.get(Commission, inside_id).status == "pagada"
        assert db.session.get(Commission, outside_id).status == "aprobada"
