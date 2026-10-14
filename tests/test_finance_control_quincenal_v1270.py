"""Flujos críticos de finanzas quincenales y aislamiento Impacto / Novax."""
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app import create_app
from app.extensions import db
from app.models import (
    AdvisorProject, Client, Collaborator, Income, Payment,
    ProductService, Role, Sale, SaleItem, User,
)
from app.finance_control_models import AdvisorFortnightGoal, AdvisorFortnightPayout, FinanceProjectMovement
from app.finance_control_service import (
    fortnight_bounds, fortnight_summary, local_today, monthly_ledger,
)
from scripts.seed import seed_all


class Config:
    TESTING = True
    SECRET_KEY = "finances-test-v1270"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    UPLOAD_FOLDER = str(Path(__file__).parent / "uploads_finance_1270")
    MAX_CONTENT_LENGTH = 10 * 1024 * 1024
    COMPANY_NAME = "Impacto Media Agency"
    COMPANY_TIMEZONE = "America/Managua"
    SMTP_HOST = None
    SMTP_PORT = 587
    SMTP_USER = None
    SMTP_PASSWORD = None
    SMTP_FROM = "test@example.com"
    SMTP_USE_TLS = False


def register(email, role_name, project=None, supervisor=None):
    user = User(
        name=email.split("@")[0], email=email,
        role=Role.query.filter_by(name=role_name).one(), active=True,
    )
    user.set_password("ExamplePassword2026!")
    db.session.add(user)
    db.session.flush()
    if role_name in {"advisor", "supervisor"}:
        person = Collaborator(
            user_id=user.id, job_title="Ventas", status="activo",
            advisor_project_id=project,
            supervisor_id=supervisor,
        )
        db.session.add(person)
        db.session.flush()
    return user


@pytest.fixture()
def app():
    app = create_app(Config)
    with app.app_context():
        db.create_all()
        seed_all()
        imp = AdvisorProject.query.filter_by(code="IMPACTO").one()
        nov = AdvisorProject.query.filter_by(code="NOVAX").one()
        superuser = register("chief1270@test.local", "superadmin")
        admin = register("admin1270@test.local", "admin")
        supervisor = register("super1270@test.local", "supervisor", imp.id)
        advisor = register("advisor1270@test.local", "advisor", imp.id, supervisor.collaborator.id)
        other = register("other1270@test.local", "advisor", nov.id)
        product = ProductService.query.filter_by(name="Golden").first()
        customer = Client(
            code="FC-1270-1", business_name="Cliente Impacto",
            contact_name="Contacto", record_type="cliente",
            owner_id=advisor.collaborator.id,
        )
        db.session.add(customer)
        db.session.flush()
        sale = Sale(
            sale_no="FC-VEN-1270", client_id=customer.id,
            advisor_id=advisor.collaborator.id,
            status="confirmada", currency="USD",
            sale_date=local_today(), total=1000,
            amount_paid=400, balance=600,
        )
        db.session.add(sale)
        db.session.flush()
        db.session.add(SaleItem(sale_id=sale.id, product_id=product.id,
                                description="Golden", quantity=1, total=1000))
        db.session.add(Payment(
            client_id=customer.id, sale_id=sale.id,
            effective_date=local_today(), amount=400, currency="USD",
            status="confirmado",
        ))
        starts, _ = fortnight_bounds(local_today())
        db.session.add(AdvisorFortnightGoal(
            advisor_id=advisor.collaborator.id, project_id=imp.id,
            period_start=starts, total_goal=1000, net_goal=300,
            percentage=10, updated_by_id=admin.id,
        ))
        db.session.commit()
    yield app


def login(client, username):
    response = client.post("/auth/login", data={
        "email": username+"1270@test.local",
        "password": "ExamplePassword2026!",
    })
    assert response.status_code in {302, 303}


def test_quotas_use_confirmed_payments_and_are_independent(app):
    with app.app_context():
        adviser = User.query.filter_by(email="advisor1270@test.local").one().collaborator
        stats = fortnight_summary(adviser)
        assert stats["gross"] == Decimal("400.00")
        assert stats["net"] == Decimal("400.00")
        assert stats["eligible"] is True
        assert stats["earned"] == Decimal("40.00")
        assert stats["gross_left"] == Decimal("600.00")
        assert stats["daily_required"] >= 0
        assert stats["new_clients"] == 1


def test_second_fortnight_does_not_deduct_from_first(app):
    with app.app_context():
        adviser = User.query.filter_by(email="advisor1270@test.local").one().collaborator
        sale = Sale.query.filter_by(sale_no="FC-VEN-1270").one()
        today = local_today()
        current_start, current_end = fortnight_bounds(today)
        if current_start.day == 1:
            next_period = date(today.year, today.month, 16)
        else:
            next_period = (current_end + timedelta(days=1))
        next_starts, _ = fortnight_bounds(next_period)
        db.session.add(Payment(client_id=sale.client_id, sale_id=sale.id,
                               effective_date=next_starts, amount=600,
                               currency="USD", status="confirmado"))
        db.session.add(FinanceProjectMovement(
            project_id=adviser.advisor_project_id, sale_id=sale.id,
            category="imprenta", description="Tarjetas enviadas",
            amount=200, status="ejecutado", requested_by_id=adviser.user_id,
            executed_on=next_starts,
        ))
        db.session.add(AdvisorFortnightGoal(
            advisor_id=adviser.id, project_id=adviser.advisor_project_id,
            period_start=next_starts, total_goal=800, net_goal=300,
            percentage=10, updated_by_id=adviser.user_id,
        ))
        db.session.commit()
        first = fortnight_summary(adviser, current_start)
        second = fortnight_summary(adviser, next_starts)
        assert first["gross"] == Decimal("400.00")
        assert first["deductions"] == Decimal("0.00")
        assert first["earned"] == Decimal("40.00")
        assert second["gross"] == Decimal("600.00")
        assert second["deductions"] == Decimal("200.00")
        assert second["earned"] == Decimal("40.00")


