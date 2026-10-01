from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app import create_app
from app.extensions import db
from app.models import (
    Client,
    ClientCollaborator,
    Collaborator,
    Notification,
    Renewal,
    Role,
    Sale,
    SaleItem,
    User,
)
from app.client_v2_models import ClientOperationalProfile, ClientTeamAssignment
from app.client_v3_services import (
    ACTIVE_TEAM_STATUS,
    build_client_alerts,
    ensure_client_v3_notifications,
    sync_legacy_team_assignments,
)
from scripts.seed import seed_all


class TestConfig:
    TESTING = True
    SECRET_KEY = "test-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    WTF_CSRF_TIME_LIMIT = None
    UPLOAD_FOLDER = str(Path(__file__).parent / "uploads_phase3")
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
        _create_user("admin", "admin-p3@test.local", "Admin P3")
        _, supervisor_c = _create_user("supervisor", "supervisor-p3@test.local", "Supervisor P3")
        _, advisor_c = _create_user("advisor", "advisor-p3@test.local", "Asesor P3")
        _create_user("advisor", "other-p3@test.local", "Otro Asesor P3")
        _create_user("production", "production-p3@test.local", "Producción P3")
        advisor_c.supervisor_id = supervisor_c.id
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
        code=f"P3-{user.id:03d}",
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


def _logout(client):
    client.post("/auth/logout", follow_redirects=False)


def _make_client(app, owner_email="advisor-p3@test.local", name="Cliente Fase 3"):
    with app.app_context():
        owner = User.query.filter_by(email=owner_email).one().collaborator
        row = Client(
            code=f"P3-C-{Client.query.count()+1}",
            business_name=name,
            contact_name="Contacto",
            owner_id=owner.id,
            record_type="cliente",
            pipeline_stage="venta_cerrada",
            client_status="activo",
        )
        db.session.add(row)
        db.session.commit()
        return row.id


def test_admin_assigns_production_and_client_appears_in_my_clients(client, app):
    client_id = _make_client(app)
    with app.app_context():
        production_id = User.query.filter_by(email="production-p3@test.local").one().collaborator.id

    _login(client, "admin-p3@test.local")
    response = client.post(
        f"/clients/{client_id}/team",
        data={
            "collaborator_id": production_id,
            "role_in_client": "Website",
            "service_label": "Desarrollo web",
            "starts_on": date.today().isoformat(),
        },
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}

    _logout(client)
    _login(client, "production-p3@test.local")
    mine = client.get("/clients/mine")
    assert mine.status_code == 200
    assert b"Cliente Fase 3" in mine.data


def test_ending_assignment_preserves_history_and_removes_from_production_mine(client, app):
    client_id = _make_client(app, name="Cliente Historico P3")
    with app.app_context():
        prod = User.query.filter_by(email="production-p3@test.local").one().collaborator
        assignment = ClientTeamAssignment(
            client_id=client_id,
            collaborator_id=prod.id,
            role_in_client="Diseño",
            service_label="Branding",
            starts_on=date.today(),
            status=ACTIVE_TEAM_STATUS,
            assigned_by_id=User.query.filter_by(email="admin-p3@test.local").one().id,
        )
        db.session.add(assignment)
        db.session.commit()
        assignment_id = assignment.id

    _login(client, "admin-p3@test.local")
    response = client.post(
        f"/clients/{client_id}/team/{assignment_id}/end",
        data={"ends_on": date.today().isoformat(), "end_reason": "Servicio entregado"},
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}

    with app.app_context():
        row = db.session.get(ClientTeamAssignment, assignment_id)
        assert row.status == "finalizada"
        assert row.end_reason == "Servicio entregado"

    _logout(client)
    _login(client, "production-p3@test.local")
    mine = client.get("/clients/mine")
    assert b"Cliente Historico P3" not in mine.data


