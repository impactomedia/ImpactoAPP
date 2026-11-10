"""Regresión para la organización de tareas por proyecto y cliente."""
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from app import create_app
from app.extensions import db
from app.models import Client, Project, Role, Task, User
from app.task_grouping import filter_task_groups, organize_tasks
from scripts.seed import seed_all


def make_task(task_id, title, *, project=None, client=None, status="pendiente", due_at=None):
    return NS(
        id=task_id,
        title=title,
        project=project,
        client=client,
        task_type="interna",
        priority="media",
        status=status,
        due_at=due_at,
        assignee=None,
    )


def test_groups_tasks_by_real_project_and_client_fallback():
    customer = NS(id=17, business_name="Cliente Uno")
    other = NS(id=18, business_name="Cliente Dos")
    website = NS(id=101, name="Sitio web", client=customer)
    social = NS(id=102, name="Redes sociales", client=customer)
    tasks = [
        make_task(1, "Diseñar portada", project=website),
        make_task(2, "Configurar cuentas", project=social),
        make_task(3, "Llamar al cliente", client=other),
        make_task(4, "Reunión interna"),
        make_task(5, "Corregir footer", project=website, status="completada"),
    ]
    groups = organize_tasks(tasks, now=datetime(2026, 11, 10))
    assert [group["key"] for group in groups] == [
        "project:102", "project:101", "client:18", "general",
    ]
    assert [group["total"] for group in groups] == [1, 2, 1, 1]
    assert groups[1]["open_count"] == 1
    assert groups[1]["completed_count"] == 1
    assert sum(group["total"] for group in groups) == len(tasks)


def test_completed_tasks_hidden_only_in_default_view():
    customer = NS(id=7, business_name="Empresa Prueba")
    project = NS(id=23, name="Proyecto de prueba", client=customer)
    groups = organize_tasks([
        make_task(1, "Pendiente", project=project),
        make_task(2, "Terminada", project=project, status="completada"),
        make_task(3, "Cancelada", project=project, status="cancelada"),
    ])
    default = filter_task_groups(groups)
    assert [task.title for task in default[0]["tasks"]] == ["Pendiente"]
    assert len(filter_task_groups(groups, status="all")[0]["tasks"]) == 3
    assert [task.title for task in filter_task_groups(groups, status="completada")[0]["tasks"]] == ["Terminada"]


def test_filters_do_not_show_tasks_from_other_projects():
    client = NS(id=4, business_name="Empresa")
    a = NS(id=11, name="Proyecto A", client=client)
    b = NS(id=12, name="Proyecto B", client=client)
    groups = organize_tasks([
        make_task(1, "Diseño A", project=a),
        make_task(2, "Diseño B", project=b),
    ])
    filtered = filter_task_groups(groups, group_key="project:11")
    assert len(filtered) == 1
    assert [task.title for task in filtered[0]["tasks"]] == ["Diseño A"]
    assert filter_task_groups(groups, group_key="project:999") == []
    assert [task.title for task in filter_task_groups(groups, search="diseño b")[0]["tasks"]] == ["Diseño B"]


class Config:
    TESTING = True
    SECRET_KEY = "test-project-task-grouping"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    UPLOAD_FOLDER = str(Path(__file__).parent / "uploads_task_groups")
    COMPANY_NAME = "Impacto Media Agency"
    COMPANY_TIMEZONE = "America/Managua"
    SMTP_HOST = None
    SMTP_PORT = 587
    SMTP_USER = None
    SMTP_PASSWORD = None
    SMTP_FROM = "test@example.com"
    SMTP_USE_TLS = False


@pytest.fixture()
def prepared_app():
    app = create_app(Config)
    with app.app_context():
        db.create_all()
        seed_all()
        admin_role = Role.query.filter_by(name="superadmin").one()
        user = User(
            name="Pruebas de tareas",
            email="task-groups@test.local",
            role=admin_role,
            active=True,
        )
        user.set_password("Test123456!")
        db.session.add(user)
        db.session.flush()
        first = Client(
            code="TASK-G-01", business_name="Cliente Alpha",
            contact_name="Contacto Alpha", record_type="cliente",
        )
        second = Client(
            code="TASK-G-02", business_name="Cliente Beta",
            contact_name="Contacto Beta", record_type="cliente",
        )
        db.session.add_all([first, second])
        db.session.flush()
        project = Project(
            project_no="PRJ-TASK-G", client_id=first.id,
            name="Proyecto Alpha", starts_on=date.today(),
        )
        db.session.add(project)
        db.session.flush()
        db.session.add_all([
            Task(
                title="Tarea activa Alpha", client_id=first.id,
                project_id=project.id, status="pendiente",
            ),
            Task(
                title="Tarea cerrada Alpha", client_id=first.id,
                project_id=project.id, status="completada",
            ),
            Task(
                title="Tarea activa Beta", client_id=second.id,
                status="en_proceso",
            ),
            Task(title="Tarea interna independiente", status="pendiente"),
        ])
        db.session.commit()
        yield app, project.id
        db.session.remove()


def test_task_page_groups_and_filters_without_losing_closed_tasks(prepared_app):
    app, project_id = prepared_app
    browser = app.test_client()
    login = browser.post("/auth/login", data={
        "email": "task-groups@test.local", "password": "Test123456!",
    })
    assert login.status_code in {302, 303}

    page = browser.get("/operations/tasks")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "Tareas por proyecto" in html
    assert "Proyecto Alpha" in html
    assert "Cliente Beta" in html
    assert "Sin proyecto ni cliente" in html
    assert "Tarea activa Alpha" in html
    assert "Tarea cerrada Alpha" not in html

    all_tasks = browser.get("/operations/tasks?status=all")
    assert all_tasks.status_code == 200
    assert "Tarea cerrada Alpha" in all_tasks.get_data(as_text=True)

    selected = browser.get(f"/operations/tasks?group=project:{project_id}")
    body = selected.get_data(as_text=True)
    assert "Tarea activa Alpha" in body
    assert "Tarea activa Beta" not in body
    assert "Tarea interna independiente" not in body
