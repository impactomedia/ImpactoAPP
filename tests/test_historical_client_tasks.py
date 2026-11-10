"""Contratos históricos: proyectos y tareas sin duplicaciones ni cambios de estado."""
from datetime import date, timedelta
from pathlib import Path

import pytest

from app import create_app
from app.client_v2_models import ClientContractDetail
from app.extensions import db
from app.historical_task_backfill import prepare_registered_clients
from app.models import Client, ClientContract, ProductService, Project, Role, Task, User
from scripts.seed import seed_all


class Config:
    TESTING = True
    SECRET_KEY = "test-registered-client-tasks"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    UPLOAD_FOLDER = str(Path(__file__).parent / "uploads_historical_tasks")
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
def prepared():
    app = create_app(Config)
    with app.app_context():
        db.create_all()
        seed_all()
        old = Client(
            code="BACKFILL-001", business_name="Agencia Histórica",
            contact_name="Histórico", record_type="cliente", client_status="activo",
        )
        ambiguous = Client(
            code="BACKFILL-002", business_name="Contratos Repetidos",
            contact_name="Histórico", record_type="cliente", client_status="activo",
        )
        finished = Client(
            code="BACKFILL-003", business_name="Proyecto Ya Entregado",
            contact_name="Histórico", record_type="cliente", client_status="activo",
        )
        orphan_client = Client(
            code="BACKFILL-004", business_name="Tareas Previas",
            contact_name="Histórico", record_type="cliente", client_status="activo",
        )
        db.session.add_all([old, ambiguous, finished, orphan_client])
        db.session.flush()
        product = ProductService(
            name="Plan de prueba de históricos", category="paquete",
            responsible_area="Desarrollo",
            components="WEBSITE\n- Website responsive\n- Certificado SSL\n\nCORTESÍAS\n- No acordada",
            active=True,
        )
        db.session.add(product)
        db.session.flush()
        old_contract = ClientContract(
            client_id=old.id, product_id=product.id,
            starts_on=date.today()-timedelta(days=120), status="activo",
        )
        duplicates = [
            ClientContract(
                client_id=ambiguous.id, product_id=product.id,
                starts_on=date.today()-timedelta(days=30), status="activo",
            ) for _ in range(2)
        ]
        finished_contract = ClientContract(
            client_id=finished.id, product_id=product.id,
            starts_on=date.today()-timedelta(days=90), status="activo",
        )
        db.session.add_all([old_contract, *duplicates, finished_contract])
        db.session.flush()
        db.session.add(ClientContractDetail(
            contract_id=old_contract.id, benefits_snapshot=product.components,
        ))
        completed = Task(
            title="Website responsive", client_id=old.id, project_id=None,
            status="completada", task_type="cliente",
        )
        db.session.add(completed)
        closed_project = Project(
            project_no="PRJ-BACKFILL-FINISHED", client_id=finished.id,
            product_id=product.id, name="Plan finalizado",
            status="entregado", progress=100,
        )
        task_only_project = Project(
            project_no="PRJ-BACKFILL-ORPHAN", client_id=orphan_client.id,
            name="Proyecto existente",
            status="en_produccion",
        )
        db.session.add_all([closed_project, task_only_project])
        db.session.flush()
        unlinked = Task(
            title="Seguimiento existente", client_id=orphan_client.id,
            status="en_proceso", assignee_id=None,
        )
        db.session.add(unlinked)
        db.session.commit()
        ids = {
            "client": old.id,
            "ambiguous": ambiguous.id,
            "finished": finished.id,
            "completed": completed.id,
            "orphan": unlinked.id,
            "orphan_project": task_only_project.id,
        }
        yield app, ids
        db.session.remove()


def test_preview_is_read_only_and_shows_missing_work(prepared):
    app, ids = prepared
    with app.app_context():
        before = (Project.query.count(), Task.query.count())
        result = prepare_registered_clients(apply=False)
        assert result["summary"]["projects_created"] == 1
        assert result["summary"]["tasks_added"] == 1
        assert result["summary"]["tasks_linked"] == 2
        assert result["summary"]["skipped_ambiguous"] == 2
        assert result["summary"]["skipped_closed"] == 1
        assert (Project.query.count(), Task.query.count()) == before
        assert db.session.get(Task, ids["completed"]).project_id is None


def test_apply_is_idempotent_keeps_old_completed_status_and_links_tasks(prepared):
    app, ids = prepared
    with app.app_context():
        summary = prepare_registered_clients(apply=True)["summary"]
        db.session.commit()
        assert summary["projects_created"] == 1
        assert summary["tasks_added"] == 1
        assert summary["tasks_linked"] == 2
        old_project = Project.query.filter_by(client_id=ids["client"]).one()
        assert old_project.status == "revision_interna"
        assert old_project.progress == 0
        assert db.session.get(Task, ids["completed"]).project_id == old_project.id
        assert db.session.get(Task, ids["completed"]).status == "completada"
        assert db.session.get(Task, ids["orphan"]).project_id == ids["orphan_project"]
        assert db.session.get(Task, ids["orphan"]).status == "en_proceso"
        new_tasks = Task.query.filter_by(
            client_id=ids["client"], project_id=old_project.id
        ).all()
        assert len(new_tasks) == 2
        assert {t.title for t in new_tasks} == {"Website responsive", "Certificado SSL"}
        assert {t.status for t in new_tasks} == {"completada", "pendiente"}
        assert Project.query.filter_by(client_id=ids["ambiguous"]).count() == 0
        assert Task.query.filter_by(client_id=ids["finished"]).count() == 0

        before = (Project.query.count(), Task.query.count())
        again = prepare_registered_clients(apply=True)["summary"]
        db.session.commit()
        assert again["projects_created"] == 0
        assert again["tasks_added"] == 0
        assert again["tasks_linked"] == 0
        assert (Project.query.count(), Task.query.count()) == before


def test_superadmin_can_preview_and_confirm_but_admin_cannot(prepared):
    app, ids = prepared
    browser = app.test_client()
    login = browser.post("/auth/login", data={
        "email": "admin@impactomedia.local", "password": "ChangeMe123!",
    })
    assert login.status_code in {302, 303}
    response = browser.get("/operations/tasks/prepare-registered")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Agencia Histórica" in body
    assert "Preparar clientes existentes" in body


    wrong = browser.post("/operations/tasks/prepare-registered", data={
        "confirmation": "NO",
    })
    assert wrong.status_code == 302
    with app.app_context():
        assert Project.query.filter_by(client_id=ids["client"]).count() == 0

    applied = browser.post("/operations/tasks/prepare-registered", data={
        "confirmation": "PREPARAR",
    })
    assert applied.status_code == 302
    with app.app_context():
        assert Project.query.filter_by(client_id=ids["client"]).count() == 1

    browser.post("/auth/logout")
    with app.app_context():
        regular = User(
            name="Administrador limitado", email="admin-historic@test.local",
            role=Role.query.filter_by(name="admin").one(), active=True,
        )
        regular.set_password("SecurePassword123")
        db.session.add(regular)
        db.session.commit()
    response = browser.post("/auth/login", data={
        "email": "admin-historic@test.local", "password": "SecurePassword123",
    })
    assert response.status_code in {302, 303}
    assert browser.get("/operations/tasks/prepare-registered").status_code == 403
    assert browser.post("/operations/tasks/prepare-registered", data={
        "confirmation": "PREPARAR",
    }).status_code == 403