def test_supervisor_cannot_assign_outside_team(client, app):
    client_id = _make_client(app)
    with app.app_context():
        outsider = User.query.filter_by(email="other-p3@test.local").one().collaborator.id

    _login(client, "supervisor-p3@test.local")
    response = client.post(
        f"/clients/{client_id}/team",
        data={"collaborator_id": outsider, "role_in_client": "SEO"},
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}
    with app.app_context():
        assert ClientTeamAssignment.query.filter_by(client_id=client_id, collaborator_id=outsider).count() == 0


def test_legacy_assignment_is_migrated_to_v3_history(app):
    client_id = _make_client(app)
    with app.app_context():
        prod = User.query.filter_by(email="production-p3@test.local").one().collaborator
        legacy = ClientCollaborator(
            client_id=client_id,
            collaborator_id=prod.id,
            role_in_client="Producción",
            primary=True,
        )
        db.session.add(legacy)
        db.session.commit()
        legacy_id = legacy.id

        sync_legacy_team_assignments()
        db.session.commit()
        row = ClientTeamAssignment.query.filter_by(legacy_assignment_id=legacy_id).one()
        assert row.status == ACTIVE_TEAM_STATUS
        assert row.role_in_client == "Producción"


def test_alert_center_includes_domain_and_renewal(app):
    client_id = _make_client(app)
    with app.app_context():
        customer = db.session.get(Client, client_id)
        db.session.add(
            ClientOperationalProfile(
                client_id=client_id,
                domain_renews_on=date.today() + timedelta(days=15),
            )
        )
        db.session.add(
            Renewal(
                client_id=client_id,
                renewal_type="Plan Golden",
                due_date=date.today() + timedelta(days=7),
                status="pendiente",
            )
        )
        db.session.commit()
        alerts = build_client_alerts(customer)
        kinds = {row["kind"] for row in alerts}
        assert "Dominio" in kinds
        assert "Renovación" in kinds


def test_assigned_team_receives_renewal_notification(app):
    client_id = _make_client(app)
    with app.app_context():
        prod = User.query.filter_by(email="production-p3@test.local").one().collaborator
        prod_user_id = prod.user_id
        db.session.add(
            ClientTeamAssignment(
                client_id=client_id,
                collaborator_id=prod.id,
                role_in_client="Website",
                starts_on=date.today(),
                status=ACTIVE_TEAM_STATUS,
            )
        )
        db.session.add(
            Renewal(
                client_id=client_id,
                renewal_type="Hosting",
                due_date=date.today() + timedelta(days=7),
                status="pendiente",
            )
        )
        db.session.commit()

        ensure_client_v3_notifications()
        db.session.commit()
        notification = Notification.query.filter_by(user_id=prod_user_id).first()
        assert notification is not None
        assert "Renovación" in notification.title


def test_production_coordination_does_not_render_financial_events(client, app):
    client_id = _make_client(app, name="Cliente Privado Finanzas")
    with app.app_context():
        owner = User.query.filter_by(email="advisor-p3@test.local").one().collaborator
        prod = User.query.filter_by(email="production-p3@test.local").one().collaborator
        sale = Sale(
            sale_no="P3-FIN-001",
            client_id=client_id,
            advisor_id=owner.id,
            sale_date=date.today(),
            status="confirmada",
            currency="USD",
            total=Decimal("900"),
            amount_paid=0,
            balance=Decimal("900"),
        )
        db.session.add(sale)
        db.session.flush()
        db.session.add(
            SaleItem(
                sale_id=sale.id,
                description="Servicio financiero secreto",
                quantity=1,
                list_price=Decimal("900"),
                unit_price=Decimal("900"),
                total=Decimal("900"),
            )
        )
        db.session.add(
            ClientTeamAssignment(
                client_id=client_id,
                collaborator_id=prod.id,
                role_in_client="Producción",
                starts_on=date.today(),
                status=ACTIVE_TEAM_STATUS,
            )
        )
        db.session.commit()

    _login(client, "production-p3@test.local")
    response = client.get(f"/clients/{client_id}/coordination")
    assert response.status_code == 200
    assert b"P3-FIN-001" not in response.data
    assert b"Servicio financiero secreto" not in response.data
