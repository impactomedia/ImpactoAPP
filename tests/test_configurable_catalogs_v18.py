from datetime import date
from pathlib import Path

import pytest

from app import create_app
from app.catalog_runtime import catalog_options, refresh_runtime_catalogs
from app.extensions import db
from app.models import (
    CatalogItem,
    Holiday,
    ProductService,
    Role,
    SystemSetting,
    TaskTemplate,
    TaskTemplateItem,
    User,
)
from scripts.seed import seed_all


class TestConfig:
    TESTING = True
    SECRET_KEY = "block11-test"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    WTF_CSRF_TIME_LIMIT = None
    UPLOAD_FOLDER = str(Path(__file__).parent / "uploads_block11")
    MAX_CONTENT_LENGTH = 10 * 1024 * 1024
    COMPANY_NAME = "Impacto Media Agency"
    COMPANY_TIMEZONE = "America/Managua"
    SMTP_HOST = None
    SMTP_PORT = 587
    SMTP_USER = None
    SMTP_PASSWORD = None
    SMTP_FROM = "test@example.com"
    SMTP_USE_TLS = False
    SESSION_COOKIE_SECURE = False
    REMEMBER_COOKIE_SECURE = False


@pytest.fixture()
def app():
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        seed_all()

        role = Role.query.filter_by(name="admin").one()
        user = User(
            name="Admin Bloque 11",
            email="admin-b11@test.local",
            role=role,
            active=True,
        )
        user.set_password("Test123!")
        db.session.add(user)
        db.session.commit()

    yield app


@pytest.fixture()
def client(app):
    return app.test_client()


def _login(client):
    response = client.post(
        "/auth/login",
        data={
            "email": "admin-b11@test.local",
            "password": "Test123!",
        },
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}


def test_master_data_initializes_operational_catalogs(client, app):
    _login(client)
    response = client.get("/settings/master-data/")
    assert response.status_code == 200
    assert b"Configuraciones y cat" in response.data

    with app.app_context():
        assert CatalogItem.query.filter_by(
            category="pipeline_stage",
            code="nuevo",
        ).one().active is True
        assert CatalogItem.query.filter_by(
            category="project_status",
            code="completado",
        ).one().active is True
        assert CatalogItem.query.filter_by(
            category="currency",
            code="usd",
        ).one().label == "USD"


def test_new_pipeline_value_updates_runtime_without_code_change(client, app):
    _login(client)
    response = client.post(
        "/settings/master-data/catalog/pipeline_stage/add",
        data={
            "code": "postventa",
            "label": "Postventa",
            "sort_order": "50",
        },
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}

    with app.app_context():
        refresh_runtime_catalogs(force=True)
        from app.blueprints import crm

        assert "postventa" in crm.STAGES
        assert any(
            option["value"] == "postventa"
            for option in catalog_options("pipeline_stage")
        )


def test_protected_pipeline_value_cannot_be_disabled(client, app):
    _login(client)
    client.get("/settings/master-data/")

    with app.app_context():
        item = CatalogItem.query.filter_by(
            category="pipeline_stage",
            code="nuevo",
        ).one()
        item_id = item.id

    response = client.post(
        f"/settings/master-data/catalog/{item_id}/toggle",
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}

    with app.app_context():
        assert db.session.get(CatalogItem, item_id).active is True


def test_support_priority_rejects_unknown_sla_code(client, app):
    _login(client)
    response = client.post(
        "/settings/master-data/catalog/support_priority/add",
        data={
            "code": "critica",
            "label": "Crítica",
            "sort_order": "99",
        },
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}

    with app.app_context():
        assert CatalogItem.query.filter_by(
            category="support_priority",
            code="critica",
        ).first() is None


def test_policy_and_renewal_rules_are_saved(client, app):
    _login(client)
    client.get("/settings/master-data/")

    response = client.post(
        "/settings/master-data/policies",
        data={
            "attendance_default_tolerance_minutes": "12",
            "attendance_default_break_minutes": "25",
            "attendance_default_lunch_minutes": "55",
            "vacation_default_rate": "1.25",
            "vacation_policy_note": "Política de vacaciones B11",
            "attendance_policy_note": "Política de asistencia B11",
            "renewal_alert_days": "90,30,7,0,30",
            "currency_default": "USD",
        },
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}

    with app.app_context():
        assert SystemSetting.query.filter_by(
            key="attendance_default_tolerance_minutes"
        ).one().value == "12"
        assert SystemSetting.query.filter_by(
            key="renewal_alert_days"
        ).one().value == "90,30,7,0"
        assert SystemSetting.query.filter_by(
            key="vacation_default_rate"
        ).one().value == "1.25"


def test_holiday_can_be_created_and_updated(client, app):
    _login(client)

    response = client.post(
        "/settings/master-data/holidays/add",
        data={
            "holiday_date": "2026-12-25",
            "name": "Navidad",
            "non_working": "on",
        },
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}

    with app.app_context():
        holiday = Holiday.query.filter_by(
            holiday_date=date(2026, 12, 25)
        ).one()
        holiday_id = holiday.id
        assert holiday.non_working is True

    client.post(
        f"/settings/master-data/holidays/{holiday_id}/update",
        data={
            "holiday_date": "2026-12-25",
            "name": "Navidad / cierre",
        },
    )

    with app.app_context():
        holiday = db.session.get(Holiday, holiday_id)
        assert holiday.name == "Navidad / cierre"
        assert holiday.non_working is False


def test_task_template_configuration_uses_existing_operational_models(client, app):
    _login(client)

    with app.app_context():
        product = ProductService.query.filter_by(active=True).first()
        product_id = product.id if product else None

    response = client.post(
        "/settings/master-data/task-templates/add",
        data={
            "name": "Onboarding B11",
            "product_id": str(product_id or ""),
        },
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}

    with app.app_context():
        template = TaskTemplate.query.filter_by(name="Onboarding B11").one()
        template_id = template.id

    response = client.post(
        f"/settings/master-data/task-templates/{template_id}/items/add",
        data={
            "title": "Solicitar accesos",
            "task_type": "cliente",
            "priority": "media",
            "due_days": "2",
            "required": "on",
        },
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}

    with app.app_context():
        item = TaskTemplateItem.query.filter_by(
            template_id=template_id,
            title="Solicitar accesos",
        ).one()
        assert item.due_days == 2
        assert item.required is True


def test_payment_method_catalog_refreshes_sales_runtime(client, app):
    _login(client)
    client.get("/settings/master-data/")

    response = client.post(
        "/settings/master-data/catalog/payment_method/add",
        data={
            "code": "cash_app",
            "label": "Cash App",
            "sort_order": "90",
        },
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}

    with app.app_context():
        refresh_runtime_catalogs(force=True)
        from app.blueprints import sales

        assert "Cash App" in sales.PAYMENT_METHODS
