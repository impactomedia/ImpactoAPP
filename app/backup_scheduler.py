from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from flask import current_app
from sqlalchemy import text

from app.catalog_runtime import save_system_setting, system_setting
from app.extensions import db
from app.helpers import audit


LOCK_NAME = "impacto_nexora_automatic_backup_v191"
_LOCAL_LOCK = threading.Lock()
_THREAD_STARTED = False

KEY_ENABLED = "backup_auto_enabled"
KEY_TIME = "backup_auto_time"
KEY_RETENTION_DAYS = "backup_retention_days"
KEY_MAX_FILES = "backup_max_files"
KEY_LAST_ATTEMPT_UTC = "backup_last_attempt_utc"
KEY_LAST_SUCCESS_UTC = "backup_last_success_utc"
KEY_LAST_FILE = "backup_last_file"
KEY_LAST_ERROR = "backup_last_error"

DEFAULT_TIME = "02:00"
DEFAULT_RETENTION_DAYS = 14
DEFAULT_MAX_FILES = 30


def _bool_setting(value, default=False):
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "si", "sí"}


def _bounded_int(value, default, minimum, maximum):
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = int(default)
    return max(minimum, min(maximum, number))


def _valid_time(value):
    raw = str(value or "").strip()
    try:
        parsed = datetime.strptime(raw, "%H:%M")
    except ValueError:
        return DEFAULT_TIME
    return parsed.strftime("%H:%M")


