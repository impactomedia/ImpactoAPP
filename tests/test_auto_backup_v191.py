from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from app import create_app
from app.backup_scheduler import (
    _is_due,
    automatic_backup_policy,
    automatic_backup_status,
    run_backup_cycle,
)
from app.extensions import db
from app.models import Collaborator, Role, SystemSetting, User
from scripts.seed import seed_all


class TestConfig:
    TESTING = True
    SECRET_KEY = "backup-v191-test"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    WTF_CSRF_TIME_LIMIT = None
    UPLOAD_FOLDER = "/tmp/nexora-v191-test-uploads"
    BACKUP_DIR = "/tmp/nexora-v191-test-backups"
    MAX_CONTENT_LENGTH = 10 * 1024 * 1024
    COMPANY_NAME = "Impacto Media Agency"
    COMPANY_TIMEZONE = "UTC"
    SMTP_HOST = None
    SMTP_PORT = 587
    SMTP_USER = None
    SMTP_PASSWORD = None
    SMTP_FROM = "test@example.com"
    SMTP_USE_TLS = False
    SESSION_COOKIE_SECURE = False
    REMEMBER_COOKIE_SECURE = False


@pytest.fixture()
def app(tmp_path, monkeypatch):
    backup_dir = tmp_path / "backups"
    upload_dir = tmp_path / "uploads"
    sqlite_source = tmp_path / "backup_source.db"

    backup_dir.mkdir()
    upload_dir.mkdir()
    sqlite_source.write_bytes(b"SQLite format 3\\x00nexora-backup-test")

    monkeypatch.setenv("BACKUP_DIR", str(backup_dir))
    monkeypatch.setenv("UPLOAD_FOLDER", str(upload_dir))
    monkeypatch.setenv(
        "DATABASE_URL",
        f"sqlite:///{sqlite_source}",
    )

    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        seed_all()

        role = Role.query.filter_by(name="admin").one()
        user = User(
            name="Admin Backup",
            email="admin-backup@test.local",
            role=role,
            active=True,
        )
        user.set_password("Test123!")
        db.session.add(user)
        db.session.flush()

        db.session.add(
            Collaborator(
                user_id=user.id,
                code="BACKUP-ADM",
                job_title="Administración",
                department="Administración",
                status="activo",
                join_date=date.today(),
            )
        )
        db.session.commit()

    yield app


@pytest.fixture()
def client(app):
    return app.test_client()


def _login(client):
    response = client.post(
        "/auth/login",
        data={
            "email": "admin-backup@test.local",
            "password": "Test123!",
        },
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}


def test_backup_policy_defaults_to_enabled_with_persistent_dir(app):
    with app.app_context():
        policy = automatic_backup_policy()
        assert policy["enabled"] is True
        assert policy["time"] == "02:00"
        assert policy["retention_days"] == 14
        assert policy["max_files"] == 30
        assert policy["persistent_storage"] is True


def test_backup_policy_can_be_updated_from_admin_ui(client, app):
    _login(client)

    response = client.post(
        "/settings/backups/policy",
        data={
            "enabled": "on",
            "backup_time": "03:30",
            "retention_days": "21",
            "max_files": "40",
        },
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}

    with app.app_context():
        values = {
            row.key: row.value
            for row in SystemSetting.query.filter(
                SystemSetting.key.in_(
                    [
                        "backup_auto_enabled",
                        "backup_auto_time",
                        "backup_retention_days",
                        "backup_max_files",
                    ]
                )
            ).all()
        }
        assert values["backup_auto_enabled"] == "1"
        assert values["backup_auto_time"] == "03:30"
        assert values["backup_retention_days"] == "21"
        assert values["backup_max_files"] == "40"


def test_daily_due_logic_runs_once_after_scheduled_time(app):
    with app.app_context():
        policy = {
            "enabled": True,
            "persistent_storage": True,
            "time": "02:00",
        }

        now = datetime(2026, 10, 5, 2, 15, tzinfo=timezone.utc)
        previous_day = datetime(
            2026,
            10,
            4,
            2,
            5,
            tzinfo=timezone.utc,
        )
        same_day = datetime(
            2026,
            10,
            5,
            2,
            5,
            tzinfo=timezone.utc,
        )

        assert _is_due(
            now,
            policy,
            last_success_utc=previous_day,
            last_attempt_utc=None,
        ) is True

        assert _is_due(
            now,
            policy,
            last_success_utc=same_day,
            last_attempt_utc=None,
        ) is False

        recent_failure = now - timedelta(minutes=20)
        assert _is_due(
            now,
            policy,
            last_success_utc=previous_day,
            last_attempt_utc=recent_failure,
        ) is False


def test_forced_backup_records_success_and_file(app):
    with app.app_context():
        result = run_backup_cycle(
            force=True,
            source="manual_controlled",
        )
        assert result["status"] == "success"
        assert result["path"].is_file()

        status = automatic_backup_status()
        assert status["last_file"] == result["file_name"]
        assert status["last_success_utc"] is not None
        assert status["last_error"] in (None, "")


def test_backup_admin_page_loads(client):
    _login(client)
    response = client.get("/settings/backups/")
    assert response.status_code == 200
    assert b"Backups autom" in response.data
    assert b"02:00" in response.data
