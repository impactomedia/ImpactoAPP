"""Regresión de marcaciones en Nicaragua (UTC−6) y días locales."""
from datetime import date, datetime
from pathlib import Path

import pytest

from app import create_app
from app.attendance_time import local_form_to_utc, to_local, utc_range_for_local_day
from app.blueprints import hr as hr_module
from app.extensions import db
from app.models import AttendanceMark, Collaborator, Role, User
from scripts.seed import seed_all


class TestConfig:
    TESTING = True
    SECRET_KEY = "test-attendance-managua"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    UPLOAD_FOLDER = str(Path(__file__).parent / "uploads_attendance_timezone")
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
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        seed_all()
        person = User(
            name="Asesora de prueba", email="attendance-advisor@test.local",
            role=Role.query.filter_by(name="advisor").one(), active=True,
        )
        person.set_password("SafePassword123!")
        db.session.add(person)
        db.session.flush()
        collaborator = Collaborator(
            user_id=person.id, code="AT-MANAGUA-01", status="activo",
            department="Ventas", job_title="Asesora",
        )
        db.session.add(collaborator)
        db.session.commit()
        person_id = collaborator.id
        yield app, person_id
        db.session.remove()


def login(browser, email, password):
    response = browser.post("/auth/login", data={"email": email, "password": password})
    assert response.status_code in (302, 303)


def test_local_day_boundaries_and_conversions_are_utc_minus_six():
    first, next_day = utc_range_for_local_day(date(2026, 11, 12))
    assert first == datetime(2026, 11, 12, 6, 0)
    assert next_day == datetime(2026, 11, 13, 6, 0)
    # UTC at 02:35 on the 13th is still the 12th at 20:35 in Managua.
    stored = datetime(2026, 11, 13, 2, 35, 0)
    shown = to_local(stored)
    assert shown.strftime("%Y-%m-%d %H:%M:%S") == "2026-11-12 20:35:00"
    assert shown.utcoffset().total_seconds() == -6 * 3600
    assert local_form_to_utc(datetime(2026, 11, 12, 20, 35)) == stored


def test_attendance_admin_uses_local_day_and_local_edit_form(prepared):
    app, staff_id = prepared
    with app.app_context():
        # Both UTC timestamps fall on different local calendar days.
        previous = AttendanceMark(
            collaborator_id=staff_id, mark_type="entrada",
            marked_at=datetime(2026, 11, 12, 5, 59),
        )
        in_local_day = AttendanceMark(
            collaborator_id=staff_id, mark_type="salida",
            marked_at=datetime(2026, 11, 13, 2, 35),
        )
        db.session.add_all([previous, in_local_day])
        db.session.commit()
        chosen_id = in_local_day.id

    browser = app.test_client()
    login(browser, "admin@impactomedia.local", "ChangeMe123!")
    response = browser.get("/hr/attendance?date=2026-11-12")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "20:35:00" in body
    assert 'value="2026-11-12T20:35"' in body
    assert "05:59:00" not in body
    assert "UTC−6" in body

    edit = browser.post(
        f"/hr/attendance/{chosen_id}/correct",
        data={
            "marked_at": "2026-11-12T18:45",
            "mark_type": "salida",
            "reason": "Corrección autorizada",
        },
    )
    assert edit.status_code == 302
    assert "date=2026-11-12" in edit.headers["Location"]
    with app.app_context():
        saved = db.session.get(AttendanceMark, chosen_id)
        assert saved.marked_at == datetime(2026, 11, 13, 0, 45)
        assert saved.corrected is True


def test_advisor_marks_with_utc_in_database_but_uses_local_jornada(prepared, monkeypatch):
    app, staff_id = prepared
    monkeypatch.setattr(hr_module, "today_local", lambda: date(2026, 11, 12))
    with app.app_context():
        mark = AttendanceMark(
            collaborator_id=staff_id, mark_type="entrada",
            marked_at=datetime(2026, 11, 13, 2, 15),
        )
        db.session.add(mark)
        db.session.commit()

    browser = app.test_client()
    login(browser, "attendance-advisor@test.local", "SafePassword123!")
    page = browser.get("/hr/my-day")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "20:15:00" in html
    assert "Inicio de jornada" not in html
    assert "UTC−6" in html

    # La hora del servidor 03:00 UTC sigue siendo las 21:00 del día 12.
    monkeypatch.setattr(hr_module, "utc_now", lambda: datetime(2026, 11, 13, 3, 0))
    response = browser.post("/hr/my-day/mark", data={"mark_type": "salida"})
    assert response.status_code == 302
    with app.app_context():
        marks = AttendanceMark.query.filter_by(collaborator_id=staff_id).order_by(
            AttendanceMark.marked_at
        ).all()
        assert len(marks) == 2
        assert [m.mark_type for m in marks] == ["entrada", "salida"]
        assert marks[1].marked_at == datetime(2026, 11, 13, 3, 0)


def test_after_midnight_local_new_jornada_is_not_blocked(prepared, monkeypatch):
    app, staff_id = prepared
    with app.app_context():
        db.session.add(AttendanceMark(
            collaborator_id=staff_id, mark_type="salida",
            marked_at=datetime(2026, 11, 13, 5, 45),
        ))
        db.session.commit()

    # 06:15 UTC is 00:15 in Managua on the next local day.
    monkeypatch.setattr(hr_module, "utc_now", lambda: datetime(2026, 11, 13, 6, 15))
    browser = app.test_client()
    login(browser, "attendance-advisor@test.local", "SafePassword123!")
    response = browser.post("/hr/my-day/mark", data={"mark_type": "entrada"})
    assert response.status_code == 302
    with app.app_context():
        marks = AttendanceMark.query.filter_by(collaborator_id=staff_id).order_by(
            AttendanceMark.marked_at
        ).all()
        assert [m.mark_type for m in marks] == ["salida", "entrada"]
        assert marks[-1].marked_at == datetime(2026, 11, 13, 6, 15)