def _parse_iso_utc(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _latest_backup_info():
    try:
        from scripts.backup import backup_directory

        files = [
            path
            for path in backup_directory().glob("impacto_nexora_*.zip")
            if path.is_file()
        ]
        if not files:
            return None, None
        latest = max(files, key=lambda path: path.stat().st_mtime)
        modified = datetime.fromtimestamp(
            latest.stat().st_mtime,
            tz=timezone.utc,
        )
        return modified, latest.name
    except Exception:
        return None, None


def _effective_last_success():
    stored = _parse_iso_utc(system_setting(KEY_LAST_SUCCESS_UTC))
    latest_file_time, latest_file_name = _latest_backup_info()
    if latest_file_time and (not stored or latest_file_time > stored):
        return latest_file_time, latest_file_name
    return stored, system_setting(KEY_LAST_FILE)


def _company_zone():
    name = current_app.config.get("COMPANY_TIMEZONE", "America/Managua")
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("UTC")


def automatic_backup_policy():
    volume_available = bool(
        os.getenv("RAILWAY_VOLUME_MOUNT_PATH")
        or os.getenv("BACKUP_DIR")
    )
    default_enabled = volume_available

    return {
        "enabled": _bool_setting(
            system_setting(KEY_ENABLED, "1" if default_enabled else "0"),
            default_enabled,
        ),
        "time": _valid_time(system_setting(KEY_TIME, DEFAULT_TIME)),
        "retention_days": _bounded_int(
            system_setting(KEY_RETENTION_DAYS, DEFAULT_RETENTION_DAYS),
            DEFAULT_RETENTION_DAYS,
            1,
            365,
        ),
        "max_files": _bounded_int(
            system_setting(KEY_MAX_FILES, DEFAULT_MAX_FILES),
            DEFAULT_MAX_FILES,
            1,
            200,
        ),
        "timezone": current_app.config.get(
            "COMPANY_TIMEZONE",
            "America/Managua",
        ),
        "persistent_storage": volume_available,
        "volume_mount": os.getenv("RAILWAY_VOLUME_MOUNT_PATH"),
        "thread_enabled": (
            not current_app.config.get("TESTING")
            and os.getenv("NEXORA_DISABLE_AUTO_BACKUP_THREAD", "0") != "1"
        ),
    }


def save_automatic_backup_policy(*, enabled, backup_time, retention_days, max_files):
    backup_time = _valid_time(backup_time)
    retention_days = _bounded_int(retention_days, DEFAULT_RETENTION_DAYS, 1, 365)
    max_files = _bounded_int(max_files, DEFAULT_MAX_FILES, 1, 200)

    save_system_setting(
        KEY_ENABLED,
        "1" if enabled else "0",
        "Activa el respaldo lógico automático diario de Nexora.",
    )
    save_system_setting(
        KEY_TIME,
        backup_time,
        "Hora local diaria del respaldo automático.",
    )
    save_system_setting(
        KEY_RETENTION_DAYS,
        retention_days,
        "Días de retención para respaldos lógicos de Nexora.",
    )
    save_system_setting(
        KEY_MAX_FILES,
        max_files,
        "Cantidad máxima de respaldos lógicos conservados.",
    )

    return {
        "enabled": bool(enabled),
        "time": backup_time,
        "retention_days": retention_days,
        "max_files": max_files,
    }


def _scheduled_for(day, policy):
    hour, minute = [int(part) for part in policy["time"].split(":", 1)]
    zone = _company_zone()
    return datetime(
        day.year,
        day.month,
        day.day,
        hour,
        minute,
        tzinfo=zone,
    )


def _is_due(now_utc, policy, last_success_utc=None, last_attempt_utc=None):
    if not policy["enabled"] or not policy["persistent_storage"]:
        return False

    zone = _company_zone()
    now_local = now_utc.astimezone(zone)
    scheduled_today = _scheduled_for(now_local.date(), policy)

    if now_local < scheduled_today:
        return False

    if last_success_utc:
        last_success_local = last_success_utc.astimezone(zone)
        if (
            last_success_local.date() == now_local.date()
            and last_success_local >= scheduled_today
        ):
            return False

    # Si hubo un fallo reciente, esperar una hora antes de reintentar.
    if last_attempt_utc and now_utc - last_attempt_utc < timedelta(hours=1):
        return False

    return True


def automatic_backup_status():
    policy = automatic_backup_policy()
    now_utc = datetime.now(timezone.utc)
    zone = _company_zone()
    now_local = now_utc.astimezone(zone)

    last_attempt = _parse_iso_utc(system_setting(KEY_LAST_ATTEMPT_UTC))
    last_success, last_file = _effective_last_success()
    last_error = system_setting(KEY_LAST_ERROR)

    scheduled_today = _scheduled_for(now_local.date(), policy)
    due_now = _is_due(now_utc, policy, last_success, last_attempt)

    if not policy["enabled"]:
        next_run = None
    elif due_now:
        next_run = now_local
    elif now_local < scheduled_today:
        next_run = scheduled_today
    else:
        next_run = _scheduled_for(
            now_local.date() + timedelta(days=1),
            policy,
        )

    return {
        **policy,
        "due_now": due_now,
        "last_attempt_utc": last_attempt,
        "last_success_utc": last_success,
        "last_success_local": (
            last_success.astimezone(zone) if last_success else None
        ),
        "last_file": last_file,
        "last_error": last_error,
        "next_run_local": next_run,
    }


@contextmanager
def _scheduler_lock():
    dialect = db.engine.dialect.name

    if dialect in {"mysql", "mariadb"}:
        connection = db.engine.connect()
        acquired = False
        try:
            acquired = bool(
                connection.execute(
                    text("SELECT GET_LOCK(:lock_name, 0)"),
                    {"lock_name": LOCK_NAME},
                ).scalar()
            )
            yield acquired
        finally:
            if acquired:
                try:
                    connection.execute(
                        text("SELECT RELEASE_LOCK(:lock_name)"),
                        {"lock_name": LOCK_NAME},
                    )
                except Exception:
                    current_app.logger.exception(
                        "No se pudo liberar el lock de backup."
                    )
            connection.close()
        return

    acquired = _LOCAL_LOCK.acquire(blocking=False)
    try:
        yield acquired
    finally:
        if acquired:
            _LOCAL_LOCK.release()


def run_backup_cycle(*, force=False, source="automatic"):
    with _scheduler_lock() as acquired:
        if not acquired:
            return {
                "status": "busy",
                "message": "Ya hay otro proceso de respaldo en curso.",
            }

        db.session.expire_all()
        policy = automatic_backup_policy()
        now_utc = datetime.now(timezone.utc)
        last_success, _last_file = _effective_last_success()
        last_attempt = _parse_iso_utc(system_setting(KEY_LAST_ATTEMPT_UTC))

        if not force and not _is_due(
            now_utc,
            policy,
            last_success,
            last_attempt,
        ):
            return {"status": "not_due"}

        if not policy["persistent_storage"]:
            message = (
                "No hay almacenamiento persistente configurado para "
                "respaldos automáticos."
            )
            save_system_setting(KEY_LAST_ATTEMPT_UTC, now_utc.isoformat())
            save_system_setting(KEY_LAST_ERROR, message)
            db.session.commit()
            return {"status": "no_storage", "message": message}

        save_system_setting(KEY_LAST_ATTEMPT_UTC, now_utc.isoformat())
        db.session.commit()

        try:
            from scripts.backup import BackupBusyError, create_backup

            try:
                path = create_backup(
                    retention_days=policy["retention_days"],
                    max_files=policy["max_files"],
                    source=source,
                )
            except BackupBusyError:
                return {
                    "status": "busy",
                    "message": "Ya hay otro respaldo de Nexora en ejecución.",
                }

            completed_utc = datetime.now(timezone.utc)
            save_system_setting(
                KEY_LAST_SUCCESS_UTC,
                completed_utc.isoformat(),
            )
            save_system_setting(KEY_LAST_FILE, path.name)
            save_system_setting(KEY_LAST_ERROR, "")

            audit(
                "crear_backup_automatico"
                if source == "automatic"
                else "crear_backup_programado_manual",
                "Backup",
                after={
                    "file_name": path.name,
                    "source": source,
                    "retention_days": policy["retention_days"],
                    "max_files": policy["max_files"],
                },
            )
            db.session.commit()

            return {
                "status": "success",
                "path": path,
                "file_name": path.name,
            }
        except Exception as exc:
            db.session.rollback()
            message = str(exc)[:500]
            save_system_setting(
                KEY_LAST_ATTEMPT_UTC,
                datetime.now(timezone.utc).isoformat(),
            )
            save_system_setting(KEY_LAST_ERROR, message)
            audit(
                "fallo_backup_automatico",
                "Backup",
                after={"source": source},
                reason=message,
            )
            db.session.commit()
            current_app.logger.exception(
                "Falló el respaldo %s de Nexora.",
                source,
            )
            return {
                "status": "error",
                "message": message,
            }


def _scheduler_worker(app):
    initial_delay = _bounded_int(
        os.getenv("AUTO_BACKUP_INITIAL_DELAY_SECONDS", "75"),
        75,
        5,
        3600,
    )
    interval = _bounded_int(
        os.getenv("AUTO_BACKUP_CHECK_SECONDS", "300"),
        300,
        60,
        3600,
    )

    time.sleep(initial_delay)

    while True:
        try:
            with app.app_context():
                run_backup_cycle(force=False, source="automatic")
                db.session.remove()
        except Exception:
            app.logger.exception(
                "Error no controlado en scheduler de respaldos."
            )
            try:
                with app.app_context():
                    db.session.remove()
            except Exception:
                pass
        time.sleep(interval)


def init_backup_scheduler(app):
    global _THREAD_STARTED

    if app.config.get("TESTING"):
        return
    if os.getenv("NEXORA_DISABLE_AUTO_BACKUP_THREAD", "0") == "1":
        return
    if _THREAD_STARTED:
        return

    _THREAD_STARTED = True
    thread = threading.Thread(
        target=_scheduler_worker,
        args=(app,),
        name="nexora-auto-backup",
        daemon=True,
    )
    thread.start()
