"""Importación de contratos con fechas históricas comprobables."""
from datetime import date
from pathlib import Path

import pytest

from app import create_app
from app.client_excel_importer import (
    _contract_dates, _product_mapping, _resolve_owner,
    _upsert_safe_contract, analyze_client_payload,
)
from app.client_excel_parser import _slot_payload, parse_date_value
from app.extensions import db
from app.models import Client, ClientContract, Collaborator, ProductService, Renewal, Role, User
from scripts.seed import seed_all


class Config:
    TESTING = True
    SECRET_KEY = "import-dates-tests"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    UPLOAD_FOLDER = str(Path(__file__).parent / "uploads_import_dates")
    COMPANY_TIMEZONE = "America/Managua"
    SMTP_HOST = None


@pytest.fixture()
def app():
    instance = create_app(Config)
    with instance.app_context():
        db.create_all()
        seed_all()
        for name in ("Diana Gutierrez", "Nicole Cuadra", "Lia Mendez", "Jessy Montalvan"):
            user = User(
                name=name, email=name.lower().replace(" ", ".")+"@sample.local",
                role=Role.query.filter_by(name="advisor").one(), active=True,
            )
            user.set_password("TestSecure123!")
            db.session.add(user)
            db.session.flush()
            db.session.add(Collaborator(
                user_id=user.id, code="C-"+str(user.id),
                job_title="Asesor", status="activo",
            ))
        db.session.commit()
    return instance


def payload(plan, *, start="2026-04-09", end=None, end_kind="missing",
            end_precision="none", investments=False, advisor="Jennifer"):
    return dict(
        business_name="Empresa de prueba NOVAX",
        contact_name="Persona de prueba",
        advisor=advisor, phone="(202) 555-0129",
        email="sample-merge@sample.local", current_plan_label=plan,
        plan_start=start, plan_start_precision="exact",
        plan_end=end, plan_end_kind=end_kind, plan_end_precision=end_precision,
        multiple_investments=investments, benefits="Beneficios contratados",
        courtesies="",
    )


def test_parser_excel_dates_and_permanent_marker():
    assert parse_date_value("09/04/2026") == (date(2026, 4, 9), "exact")
    cells = {
        (4, 2): "Empresa", (13, 2): "09/04/2026",
        (14, 2): "PERMANENTE", (1, 1): "#1", (2, 1): "Golden",
    }
    record = _slot_payload(cells, 1, "#1")
    assert record["plan_start"] == "2026-04-09"
    assert record["plan_end"] is None
    assert record["plan_end_kind"] == "permanent"


def test_esencial_historic_name_and_valid_original_dates(app):
    with app.app_context():
        a = analyze_client_payload(payload(
            "ESENCIAL - 6 MESES", start="2026-04-09",
            end="2026-10-09", end_kind="date", end_precision="exact",
        ))
        assert a["product"].name == "Impacto Esencial - 6 Meses"
        assert a["product_confidence"] == "high"
        assert a["safe_contract"] is True
        assert a["date_status"] == "original"
        assert a["contract_start"] == date(2026, 4, 9)
        assert a["contract_end"] == date(2026, 10, 9)


def test_missing_end_date_is_calculated_by_calendar_months(app):
    with app.app_context():
        a = analyze_client_payload(payload(
            "Plan Esencial - 6 meses", start="2026-03-20"
        ))
        assert a["safe_contract"] is True
        assert a["date_status"] == "calculated"
        assert a["contract_end"] == date(2026, 9, 20)

        cm = analyze_client_payload(payload(
            "Community Manager Esencial - Solo Redes Sociales", start="2026-01-31"
        ))
        assert cm["safe_contract"] is True
        assert cm["contract_end"] == date(2026, 2, 28)


