from datetime import date, datetime, timedelta
from pathlib import Path
import json

import pytest

from app import create_app
from app.extensions import db
from app.models import (
    Attachment,
    AuditLog,
    ChangeRequest,
    Client,
    ClientComment,
    Collaborator,
    DesignVersion,
    Interaction,
    Payment,
    PrintIncident,
    PrintItem,
    PrintOrder,
    Project,
    Quote,
    Renewal,
    Role,
    Sale,
    Shipment,
    SupportTicket,
    Task,
    TaskComment,
    TicketComment,
    User,
)
from scripts.seed import seed_all


class TestConfig:
    TESTING = True
    SECRET_KEY = "timeline-test"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    WTF_CSRF_TIME_LIMIT = None
    UPLOAD_FOLDER = str(Path(__file__).parent / "uploads_timeline")
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
            name="Admin Timeline",
            email="admin-timeline@test.local",
            role=admin_role,
            active=True,
        )
        admin.set_password("Test123!")
        db.session.add(admin)
        db.session.flush()

        production_role = Role.query.filter_by(name="production").one()
        production = User(
            name="Produccion Timeline",
            email="production-timeline@test.local",
            role=production_role,
            active=True,
        )
        production.set_password("Test123!")
        db.session.add(production)
        db.session.flush()

        production_collaborator = Collaborator(
            user_id=production.id,
            code="TL-PROD",
            job_title="Producción",
            department="Desarrollo",
            status="activo",
            join_date=date.today(),
        )
        db.session.add(production_collaborator)
        db.session.flush()

        customer = Client(
            code="TL-CLIENT",
            business_name="Cliente Timeline",
            contact_name="Contacto",
            record_type="cliente",
            pipeline_stage="venta_cerrada",
            client_status="activo",
            country="USA",
        )
        db.session.add(customer)
        db.session.flush()

        interaction = Interaction(
            client_id=customer.id,
            user_id=admin.id,
            interaction_type="llamada",
            occurred_at=datetime.utcnow() - timedelta(days=8),
            subject="Llamada inicial Timeline",
            notes="Cliente interesado.",
        )
        quote = Quote(
            quote_no="COT-TL-001",
            client_id=customer.id,
            status="enviada",
            currency="USD",
            total=900,
        )
        sale = Sale(
            sale_no="VEN-TL-001",
            client_id=customer.id,
            sale_date=date.today(),
            status="confirmada",
            currency="USD",
            total=900,
            amount_paid=300,
            balance=600,
        )
        db.session.add_all([interaction, quote, sale])
        db.session.flush()

        payment = Payment(
            client_id=customer.id,
            sale_id=sale.id,
            effective_date=date.today(),
            amount=300,
            currency="USD",
            method="Zelle",
            reference="TL-ZELLE",
            status="confirmado",
            registered_by_id=admin.id,
        )
        db.session.add(payment)

        project = Project(
            project_no="PRJ-TL-001",
            client_id=customer.id,
            name="Website Timeline",
            coordinator_id=production_collaborator.id,
            department="Desarrollo",
            status="en_produccion",
            progress=40,
            starts_on=date.today(),
        )
        db.session.add(project)
        db.session.flush()

        task = Task(
            title="Diseñar Home Timeline",
            description="Diseño inicial",
            client_id=customer.id,
            project_id=project.id,
            assignee_id=production_collaborator.id,
            priority="alta",
            status="en_proceso",
        )
        db.session.add(task)
        db.session.flush()

        db.session.add(
            TaskComment(
                task_id=task.id,
                user_id=admin.id,
                body="Comentario técnico Timeline",
            )
        )
        db.session.add(
            ChangeRequest(
                project_id=project.id,
                title="Cambiar portada Timeline",
                description="Nueva portada solicitada.",
                priority="media",
                status="recibida",
            )
        )

        ticket = SupportTicket(
            ticket_no="TCK-TL-001",
            client_id=customer.id,
            ticket_type="website",
            channel="interno",
            priority="normal",
            status="en_proceso",
            subject="Soporte Website Timeline",
        )
        db.session.add(ticket)
        db.session.flush()
        db.session.add(
            TicketComment(
                ticket_id=ticket.id,
                user_id=admin.id,
                body="Comentario soporte Timeline",
            )
        )

        order = PrintOrder(
            order_no="IMP-TL-001",
            client_id=customer.id,
            sale_id=sale.id,
            status="diseno",
        )
        db.session.add(order)
        db.session.flush()

        print_item = PrintItem(
            order_id=order.id,
            name="Business Cards Timeline",
            quantity=500,
            design_status="revision",
        )
        db.session.add(print_item)
        db.session.flush()

        db.session.add(
            DesignVersion(
                print_item_id=print_item.id,
                version=1,
                status="revision",
                comment="Primera versión Timeline",
                created_by_id=admin.id,
            )
        )
        db.session.add(
            Shipment(
                order_id=order.id,
                carrier="UPS",
                tracking_number="TRACK-TL",
                shipped_at=datetime.utcnow(),
                status="en_transito",
            )
        )
        db.session.add(
            PrintIncident(
                order_id=order.id,
                print_item_id=print_item.id,
                incident_type="faltante",
                status="abierta",
                description="Faltante Timeline",
            )
        )

        db.session.add(
            Renewal(
                client_id=customer.id,
                renewal_type="Hosting Timeline",
                due_date=date.today() + timedelta(days=30),
                status="pendiente",
            )
        )
        db.session.add(
            Attachment(
                entity_type="Client",
                entity_id=customer.id,
                file_name="brief_timeline.pdf",
                file_path="uploads/brief_timeline.pdf",
                uploaded_by_id=admin.id,
            )
        )
        db.session.add(
            ClientComment(
                client_id=customer.id,
                user_id=admin.id,
                process_type="Desarrollo web",
                process_detail="Home",
                body="Comentario interno Timeline",
            )
        )

        db.session.add(
            AuditLog(
                user_id=admin.id,
                action="actualizar_proyecto",
                entity="Project",
                entity_id=str(project.id),
                before_json=json.dumps({"status": "pendiente_onboarding", "progress": 0}),
                after_json=json.dumps({"status": "en_produccion", "progress": 40}),
            )
        )
        db.session.add(
            AuditLog(
                user_id=admin.id,
                action="actualizar_tarea",
                entity="Task",
                entity_id=str(task.id),
                before_json=json.dumps({"status": "pendiente"}),
                after_json=json.dumps({"status": "en_proceso"}),
            )
        )
        db.session.add(
            AuditLog(
                user_id=admin.id,
                action="actualizar_ticket",
                entity="SupportTicket",
                entity_id=str(ticket.id),
                before_json=json.dumps({"status": "nuevo"}),
                after_json=json.dumps({"status": "en_proceso"}),
            )
        )

        db.session.commit()
        app.config["TIMELINE_CLIENT_ID"] = customer.id

    yield app