def test_superadmin_only_can_approve_exception(app):
    client = app.test_client()
    login(client, "admin")
    with app.app_context():
        sale_id = Sale.query.filter_by(sale_no="FC-VEN-1270").one().id
        proj = AdvisorProject.query.filter_by(code="IMPACTO").one().id
    response = client.post("/finance-control/movements", data={
        "project_id": proj, "sale_id": sale_id,
        "category": "imprenta", "description": "Envío urgente",
        "amount": "100",
    })
    assert response.status_code == 302
    with app.app_context():
        row = FinanceProjectMovement.query.one()
        assert row.status == "pendiente_autorizacion"
        assert row.executed_on is None
        movement_id = row.id
    assert client.post(f"/finance-control/movements/{movement_id}/decision", data={
        "decision": "aprobar", "reason": "Cliente urgente",
    }).status_code == 403
    client.post("/auth/logout")
    login(client, "chief")
    response = client.post(f"/finance-control/movements/{movement_id}/decision", data={
        "decision": "aprobar", "reason": "Autorización especial",
    })
    assert response.status_code == 302
    with app.app_context():
        row = db.session.get(FinanceProjectMovement, movement_id)
        assert row.status == "autorizado"
        assert row.authorized_by_id is not None
        assert row.executed_on is None
    response = client.post(f"/finance-control/movements/{movement_id}/execute", data={
        "executed_on": str(local_today()),
    })
    assert response.status_code == 302
    with app.app_context():
        row = db.session.get(FinanceProjectMovement, movement_id)
        assert row.status == "ejecutado"


def test_advisor_cannot_view_global_finances_or_edit_goals(app):
    client = app.test_client()
    login(client, "advisor")
    assert client.get("/finance-control/").status_code == 403
    assert client.get("/finance-control/goals").status_code == 403
    assert client.get("/finance-control/payouts").status_code == 403
    assert client.get("/finance-control/advisor").status_code == 200
    assert b"Mi rendimiento quincenal" in client.get("/finance-control/advisor").data


def test_supervisor_only_manages_subordinates(app):
    client = app.test_client()
    login(client, "super")
    with app.app_context():
        other_id = User.query.filter_by(email="other1270@test.local").one().collaborator.id
        own_id = User.query.filter_by(email="advisor1270@test.local").one().collaborator.id
        today = str(local_today())
    assert client.post("/finance-control/goals", data={
        "advisor_id": other_id, "period": today,
        "total_goal": "1000", "net_goal": "200", "percentage": "10",
    }).status_code == 403
    assert client.post("/finance-control/goals", data={
        "advisor_id": own_id, "period": today,
        "total_goal": "1200", "net_goal": "200", "percentage": "12",
    }).status_code == 302


def test_payout_and_project_reports_do_not_duplicate_mirrored_income(app):
    client = app.test_client()
    login(client, "admin")
    with app.app_context():
        sale = Sale.query.filter_by(sale_no="FC-VEN-1270").one()
        db.session.add(Income(
            sale_id=sale.id, client_id=sale.client_id,
            effective_date=local_today(), concept="Pago espejo",
            amount=400, currency="USD", status="confirmado",
        ))
        db.session.commit()
        grouped, entries, _ = monthly_ledger(local_today().year, local_today().month)
        project_id = sale.advisor.advisor_project_id
        assert grouped[project_id]["income"] == Decimal("400.00")
        adviser_id = sale.advisor_id
    assert client.get("/finance-control/").status_code == 200
    paid = client.post("/finance-control/payouts", data={
        "period": str(local_today()), "advisor_id": adviser_id,
        "amount": "30",
    })
    assert paid.status_code == 302
    with app.app_context():
        assert AdvisorFortnightPayout.query.count() == 1
        assert AdvisorFortnightPayout.query.one().amount == Decimal("30.00")
    client.post("/finance-control/payouts", data={
        "period": str(local_today()), "advisor_id": adviser_id,
        "amount": "20",
    })
    with app.app_context():
        assert AdvisorFortnightPayout.query.count() == 1


def test_finance_dashboard_and_advisor_home_pages_render(app):
    client = app.test_client()
    login(client, "chief")
    assert client.get("/finance-control/").status_code == 200
    assert client.get("/finance-control/goals").status_code == 200
    assert client.get("/finance-control/movements").status_code == 200
    assert client.get("/finance-control/payouts").status_code == 200
    client.post("/auth/logout")
    login(client, "advisor")
    response = client.get("/dashboard/")
    assert response.status_code == 200
    assert b"Mi Meta Total" in response.data