def test_permanent_vip_accepted_but_fixed_plan_not_converted_to_lifetime(app):
    with app.app_context():
        vip = analyze_client_payload(payload("Impacto VIP", end_kind="permanent"))
        assert vip["safe_contract"] is True
        assert vip["contract_end"] is None
        assert vip["date_status"] == "permanent"
        fixed = analyze_client_payload(payload("Esencial - 6 meses", end_kind="permanent"))
        assert fixed["safe_contract"] is False
        assert "PERMANENTE" in fixed["date_reason"]
        assert fixed["contract_end"] is None


def test_inconsistent_or_unclear_end_date_awaits_review(app):
    with app.app_context():
        wrong = analyze_client_payload(payload(
            "Esencial - 6 meses", end="2027-04-16",
            end_kind="date", end_precision="exact",
        ))
        assert wrong["safe_contract"] is False
        assert wrong["date_status"] == "review"
        assert wrong["contract_end"] == date(2027, 4, 16)
        assert "2026" in wrong["date_reason"]
        invalid = analyze_client_payload(payload(
            "Esencial - 6 meses", end_kind="unrecognized"
        ))
        assert invalid["safe_contract"] is False
        vague = analyze_client_payload(payload(
            "Esencial", end=None,
        ))
        assert vague["safe_contract"] is False
        assert vague["product"] is None


def test_plus_without_negotiated_price_and_multiple_investments_never_auto_import(app):
    with app.app_context():
        plus = analyze_client_payload(payload(
            "Esencial Plus", end="2027-02-09",
            end_kind="date", end_precision="exact",
        ))
        assert plus["product"] and plus["product"].name == "Impacto Esencial Plus"
        assert plus["safe_contract"] is False
        multiple = analyze_client_payload(payload(
            "Esencial - 6 meses", investments=True,
        ))
        assert multiple["safe_contract"] is False


def test_aliases_resolve_actual_accounts_without_creating_ghost_users(app):
    with app.app_context():
        for old, expected in (
            ("Jennifer", "Diana Gutierrez"), ("Jennyfher", "Diana Gutierrez"),
            ("Jennyfer", "Diana Gutierrez"), ("Rachel", "Nicole Cuadra"),
            ("Jessenia", "Jessy Montalvan"), ("Roxana", "Lia Mendez"),
        ):
            result, reason = _resolve_owner(old)
            assert result is not None, reason
            assert result.user.name == expected


def test_safe_contract_creation_preserves_dates_and_avoids_duplicate(app):
    with app.app_context():
        record = payload("ESENCIAL - 6 MESES", start="2026-04-09")
        client = Client(
            code="CLI-EXCEL-TEST", business_name=record["business_name"],
            contact_name=record["contact_name"], record_type="cliente",
            client_status="activo",
        )
        db.session.add(client)
        db.session.commit()
        analysis = analyze_client_payload(record)
        assert analysis["safe_contract"]
        contract, created, status = _upsert_safe_contract(client, record, analysis)
        assert created and status == "ok"
        assert contract.starts_on == date(2026, 4, 9)
        assert contract.ends_on == date(2026, 10, 9)
        db.session.flush()
        assert Renewal.query.filter_by(contract_id=contract.id).count() == 1
        again, created_again, _ = _upsert_safe_contract(client, record, analysis)
        assert not created_again
        assert again.id == contract.id
        assert ClientContract.query.filter_by(client_id=client.id).count() == 1
        assert Renewal.query.filter_by(contract_id=contract.id).count() == 1


def test_no_contract_created_on_mismatching_dates(app):
    with app.app_context():
        record = payload(
            "ESENCIAL - 6 MESES", start="2026-03-20", end="2027-04-16",
            end_kind="date", end_precision="exact",
        )
        client = Client(
            code="CLI-BAD-DATES", business_name="Otro cliente", contact_name="Persona",
            record_type="cliente",
        )
        db.session.add(client)
        db.session.flush()
        analysis = analyze_client_payload(record)
        contract, created, action = _upsert_safe_contract(client, record, analysis)
        assert contract is None and not created and action == "manual_review"
        assert ClientContract.query.filter_by(client_id=client.id).count() == 0