@pytest.fixture()
def client(app):
    return app.test_client()


def login(client, email):
    response = client.post(
        "/auth/login",
        data={"email": email, "password": "Test123!"},
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}


def test_admin_timeline_unifies_real_client_modules(client, app):
    login(client, "admin-timeline@test.local")
    client_id = app.config["TIMELINE_CLIENT_ID"]

    response = client.get(f"/clients/{client_id}#historial")
    assert response.status_code == 200

    body = response.data
    assert b"Timeline autom" in body
    assert b"Llamada inicial Timeline" in body
    assert b"COT-TL-001" in body
    assert b"VEN-TL-001" in body
    assert b"TL-ZELLE" in body
    assert b"Website Timeline" in body
    assert b"Disenar Home Timeline" in body or "Diseñar Home Timeline".encode() in body
    assert b"Comentario tecnico Timeline" in body or "Comentario técnico Timeline".encode() in body
    assert b"Cambiar portada Timeline" in body
    assert b"Soporte Website Timeline" in body
    assert b"Comentario soporte Timeline" in body
    assert b"IMP-TL-001" in body
    assert b"Business Cards Timeline" in body
    assert b"TRACK-TL" in body
    assert b"Faltante Timeline" in body
    assert b"Hosting Timeline" in body
    assert b"brief_timeline.pdf" in body
    assert b"Actualizar Proyecto" in body
    assert b"Estado: Pendiente Onboarding" in body


def test_production_timeline_hides_finance_and_crm_but_keeps_assigned_work(client, app):
    login(client, "production-timeline@test.local")
    client_id = app.config["TIMELINE_CLIENT_ID"]

    response = client.get(f"/clients/{client_id}#historial")
    assert response.status_code == 200

    body = response.data
    assert b"Website Timeline" in body
    assert b"IMP-TL-001" in body
    assert b"Soporte Website Timeline" in body

    assert b"VEN-TL-001" not in body
    assert b"TL-ZELLE" not in body
    assert b"COT-TL-001" not in body
    assert b"Llamada inicial Timeline" not in body
