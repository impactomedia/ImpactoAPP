"""Respaldo lógico de Impacto Nexora.

Crea un ZIP con:
- database.sql (MySQL) o database.sqlite3 (SQLite)
- uploads/ (si existe)
- manifest.json

En Railway, para persistencia configure un Volume y BACKUP_DIR/UPLOAD_FOLDER
dentro de ese volumen. El comando termina al finalizar y puede usarse en un
servicio Cron separado si se desea conservar copias lógicas adicionales.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse


ROOT = Path(__file__).resolve().parents[1]


def backup_directory():
    configured = os.getenv("BACKUP_DIR")
    if configured:
        path = Path(configured)
    else:
        mount = os.getenv("RAILWAY_VOLUME_MOUNT_PATH")
        path = Path(mount) / "backups" if mount else ROOT / "backups"
    path.mkdir(parents=True, exist_ok=True)
    return path


def upload_directory():
    configured = os.getenv("UPLOAD_FOLDER")
    if configured:
        return Path(configured)
    mount = os.getenv("RAILWAY_VOLUME_MOUNT_PATH")
    return Path(mount) / "uploads" if mount else ROOT / "app" / "static" / "uploads"


def _database_url():
    return os.getenv("DATABASE_URL", f"sqlite:///{ROOT / 'impacto_manager.db'}")


def _mysql_parts(url):
    normalized = url.replace("mysql+pymysql://", "mysql://", 1)
    parsed = urlparse(normalized)
    if parsed.scheme != "mysql":
        raise ValueError("La URL de base de datos no corresponde a MySQL.")
    return {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 3306,
        "user": unquote(parsed.username or "root"),
        "password": unquote(parsed.password or ""),
        "database": unquote(parsed.path.lstrip("/")),
    }


def _find_mysql_dump():
    return shutil.which("mysqldump") or shutil.which("mariadb-dump")


def _dump_database(target):
    url = _database_url()

    if url.startswith("sqlite:///"):
        source = Path(url.replace("sqlite:///", "", 1))
        if not source.exists():
            raise FileNotFoundError(f"No existe la base SQLite: {source}")
        sqlite_target = target.with_suffix(".sqlite3")
        shutil.copy2(source, sqlite_target)
        return sqlite_target, "sqlite"

    parts = _mysql_parts(url)
    exe = _find_mysql_dump()
    if not exe:
        raise RuntimeError(
            "No se encontró mysqldump/mariadb-dump. La imagen debe incluir default-mysql-client."
        )

    sql_target = target.with_suffix(".sql")
    env = os.environ.copy()
    if parts["password"]:
        env["MYSQL_PWD"] = parts["password"]

    args = [
        exe,
        "--single-transaction",
        "--quick",
        "--routines",
        "--triggers",
        "--default-character-set=utf8mb4",
        "-h",
        parts["host"],
        "-P",
        str(parts["port"]),
        "-u",
        parts["user"],
        parts["database"],
    ]

    with sql_target.open("wb") as handle:
        subprocess.run(
            args,
            stdout=handle,
            stderr=subprocess.PIPE,
            check=True,
            env=env,
        )

    if sql_target.stat().st_size == 0:
        raise RuntimeError("El dump MySQL quedó vacío.")

    return sql_target, "mysql"


def _cleanup_retention(directory):
    days = max(1, int(os.getenv("BACKUP_RETENTION_DAYS", "14")))
    max_files = max(1, int(os.getenv("BACKUP_MAX_FILES", "30")))
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    files = sorted(
        directory.glob("impacto_nexora_*.zip"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )

    for index, path in enumerate(files):
        modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        if index >= max_files or modified < cutoff:
            try:
                path.unlink()
            except FileNotFoundError:
                pass


def create_backup():
    directory = backup_directory()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_UTC")
    final_path = directory / f"impacto_nexora_{stamp}.zip"

    with tempfile.TemporaryDirectory(prefix="nexora_backup_") as temp_name:
        temp_dir = Path(temp_name)
        database_file, engine = _dump_database(temp_dir / "database")

        uploads = upload_directory()
        upload_files = []
        if uploads.exists():
            upload_files = [
                path
                for path in uploads.rglob("*")
                if path.is_file()
            ]

        manifest = {
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "database_engine": engine,
            "database_file": database_file.name,
            "upload_file_count": len(upload_files),
            "upload_root": str(uploads),
            "contains_secrets": False,
            "restore_note": (
                "Use scripts/restore_backup.py contra RESTORE_DATABASE_URL. "
                "Nunca pruebe una restauración sobre DATABASE_URL de producción."
            ),
        }

        with zipfile.ZipFile(final_path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.write(database_file, arcname=database_file.name)
            archive.writestr(
                "manifest.json",
                json.dumps(manifest, ensure_ascii=False, indent=2),
            )

            for source in upload_files:
                relative = source.relative_to(uploads)
                archive.write(source, arcname=str(Path("uploads") / relative))

    with zipfile.ZipFile(final_path, "r") as archive:
        bad = archive.testzip()
        if bad:
            final_path.unlink(missing_ok=True)
            raise RuntimeError(f"El ZIP generado está corrupto: {bad}")

    _cleanup_retention(directory)
    return final_path


def main():
    path = create_backup()
    print(path)


if __name__ == "__main__":
    main()
