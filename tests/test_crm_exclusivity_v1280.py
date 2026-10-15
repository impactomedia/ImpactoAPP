"""Exclusividad de seguimiento: 30 días sin actividad, sin crear duplicados."""
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app import create_app
from app.crm_exclusivity import (
    classify_match, is_protected, matching_contacts, protected_until,
)
from app.extensions import db
from app.models import (
    AdvisorProject, Client, Collaborator, Interaction, OwnershipHistory, Role, User,
)
from scripts.seed import seed_all


class Config:
    TESTING = True
    PROJECT_ADMIN_SCOPE_ENABLED = True
    SECRET_KEY = "test-followup-exclusivity"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    UPLOAD_FOLDER = str(Path(__file__).parent / "uploads_crm_1280")
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
        for name, role, company in (
            ("Ana", "advisor", "IMPACTO"),
            ("Beto", "advisor", "IMPACTO"),
            ("Celia", "advisor", "NOVAX"),
            ("Jefa", "superadmin", None),
        ):
            user = User(
                name=name, email=name.lower()+"@leads.test",
                role=Role.query.filter_by(name=role).one(), active=True,
            )
            user.set_password("Password2026!")
            db.session.add(user)
            db.session.flush()
            if role == "advisor":
                person = Collaborator(
                    user_id=user.id, code="FOLLOW-"+str(user.id),
                    job_title="Asesor", status="activo",
                    advisor_project_id=projects[company].id,
                )
                db.session.add(person)
                db.session.flush()
                ids[name] = person.id
        db.session.commit()
        app.config["FOLLOW_IDS"] = ids
    yield app


def signin(browser, name):
    browser.post("/auth/logout")
    response = browser.post("/auth/login", data={
        "email": name.lower()+"@leads.test", "password": "Password2026!",
    })
    assert response.status_code in (302, 303)


def prospect(app, name, owner, days_old, phone="(202) 555-0191", kind="seguimiento"):
    with app.app_context():
        row = Client(
            code="FOLLOW-"+name.replace(" ","-"), business_name=name,
            contact_name="Contacto", phone=phone, record_type=kind,
            owner_id=app.config["FOLLOW_IDS"][owner],
        )
        db.session.add(row)
        db.session.flush()
        row.created_at = datetime.utcnow() - timedelta(days=days_old)
        db.session.commit()
        return row.id


def register(browser, business, phone, email=""):
    return browser.post("/crm/new", data={
        "business_name": business, "contact_name": "Contacto interesado",
        "phone": phone, "email": email, "priority": "media",
    }, follow_redirects=False)


def test_29_days_still_reserved_and_phone_formats_detected(app):
    row_id = prospect(app, "Servicio Primavera", "Ana", 29)
    browser = app.test_client()
    signin(browser, "Beto")
    response = register(browser, "Otro nombre", "+1 202-555-0191")
    assert response.status_code == 200
    assert b"30 d" in response.data
    with app.app_context():
        assert Client.query.count() == 1
        row = db.session.get(Client,row_id)
        assert row.owner_id == app.config["FOLLOW_IDS"]["Ana"]


def test_day_30_claims_existing_record_and_keeps_history(app):
    row_id = prospect(app, "Taller Renacer", "Ana", 31)
    browser = app.test_client()
    signin(browser, "Beto")
    response = register(browser, "Taller Renacer", "2025550191")
    assert response.status_code == 302
    assert response.headers["Location"].endswith(f"/crm/{row_id}")
    with app.app_context():
        assert Client.query.count() == 1
        row = db.session.get(Client,row_id)
        assert row.owner_id == app.config["FOLLOW_IDS"]["Beto"]
        changes = OwnershipHistory.query.filter_by(client_id=row_id).all()
        assert len(changes) == 1
        assert changes[0].previous_owner_id == app.config["FOLLOW_IDS"]["Ana"]
        assert changes[0].new_owner_id == app.config["FOLLOW_IDS"]["Beto"]
        assert is_protected(row)
        assert (protected_until(row) - datetime.utcnow()).days >= 29
    signin(browser, "Ana")
    blocked = register(browser, "Taller Renacer", "202-555-0191")
    assert blocked.status_code == 200
    with app.app_context():
        assert db.session.get(Client,row_id).owner_id == app.config["FOLLOW_IDS"]["Beto"]


