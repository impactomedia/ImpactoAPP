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
    available_payout, fortnight_bounds, fortnight_summary, local_today, monthly_ledger,
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


def today_date(app):
    with app.app_context():
        return local_today()


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
        assert stats["earned"] == Decimal("44.00")
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
        assert first["earned"] == Decimal("44.00")
        assert second["gross"] == Decimal("600.00")
        assert second["deductions"] == Decimal("200.00")
        assert second["earned"] == Decimal("44.00")


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
        "executed_on": str(today_date(app)),
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
        today = str(today_date(app))
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
        "period": str(today_date(app)), "advisor_id": adviser_id,
        "amount": "30",
    })
    assert paid.status_code == 302
    with app.app_context():
        assert AdvisorFortnightPayout.query.count() == 1
        assert AdvisorFortnightPayout.query.one().amount == Decimal("30.00")
    client.post("/finance-control/payouts", data={
        "period": str(today_date(app)), "advisor_id": adviser_id,
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



def test_tip_only_does_not_count_and_individual_video_does(app):
    with app.app_context():
        adviser = User.query.filter_by(email="advisor1270@test.local").one().collaborator
        for index, (description, amount) in enumerate([("Propina", 25), ("Video corto", 65)], 1):
            customer = Client(
                code=f"FC-EXTRA-{index}", business_name=f"Cliente Extra {index}",
                contact_name="Persona", record_type="cliente", owner_id=adviser.id,
            )
            db.session.add(customer)
            db.session.flush()
            sale = Sale(
                sale_no=f"FC-EXTRA-VEN-{index}", client_id=customer.id,
                advisor_id=adviser.id, sale_date=local_today(),
                status="confirmada", total=amount, amount_paid=amount, balance=0,
            )
            db.session.add(sale)
            db.session.flush()
            db.session.add(SaleItem(sale_id=sale.id, description=description,
                                    quantity=1, total=amount))
            db.session.add(Payment(
                client_id=customer.id, sale_id=sale.id, effective_date=local_today(),
                amount=amount, currency="USD", status="confirmado",
            ))
        db.session.commit()
        metrics = fortnight_summary(adviser)
        assert metrics["new_clients"] == 2
        assert metrics["new_accessories"] == 1
        assert metrics["new_memberships"] == 1
        assert metrics["tip_only_clients"] == 1


def test_later_execution_reduces_only_current_quincena_and_creates_future_adjustment(app):
    with app.app_context():
        adviser = User.query.filter_by(email="advisor1270@test.local").one().collaborator
        sale = Sale.query.filter_by(sale_no="FC-VEN-1270").one()
        starts, ends = fortnight_bounds(local_today())
        db.session.add(AdvisorFortnightPayout(
            advisor_id=adviser.id, project_id=adviser.advisor_project_id,
            period_start=starts, amount=40, paid_on=local_today(),
            paid_by_id=adviser.user_id,
        ))
        db.session.add(FinanceProjectMovement(
            project_id=adviser.advisor_project_id, sale_id=sale.id,
            category="meta_ads", description="Campaña iniciada después del abono",
            amount=150, status="ejecutado", requested_by_id=adviser.user_id,
            executed_on=local_today(),
        ))
        db.session.commit()
        first = fortnight_summary(adviser, starts)
        assert first["gross"] == Decimal("400.00")
        assert first["net"] == Decimal("250.00")
        assert first["earned"] == Decimal("27.50")
        next_day = ends + timedelta(days=1)
        next_starts, _ = fortnight_bounds(next_day)
        db.session.add(AdvisorFortnightGoal(
            advisor_id=adviser.id, project_id=adviser.advisor_project_id,
            period_start=next_starts, total_goal=500, net_goal=300,
            percentage=10, updated_by_id=adviser.user_id,
        ))
        db.session.add(Payment(
            client_id=sale.client_id, sale_id=sale.id,
            effective_date=next_starts, amount=600,
            currency="USD", status="confirmado",
        ))
        db.session.commit()
        second = fortnight_summary(adviser, next_starts)
        assert second["earned"] == Decimal("66.00")
        assert available_payout(second) == Decimal("53.50")



def test_processing_fees_reduce_company_profit_but_not_advisor_goals(app):
    from app.services import add_payment
    from app.payment_fee_models import PaymentProcessingFee

    browser = app.test_client()
    login(browser, "chief")
    response = browser.post("/finance-control/payment-fees", data={
        "method_key": "tarjeta", "percentage": "3.5", "fixed_amount": "0.50",
    })
    assert response.status_code == 302

    with app.app_context():
        sale = Sale.query.filter_by(sale_no="FC-VEN-1270").one()
        payment = add_payment(
            sale, 100, "tarjeta", effective_date=local_today(),
            registered_by_id=User.query.filter_by(email="chief1270@test.local").one().id,
        )
        db.session.commit()
        fee = PaymentProcessingFee.query.filter_by(payment_id=payment.id).one()
        assert fee.amount == Decimal("4.00")
        assert fee.percentage == Decimal("3.5000")
        assert fee.fixed_amount == Decimal("0.50")
        assert sale.amount_paid == Decimal("500.00")
        summary = fortnight_summary(sale.advisor)
        assert summary["gross"] == Decimal("500.00")
        assert summary["net"] == Decimal("500.00")
        assert summary["earned"] == Decimal("55.00")
        ledger, entries, _ = monthly_ledger(local_today().year, local_today().month)
        impact_id = sale.advisor.advisor_project_id
        assert ledger[impact_id]["income"] == Decimal("500.00")
        assert ledger[impact_id]["processing_fees"] == Decimal("4.00")
        assert ledger[impact_id]["expenses"] == Decimal("4.00")
        assert ledger[impact_id]["result"] == Decimal("496.00")
        assert sum(1 for entry in entries if entry.get("processing_fee")) == 1
    html = browser.get("/finance-control/")
    assert html.status_code == 200
    assert b"Comisiones de cobro USD" in html.data
    assert b"Tarifas de tarjeta, Zelle y otros" in html.data


def test_payment_fee_rule_changes_are_not_retroactive(app):
    from app.services import add_payment
    from app.payment_fee_models import PaymentProcessingFee

    browser = app.test_client()
    login(browser, "chief")
    browser.post("/finance-control/payment-fees", data={
        "method_key": "Zelle", "percentage": "2", "fixed_amount": "1",
    })
    with app.app_context():
        sale = Sale.query.filter_by(sale_no="FC-VEN-1270").one()
        earlier = add_payment(sale, 100, "Zelle", effective_date=local_today())
        db.session.commit()
        earlier_id = earlier.id
    browser.post("/finance-control/payment-fees", data={
        "method_key": "Zelle", "percentage": "5", "fixed_amount": "0",
    })
    with app.app_context():
        original = PaymentProcessingFee.query.filter_by(payment_id=earlier_id).one()
        assert original.amount == Decimal("3.00")
        sale = Sale.query.filter_by(sale_no="FC-VEN-1270").one()
        newer = add_payment(sale, 50, "Zelle", effective_date=local_today())
        db.session.commit()
        assert PaymentProcessingFee.query.filter_by(payment_id=newer.id).one().amount == Decimal("2.50")
        assert PaymentProcessingFee.query.filter_by(payment_id=earlier_id).one().amount == Decimal("3.00")
        ledger, _, _ = monthly_ledger(local_today().year, local_today().month)
        assert ledger[sale.advisor.advisor_project_id]["processing_fees"] == Decimal("5.50")
        # Pago cargado manualmente antes de la configuración no se retarifa.
        assert PaymentProcessingFee.query.count() == 2


def test_fees_reversed_payment_and_access_controls(app):
    from app.services import add_payment

    browser = app.test_client()
    login(browser, "admin")
    assert browser.get("/finance-control/payment-fees").status_code == 403
    assert browser.post("/finance-control/payment-fees", data={
        "method_key": "tarjeta", "percentage": "40", "fixed_amount": "100"
    }).status_code == 403
    assert browser.get("/finance-control/").status_code == 200
    browser.post("/auth/logout")
    login(browser, "advisor")
    assert browser.get("/finance-control/payment-fees").status_code == 403
    assert browser.get("/finance-control/").status_code == 403
    with app.app_context():
        sale = Sale.query.filter_by(sale_no="FC-VEN-1270").one()
        payment = add_payment(sale, 100, "tarjeta", effective_date=local_today())
        db.session.commit()
        payment.status = "reversado"
        db.session.commit()
        ledger, _, _ = monthly_ledger(local_today().year, local_today().month)
        assert ledger[sale.advisor.advisor_project_id]["processing_fees"] == Decimal("0.00")


def test_zelle_fee_is_allocated_to_novax_only(app):
    from app.services import add_payment

    browser = app.test_client()
    login(browser, "chief")
    response = browser.post("/finance-control/payment-fees", data={
        "method_key": "Zelle", "percentage": "2.5", "fixed_amount": "0",
    })
    assert response.status_code == 302
    with app.app_context():
        other = User.query.filter_by(email="other1270@test.local").one().collaborator
        novax = other.advisor_project_id
        customer = Client(
            code="NOVAX-FEE-1271", business_name="Cliente Novax Pago",
            contact_name="Persona", record_type="cliente", owner_id=other.id,
        )
        db.session.add(customer)
        db.session.flush()
        sale = Sale(
            sale_no="NOVAX-VEN-FEE-1271", client_id=customer.id,
            advisor_id=other.id, sale_date=local_today(), status="confirmada",
            currency="USD", total=200, amount_paid=0, balance=200,
        )
        db.session.add(sale)
        db.session.flush()
        db.session.add(SaleItem(sale_id=sale.id, description="Video",
                                quantity=1, total=200))
        db.session.flush()
        add_payment(sale, 200, "Zelle", effective_date=local_today())
        db.session.commit()
        ledger, _, _ = monthly_ledger(local_today().year, local_today().month)
        impacto = AdvisorProject.query.filter_by(code="IMPACTO").one().id
        assert ledger[novax]["processing_fees"] == Decimal("5.00")
        assert ledger[impacto]["processing_fees"] == Decimal("0.00")
        assert ledger[novax]["income"] == Decimal("200.00")
        assert ledger[novax]["result"] == Decimal("195.00")



def test_daniel_generates_four_percent_of_net_received_but_keeps_advisor_base(app):
    from app.services import add_payment
    from app.finance_control_models import FinanceProjectMovement
    from app.lead_commission_models import LeadGenerator, LeadCommissionPayout
    from app.lead_commission_service import assign_client_origin, net_sale_commission

    with app.app_context():
        sale = Sale.query.filter_by(sale_no="FC-VEN-1270").one()
        chief = User.query.filter_by(email="chief1270@test.local").one()
        daniel = LeadGenerator.query.filter_by(code="DANIEL").one()
        assert daniel.name == "Daniel" and daniel.percentage == Decimal("4.000")
        assign_client_origin(sale.client, daniel.id, chief.id)
        db.session.flush()
        add_payment(sale, 600, "transferencia", effective_date=local_today(), registered_by_id=chief.id)
        for category, amount in (("imprenta", 200), ("meta_ads", 100)):
            db.session.add(FinanceProjectMovement(
                project_id=sale.advisor.advisor_project_id, sale_id=sale.id,
                category=category, description="Coste ejecutado",
                amount=amount, status="ejecutado", requested_by_id=chief.id,
                executed_on=local_today(),
            ))
        db.session.commit()
        commission = net_sale_commission(sale)
        assert commission["collected"] == Decimal("1000.00")
        assert commission["deductions"] == Decimal("300.00")
        assert commission["base"] == Decimal("700.00")
        assert commission["percentage"] == Decimal("4.000")
        assert commission["commission"] == Decimal("28.00")
        assert commission["available"] == Decimal("28.00")
        assert fortnight_summary(sale.advisor)["net"] == Decimal("700.00")
        assert fortnight_summary(sale.advisor)["earned"] == Decimal("77.00")
        sale_id = sale.id
        project_id = sale.advisor.advisor_project_id

    browser = app.test_client()
    login(browser, "chief")
    response = browser.get("/finance-control/lead-commissions")
    assert response.status_code == 200 and b"Daniel" in response.data
    response = browser.post("/finance-control/lead-commissions", data={
        "sale_id": sale_id, "amount": "28", "notes": "Pago del generador",
    })
    assert response.status_code == 302
    with app.app_context():
        assert LeadCommissionPayout.query.count() == 1
        ledger, _, _ = monthly_ledger(local_today().year, local_today().month)
        assert ledger[project_id]["income"] == Decimal("1000.00")
        assert ledger[project_id]["expenses"] == Decimal("328.00")
        assert ledger[project_id]["result"] == Decimal("672.00")
        sale = db.session.get(Sale, sale_id)
        assert net_sale_commission(sale)["available"] == Decimal("0.00")
        assert fortnight_summary(sale.advisor)["net"] == Decimal("700.00")
    browser.post("/finance-control/lead-commissions", data={"sale_id": sale_id, "amount": "0.01"})
    with app.app_context():
        assert LeadCommissionPayout.query.count() == 1


def test_oscar_two_percent_empresa_zero_and_attribution_snapshots(app):
    from app.services import add_payment
    from app.lead_commission_models import LeadGenerator
    from app.lead_commission_service import assign_client_origin, net_sale_commission
    from app.finance_control_models import FinanceProjectMovement

    with app.app_context():
        other = User.query.filter_by(email="other1270@test.local").one().collaborator
        chief = User.query.filter_by(email="chief1270@test.local").one()
        oscar = LeadGenerator.query.filter_by(code="OSCAR").one()
        empresa = LeadGenerator.query.filter_by(code="EMPRESA").one()
        assert oscar.name == "Oscar" and oscar.percentage == Decimal("2.000")
        assert empresa.percentage == Decimal("0.000")
        client = Client(code="OSCAR-LEAD", business_name="Lead Oscar",
                        contact_name="Contacto", owner_id=other.id, record_type="cliente")
        db.session.add(client)
        db.session.flush()
        assign_client_origin(client, oscar.id, chief.id)
        sale = Sale(sale_no="LEAD-OSCAR-SALE", client_id=client.id, advisor_id=other.id,
                    sale_date=local_today(), status="confirmada",
                    currency="USD", total=500, amount_paid=0, balance=500)
        db.session.add(sale)
        db.session.flush()
        db.session.add(SaleItem(sale_id=sale.id, description="Servicio video",
                                quantity=1, total=500))
        db.session.flush()
        add_payment(sale, 500, "Zelle", effective_date=local_today())
        db.session.add(FinanceProjectMovement(
            project_id=other.advisor_project_id, sale_id=sale.id,
            category="meta_ads", description="Campaña", amount=100,
            status="ejecutado", requested_by_id=chief.id, executed_on=local_today(),
        ))
        db.session.commit()
        data = net_sale_commission(sale)
        assert data["base"] == Decimal("400.00")
        assert data["commission"] == Decimal("8.00")
        assert data["generator"].name == "Oscar"
        assign_client_origin(client, empresa.id, chief.id)
        db.session.commit()
        assert net_sale_commission(sale)["generator"].name == "Oscar"
        assert net_sale_commission(sale)["commission"] == Decimal("8.00")

        other_client = Client(code="EMPRESA-LEAD", business_name="Lead Empresa",
                              contact_name="Contacto", owner_id=other.id, record_type="cliente")
        db.session.add(other_client)
        db.session.flush()
        company_sale = Sale(sale_no="LEAD-EMPRESA-SALE", client_id=other_client.id,
                            advisor_id=other.id, sale_date=local_today(), status="confirmada",
                            currency="USD", total=100, amount_paid=0, balance=100)
        db.session.add(company_sale)
        db.session.flush()
        db.session.add(SaleItem(sale_id=company_sale.id, description="Diseño",
                                quantity=1, total=100))
        db.session.flush()
        add_payment(company_sale, 100, "transferencia", effective_date=local_today())
        db.session.commit()
        assert net_sale_commission(company_sale)["percentage"] == Decimal("0.000")
        assert net_sale_commission(company_sale)["commission"] == Decimal("0.00")
        assert company_sale.lead_attribution.generator.name == "Empresa"


def test_lead_generator_prospect_selection_and_permissions(app):
    from app.lead_commission_models import LeadGenerator, ClientLeadOrigin

    browser = app.test_client()
    login(browser, "advisor")
    with app.app_context():
        oscar_id = LeadGenerator.query.filter_by(code="OSCAR").one().id
        daniel_id = LeadGenerator.query.filter_by(code="DANIEL").one().id

    response = browser.post("/crm/new", data={
        "business_name": "Lead de origen identificado",
        "contact_name": "Prospecto",
        "phone": "(999) 111-2233",
        "preferred_channel": "Llamada",
        "priority": "media",
        "lead_generator_id": oscar_id,
    })
    assert response.status_code == 302
    with app.app_context():
        client = Client.query.filter_by(business_name="Lead de origen identificado").one()
        assert client.lead_origin.generator.name == "Oscar"
        cid = client.id
    assert browser.get("/finance-control/lead-commissions").status_code == 403
    assert browser.post(f"/crm/{cid}/lead-origin", data={"lead_generator_id": daniel_id}).status_code == 403
    browser.post("/auth/logout")
    login(browser, "admin")
    response = browser.post(f"/crm/{cid}/lead-origin", data={"lead_generator_id": daniel_id})
    assert response.status_code == 302
    with app.app_context():
        assert db.session.get(Client, cid).lead_origin.generator.name == "Daniel"
    assert browser.get("/finance-control/lead-commissions").status_code == 403



def test_new_sale_explicit_origin_oscar_overrides_client_and_shows_on_sales(app):
    from app.lead_commission_models import LeadGenerator
    from app.lead_commission_service import assign_client_origin, net_sale_commission

    browser = app.test_client()
    login(browser, "admin")
    with app.app_context():
        customer = Client.query.filter_by(code="FC-1270-1").one()
        daniel = LeadGenerator.query.filter_by(code="DANIEL").one()
        oscar = LeadGenerator.query.filter_by(code="OSCAR").one()
        assign_client_origin(customer, daniel.id,
            User.query.filter_by(email="admin1270@test.local").one().id)
        db.session.commit()
        customer_id, oscar_id = customer.id, oscar.id

    response = browser.get("/sales/new?client_id=" + str(customer_id))
    assert response.status_code == 200
    assert b"De qui" in response.data
    assert b"Oscar" in response.data
    assert b"Daniel" in response.data

    response = browser.post("/sales/new", data={
        "client_id": customer_id,
        "lead_generator_id": oscar_id,
        "sale_date": str(today_date(app)),
        "product_id[]": [""],
        "description[]": ["Video promocional"],
        "quantity[]": ["1"],
        "unit_price[]": ["200"],
        "discount[]": ["0"],
        "payment_mode": "contado",
        "initial_payment": "200",
        "initial_payment_date": str(today_date(app)),
        "payment_method": "Zelle",
    }, follow_redirects=False)
    assert response.status_code == 302
    assert "/sales/" in response.headers.get("Location", "")
    with app.app_context():
        sales = Sale.query.filter(Sale.sale_no != "FC-VEN-1270").all()
        assert len(sales) == 1
        sale = sales[0]
        assert sale.lead_attribution.generator.name == "Oscar"
        assert sale.lead_attribution.percentage == Decimal("2.000")
        assert sale.client.lead_origin.generator.name == "Daniel"
        assert net_sale_commission(sale)["commission"] == Decimal("4.00")
        sale_id = sale.id
    detail = browser.get(f"/sales/{sale_id}")
    assert detail.status_code == 200
    assert b"Origen del lead de esta venta" in detail.data
    assert b"Oscar" in detail.data
    # En una venta ya cobrada, Administración ve el origen pero no lo puede editar.
    assert b"saleLeadOriginCompactEditor" not in detail.data
    browser.post("/auth/logout")
    login(browser, "chief")
    super_detail = browser.get(f"/sales/{sale_id}")
    assert super_detail.status_code == 200
    assert b"saleLeadOriginCompactEditor" in super_detail.data
    assert b"data-bs-toggle=\"collapse\"" in super_detail.data
    assert b"Guardar origen" in super_detail.data
    assert b"saleLeadReason" in super_detail.data
    listing = browser.get("/sales/")
    assert listing.status_code == 200
    assert b"Origen del lead" in listing.data


def test_existing_sale_paid_origin_correction_superadmin_only_and_audited(app):
    from app.lead_commission_models import LeadGenerator, LeadCommissionPayout
    from app.lead_commission_service import net_sale_commission
    from app.models import AuditLog

    browser = app.test_client()
    with app.app_context():
        sale = Sale.query.filter_by(sale_no="FC-VEN-1270").one()
        sale_id = sale.id
        daniel_id = LeadGenerator.query.filter_by(code="DANIEL").one().id
        oscar_id = LeadGenerator.query.filter_by(code="OSCAR").one().id
        chief_id = User.query.filter_by(email="chief1270@test.local").one().id

    login(browser, "admin")
    assert browser.post(f"/sales/{sale_id}/lead-generator", data={
        "lead_generator_id": daniel_id, "reason": "Corrected source"
    }).status_code == 403
    browser.post("/auth/logout")
    login(browser, "chief")
    missing_reason = browser.post(f"/sales/{sale_id}/lead-generator", data={
        "lead_generator_id": daniel_id,
    })
    assert missing_reason.status_code == 302
    with app.app_context():
        assert db.session.get(Sale, sale_id).lead_attribution is None

    corrected = browser.post(f"/sales/{sale_id}/lead-generator", data={
        "lead_generator_id": daniel_id,
        "reason": "Cliente de base de Daniel",
    })
    assert corrected.status_code == 302
    with app.app_context():
        sale = db.session.get(Sale, sale_id)
        assert sale.lead_attribution.generator.name == "Daniel"
        assert sale.lead_attribution.percentage == Decimal("4.000")
        assert net_sale_commission(sale)["commission"] == Decimal("16.00")
        audit_row = AuditLog.query.filter_by(
            action="corregir_origen_lead_venta"
        ).order_by(AuditLog.id.desc()).first()
        assert audit_row is not None
        db.session.add(LeadCommissionPayout(
            sale_id=sale_id, generator_id=daniel_id,
            project_id=sale.advisor.advisor_project_id,
            amount=16, paid_on=local_today(), paid_by_id=chief_id,
        ))
        db.session.commit()

    response = browser.post(f"/sales/{sale_id}/lead-generator", data={
        "lead_generator_id": oscar_id, "reason": "Cambio tardío",
    })
    assert response.status_code == 302
    with app.app_context():
        sale = db.session.get(Sale, sale_id)
        assert sale.lead_attribution.generator.name == "Daniel"
    assert b"bloqueada" in browser.get(f"/sales/{sale_id}").data.lower()


def test_sale_lead_editor_permissions_for_advisor_and_unpaid_sale(app):
    from app.lead_commission_models import LeadGenerator
    browser = app.test_client()
    with app.app_context():
        adviser = User.query.filter_by(email="advisor1270@test.local").one().collaborator
        customer = Client.query.filter_by(code="FC-1270-1").one()
        sale = Sale(
            sale_no="FC-UNPAID-1273",
            client_id=customer.id, advisor_id=adviser.id,
            sale_date=local_today(), status="confirmada",
            currency="USD", total=100, balance=100, amount_paid=0,
        )
        db.session.add(sale)
        db.session.commit()
        sale_id = sale.id
        oscar_id = LeadGenerator.query.filter_by(code="OSCAR").one().id

    login(browser, "advisor")
    assert browser.post(f"/sales/{sale_id}/lead-generator", data={
        "lead_generator_id": oscar_id,
    }).status_code == 403
    browser.post("/auth/logout")
    login(browser, "admin")
    response = browser.post(f"/sales/{sale_id}/lead-generator", data={
        "lead_generator_id": oscar_id,
    })
    assert response.status_code == 302
    with app.app_context():
        assert db.session.get(Sale, sale_id).lead_attribution.generator.name == "Oscar"
