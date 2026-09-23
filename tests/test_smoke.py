from pathlib import Path
from app import create_app
from app.extensions import db
from scripts.seed import seed_all


class TestConfig:
    TESTING = True
    SECRET_KEY = "test-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    WTF_CSRF_TIME_LIMIT = None
    UPLOAD_FOLDER = str(Path(__file__).parent / "uploads")
    MAX_CONTENT_LENGTH = 10 * 1024 * 1024
    COMPANY_NAME = "Impacto Media Agency"
    COMPANY_TIMEZONE = "America/Managua"
    SMTP_HOST = None
    SMTP_PORT = 587
    SMTP_USER = None
    SMTP_PASSWORD = None
    SMTP_FROM = "test@example.com"
    SMTP_USE_TLS = False


def app_client():
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        seed_all()
    return app, app.test_client()


def test_core_pages_load_for_superadmin():
    app, client = app_client()
    response = client.post("/auth/login", data={"email": "admin@impactomedia.local", "password": "ChangeMe123!"}, follow_redirects=True)
    assert response.status_code == 200
    for path in [
        "/dashboard/", "/crm/", "/crm/pipeline", "/clients/", "/sales/", "/sales/renewals",
        "/operations/projects", "/operations/tasks", "/printing/", "/finance/", "/finance/incomes",
        "/hr/", "/support/", "/reports/", "/settings/", "/settings/products", "/settings/roles"
    ]:
        r = client.get(path, follow_redirects=True)
        assert r.status_code == 200, (path, r.status_code)
