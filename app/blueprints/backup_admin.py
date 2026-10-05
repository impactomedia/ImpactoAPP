from __future__ import annotations

from datetime import datetime
from pathlib import Path

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import login_required

from app.backup_scheduler import (
    automatic_backup_status,
    run_backup_cycle,
    save_automatic_backup_policy,
)
from app.decorators import roles_required
from app.extensions import db
from app.helpers import audit
from scripts.backup import backup_directory


bp = Blueprint(
    "backup_admin",
    __name__,
    url_prefix="/settings/backups",
)


def _backup_files():
    directory = backup_directory()
    if not directory.exists():
        return []

    paths = sorted(
        (
            path
            for path in directory.glob("impacto_nexora_*.zip")
            if path.is_file()
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )[:20]

    return [
        {
            "name": path.name,
            "size_mb": round(
                path.stat().st_size / 1024 / 1024,
                2,
            ),
            "modified": datetime.fromtimestamp(
                path.stat().st_mtime
            ),
        }
        for path in paths
    ]


@bp.route("/")
@login_required
@roles_required("superadmin", "admin", "manager")
def index():
    return render_template(
        "settings/backups.html",
        status=automatic_backup_status(),
        backups=_backup_files(),
    )


@bp.route("/policy", methods=["POST"])
@login_required
@roles_required("superadmin", "admin", "manager")
def update_policy():
    enabled = bool(request.form.get("enabled"))
    backup_time = (
        request.form.get("backup_time")
        or "02:00"
    ).strip()

    try:
        retention_days = int(
            request.form.get("retention_days", "14")
        )
        max_files = int(
            request.form.get("max_files", "30")
        )
    except ValueError:
        flash(
            "Retención y cantidad máxima deben ser números enteros.",
            "danger",
        )
        return redirect(url_for("backup_admin.index"))

    before = automatic_backup_status()
    saved = save_automatic_backup_policy(
        enabled=enabled,
        backup_time=backup_time,
        retention_days=retention_days,
        max_files=max_files,
    )

    audit(
        "actualizar_politica_backup",
        "SystemSetting",
        after={
            "enabled": saved["enabled"],
            "time": saved["time"],
            "retention_days": saved["retention_days"],
            "max_files": saved["max_files"],
        },
        before={
            "enabled": before["enabled"],
            "time": before["time"],
            "retention_days": before["retention_days"],
            "max_files": before["max_files"],
        },
    )
    db.session.commit()

    flash(
        "Política de respaldo automático actualizada.",
        "success",
    )
    return redirect(url_for("backup_admin.index"))


@bp.route("/run-now", methods=["POST"])
@login_required
@roles_required("superadmin", "admin", "manager")
def run_now():
    result = run_backup_cycle(
        force=True,
        source="manual_controlled",
    )

    if result["status"] == "success":
        flash(
            f"Respaldo creado: {result['file_name']}",
            "success",
        )
    elif result["status"] == "busy":
        flash(
            "Ya hay otro respaldo en ejecución. Intenta nuevamente en unos minutos.",
            "warning",
        )
    elif result["status"] == "no_storage":
        flash(
            result.get(
                "message",
                "No hay almacenamiento persistente disponible.",
            ),
            "danger",
        )
    else:
        flash(
            "No se pudo completar el respaldo. "
            + result.get("message", "Revisa el registro de errores."),
            "danger",
        )

    return redirect(url_for("backup_admin.index"))
