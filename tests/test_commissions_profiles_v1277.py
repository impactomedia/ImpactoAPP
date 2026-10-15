"""Escala oficial, comisión de supervisor y permisos de perfiles comerciales."""
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app import create_app
from app.commission_policy import commission_amount, commission_payment_date, rate_and_bonus
from app.extensions import db
from app.finance_control_service import fortnight_summary, fortnight_bounds
from app.models import AdvisorProject, Client, Collaborator, Payment, Role, Sale, User
from scripts.seed import seed_all


class Config:
    TESTING = True
    PROJECT_ADMIN_SCOPE_ENABLED = True
    SECRET_KEY = "test-commission-profiles-v1277"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    UPLOAD_FOLDER = str(Path(__file__).parent / "uploads_commission_1277")
    COMPANY_NAME = "Impacto Media Agency"
    COMPANY_TIMEZONE = "America/Managua"
    SMTP_HOST = None


@pytest.fixture()
def app():
    app = create_app(Config)
    with app.app_context():
        db.create_all()
        seed_all()
        projects = {p.code: p for p in AdvisorProject.query.all()}
        ids = {}
        for name, kind, project in (
            ("Chief", "superadmin", None),
            ("Admin Impacto", "admin", "IMPACTO"),
            ("Admin Novax", "admin", "NOVAX"),
            ("Supervisor Impacto", "supervisor", "IMPACTO"),
            ("Supervisor Novax", "supervisor", "NOVAX"),
            ("Ana", "advisor", "IMPACTO"),
            ("Beto", "advisor", "IMPACTO"),
            ("Celia", "advisor", "NOVAX"),
        ):
            user = User(name=name, email=name.lower().replace(" ", "-")+"@test.local",
                        role=Role.query.filter_by(name=kind).one(), active=True)
            user.set_password("ExamplePassword2026!")
            db.session.add(user)
            db.session.flush()
            if kind != "superadmin":
                collab = Collaborator(user_id=user.id,
                    code=f"EMP-COM-{user.id}", job_title=kind,
                    status="activo", advisor_project_id=projects[project].id)
                db.session.add(collab)
                db.session.flush()
                ids[name] = collab.id
        for name, boss in (("Ana","Supervisor Impacto"), ("Beto","Supervisor Impacto"),("Celia","Supervisor Novax")):
            db.session.get(Collaborator, ids[name]).supervisor_id = ids[boss]

        begin = date(2026, 10, 1)
        for name, amount in (("Ana", 4000), ("Supervisor Impacto", 1500), ("Celia", 5500)):
            client = Client(code="COM-"+name.replace(" ","-"),business_name=name+" client",
                            contact_name="Persona", record_type="cliente",
                            owner_id=ids[name])
            db.session.add(client)
            db.session.flush()
            sale = Sale(sale_no="COM-SAL-"+name.replace(" ","-"), client_id=client.id,
                        advisor_id=ids[name], sale_date=begin, status="confirmada",
                        currency="USD", total=amount, amount_paid=amount,balance=0)
            db.session.add(sale)
            db.session.flush()
            db.session.add(Payment(client_id=client.id, sale_id=sale.id,
                    effective_date=begin, amount=amount, currency="USD",status="confirmado"))
        db.session.commit()
        app.config["TEST_COMM_EMP"] = ids
    yield app


def auth(browser, who):
    browser.post("/auth/logout")
    return browser.post("/auth/login", data={
        "email": who.lower().replace(" ","-")+"@test.local",
        "password": "ExamplePassword2026!",
    })


@pytest.mark.parametrize("gross,pct,bonus", [
    (0, 11, 0), (1999.99, 11, 0), (2000, 12, 0),
    (2999.99, 12, 0), (3000, 13, 0),
    (3999.99, 13, 0), (4000, 14, 50),
    (5499.99, 14, 50), (5500, 15, 100),
])
def test_advisor_commission_tiers(gross, pct, bonus):
    p,b=rate_and_bonus("advisor", Decimal(str(gross)))
    assert p == Decimal(pct)
    assert b == Decimal(bonus)