def test_logged_action_renews_30_day_reservation(app):
    row_id = prospect(app, "Jardines Activos", "Ana", 75)
    with app.app_context():
        row = db.session.get(Client,row_id)
        user = row.owner.user
        activity = Interaction(
            client_id=row.id, user_id=user.id,
            interaction_type="llamada", subject="Llamada realizada",
            occurred_at=datetime.utcnow()-timedelta(days=5),
        )
        db.session.add(activity)
        db.session.commit()
        assert is_protected(row)
    browser=app.test_client()
    signin(browser, "Beto")
    response=register(browser,"Jardines Activos","2025550191")
    assert response.status_code == 200
    with app.app_context():
        assert db.session.get(Client,row_id).owner_id == app.config["FOLLOW_IDS"]["Ana"]
    with app.app_context():
        activity=Interaction.query.filter_by(client_id=row_id).one()
        activity.occurred_at=datetime.utcnow()-timedelta(days=31)
        db.session.commit()
    response=register(browser,"Jardines Activos","2025550191")
    assert response.status_code == 302
    with app.app_context():
        assert db.session.get(Client,row_id).owner_id == app.config["FOLLOW_IDS"]["Beto"]


def test_converted_customer_and_other_company_are_never_recovered(app):
    client_id=prospect(app,"Cliente Contratado","Ana",90,kind="cliente")
    browser=app.test_client()
    signin(browser,"Beto")
    assert register(browser,"Cliente Contratado","2025550191").status_code == 200
    with app.app_context():
        assert db.session.get(Client,client_id).owner_id == app.config["FOLLOW_IDS"]["Ana"]
        row=db.session.get(Client,client_id)
        db.session.delete(row)
        db.session.commit()
    other_id=prospect(app,"Otro Proyecto","Celia",90)
    assert register(browser,"Otro Proyecto","2025550191").status_code == 200
    with app.app_context():
        assert db.session.get(Client,other_id).owner_id == app.config["FOLLOW_IDS"]["Celia"]


def test_own_record_and_transfer_protection(app):
    row_id=prospect(app,"Empresa Sin Cambios","Ana",1)
    browser=app.test_client()
    signin(browser,"Ana")
    assert register(browser,"Empresa Sin Cambios","2025550191").status_code == 302
    with app.app_context():
        assert Client.query.count() == 1
    signin(browser,"Jefa")
    # El bypass administrativo anticipado exige motivo y conserva historial.
    no_reason=browser.post(f"/crm/{row_id}/transfer",data={
        "owner_id":app.config["FOLLOW_IDS"]["Beto"],
    })
    assert no_reason.status_code == 302
    with app.app_context():
        assert db.session.get(Client,row_id).owner_id == app.config["FOLLOW_IDS"]["Ana"]
    with_reason=browser.post(f"/crm/{row_id}/transfer",data={
        "owner_id":app.config["FOLLOW_IDS"]["Beto"], "reason":"Reorganización autorizada",
    })
    assert with_reason.status_code == 302
    with app.app_context():
        assert db.session.get(Client,row_id).owner_id == app.config["FOLLOW_IDS"]["Beto"]
        assert OwnershipHistory.query.filter_by(client_id=row_id).count() == 1


def test_duplicate_email_and_multiple_matching_records_are_blocked(app):
    prospect(app,"Mi Empresa 1","Ana",45,phone="(202) 555-0191")
    prospect(app,"Mi Empresa 2","Ana",45,phone="(202) 555-0192")
    browser=app.test_client()
    signin(browser,"Beto")
    # Múltiples coincidencias de nombre o teléfono: revisar, no adjudicar.
    result=register(browser,"Mi Empresa 1","(202) 555-0192")
    assert result.status_code == 200
    with app.app_context():
        assert Client.query.count() == 2
        assert all(x.owner_id == app.config["FOLLOW_IDS"]["Ana"] for x in Client.query.all())