def test_supervisor_15_percent_no_bonus_and_dates():
    assert commission_amount("supervisor", 6000, 5000) == {
        "percentage": Decimal("15"), "base_commission": Decimal("750.00"),
        "bonus": Decimal("0"), "earned": Decimal("750.00"),
    }
    assert commission_amount("advisor", 5500, 5000)["earned"] == Decimal("850.00")
    assert commission_amount("advisor", 6000, -20)["earned"] == Decimal("0.00")
    assert commission_payment_date(date(2026, 10, 1)) == date(2026, 10, 20)
    assert commission_payment_date(date(2026, 10, 16)) == date(2026, 11, 5)
    assert commission_payment_date(date(2026, 12, 16)) == date(2027, 1, 5)
    assert fortnight_bounds(date(2026, 10, 31)) == (date(2026,10,16),date(2026,10,31))


def test_individual_sales_only_and_supervisor_no_team_percentage(app):
    with app.app_context():
        ids=app.config["TEST_COMM_EMP"]
        ana=fortnight_summary(db.session.get(Collaborator, ids["Ana"]), date(2026,10,1))
        supervisor=fortnight_summary(db.session.get(Collaborator,ids["Supervisor Impacto"]),date(2026,10,1))
        assert ana["percentage"] == 14
        assert ana["bonus"] == Decimal("50")
        assert ana["earned"] == Decimal("610.00")
        assert supervisor["gross"] == Decimal("1500.00")   # No incluye $4000 de Ana
        assert supervisor["percentage"] == 15
        assert supervisor["bonus"] == 0
        assert supervisor["earned"] == Decimal("225.00")


def test_salary_edit_is_audited_and_limited_to_project_or_team(app):
    ids=app.config["TEST_COMM_EMP"]
    browser=app.test_client()
    route=f"/hr/advisors/{ids['Ana']}/edit"
    auth(browser, "Supervisor Impacto")
    assert browser.get("/hr/advisors").status_code == 200
    assert browser.get(route).status_code == 200
    assert browser.get(f"/hr/advisors/{ids['Celia']}/edit").status_code == 403
    r=browser.post(route,data={"name":"Ana Actualizada","phone":"1234",
        "status":"activo","work_mode":"remoto","base_salary":"800.50",
        "advisor_project_id":str(ids["Celia"]), "role_id":"1"})
    assert r.status_code == 302
    with app.app_context():
        ana=db.session.get(Collaborator,ids["Ana"])
        assert ana.base_salary == Decimal("800.50")
        assert ana.advisor_project.name == "IMPACTO"
        assert ana.user.role.name == "advisor"
    auth(browser, "Admin Novax")
    assert browser.get(route).status_code == 403
    assert browser.post(route,data={"name":"intruso","status":"activo",
               "work_mode":"remoto","base_salary":"999"}).status_code == 403
    assert browser.get(f"/hr/advisors/{ids['Celia']}/edit").status_code == 200
    auth(browser, "Admin Impacto")
    assert browser.get(route).status_code == 200
    assert browser.get(f"/hr/advisors/{ids['Celia']}/edit").status_code == 403
    auth(browser,"Chief")
    assert browser.get(f"/hr/advisors/{ids['Celia']}/edit").status_code == 200


def test_reject_non_advisors_and_invalid_salary(app):
    ids=app.config["TEST_COMM_EMP"]
    browser=app.test_client()
    auth(browser, "Supervisor Impacto")
    assert browser.get(f"/hr/advisors/{ids['Supervisor Impacto']}/edit").status_code == 403
    path=f"/hr/advisors/{ids['Ana']}/edit"
    browser.post(path,data={"name":"Ana","status":"activo","work_mode":"remoto","base_salary":"NaN"})
    with app.app_context():
        assert db.session.get(Collaborator,ids["Ana"]).base_salary != Decimal("NaN")


def test_prior_supervisor_payout_is_not_counted_as_overpayment(app):
    from app.finance_control_models import AdvisorFortnightPayout
    from app.finance_control_service import adjustment_balance
    with app.app_context():
        person=db.session.get(Collaborator, app.config["TEST_COMM_EMP"]["Supervisor Impacto"])
        db.session.add(AdvisorFortnightPayout(
            advisor_id=person.id, project_id=person.advisor_project_id,
            period_start=date(2026,10,1), amount=Decimal("200"),
            paid_on=date(2026,10,20), paid_by_id=person.user_id,
        ))
        db.session.commit()
        assert adjustment_balance(person, date(2026,10,16)) == Decimal("0.00")


def test_bonuses_follow_gross_threshold_but_rate_applies_to_net():
    value=commission_amount("advisor", Decimal("4000"), Decimal("3900"))
    assert value["percentage"] == Decimal("14")
    assert value["base_commission"] == Decimal("546.00")
    assert value["bonus"] == Decimal("50")
    assert value["earned"] == Decimal("596.00")
