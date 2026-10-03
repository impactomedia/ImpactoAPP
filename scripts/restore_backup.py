"""Valida o restaura un respaldo lógico de Impacto Nexora.

Validación:
    python -m scripts.restore_backup /ruta/impacto_nexora_....zip

Restauración de prueba MySQL:
    RESTORE_DATABASE_URL=mysql+pymysql://... \
      python -m scripts.restore_backup /ruta/impacto_nexora_....zip

El script se niega a restaurar cuando RESTORE_DATABASE_URL apunta al mismo
host/puerto/base/usuario que DATABASE_URL.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import unquote, urlparse

import pymysql


def _canonical_database_identity(url):
    if not url:
        return None
    if url.startswith("sqlite:///"):
        return ("sqlite", str(Path(url.replace("sqlite:///", "", 1)).resolve()))

    normalized = url.replace("mysql+pymysql://", "mysql://", 1)
    parsed = urlparse(normalized)
    return (
        "mysql",
        (parsed.hostname or "localhost").lower(),
        parsed.port or 3306,
        unquote(parsed.username or "root"),
        unquote(parsed.path.lstrip("/")),
    )


def _mysql_parts(url):
    normalized = url.replace("mysql+pymysql://", "mysql://", 1)
    parsed = urlparse(normalized)
    if parsed.scheme != "mysql":
        raise ValueError("RESTORE_DATABASE_URL debe ser MySQL o SQLite.")
    return {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 3306,
        "user": unquote(parsed.username or "root"),
        "password": unquote(parsed.password or ""),
        "database": unquote(parsed.path.lstrip("/")),
    }


def _safe_read(archive, name):
    if name not in archive.namelist():
        raise ValueError(f"El respaldo no contiene {name}.")
    return archive.read(name)


def validate_backup(path):
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)

    with zipfile.ZipFile(path, "r") as archive:
        bad = archive.testzip()
        if bad:
            raise ValueError(f"ZIP corrupto: {bad}")

        manifest = json.loads(_safe_read(archive, "manifest.json").decode("utf-8"))
        db_file = manifest.get("database_file")
        if not db_file:
            raise ValueError("manifest.json no indica database_file.")
        _safe_read(archive, db_file)

    return manifest


def _verify_mysql(parts):
    connection = pymysql.connect(
        host=parts["host"],
        port=parts["port"],
        user=parts["user"],
        password=parts["password"],
        database=parts["database"],
        connect_timeout=15,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute("SHOW TABLES")
            tables = cursor.fetchall()
        if not tables:
            raise RuntimeError("La base restaurada no contiene tablas.")
        return len(tables)
    finally:
        connection.close()


def restore_backup(path, restore_url):
    production_url = os.getenv("DATABASE_URL")
    if _canonical_database_identity(restore_url) == _canonical_database_identity(production_url):
        raise RuntimeError(
            "RESTORE_DATABASE_URL coincide con DATABASE_URL. "
            "La restauración de prueba sobre producción está bloqueada."
        )

    manifest = validate_backup(path)
    engine = manifest["database_engine"]
    db_file = manifest["database_file"]

    with zipfile.ZipFile(path, "r") as archive:
        with tempfile.TemporaryDirectory(prefix="nexora_restore_") as temp_name:
            temp_dir = Path(temp_name)
            source = temp_dir / Path(db_file).name
            source.write_bytes(_safe_read(archive, db_file))

            if engine == "sqlite":
                if not restore_url.startswith("sqlite:///"):
                    raise RuntimeError("El backup SQLite requiere RESTORE_DATABASE_URL SQLite.")
                target = Path(restore_url.replace("sqlite:///", "", 1)).resolve()
                if production_url and _canonical_database_identity(restore_url) == _canonical_database_identity(production_url):
                    raise RuntimeError("No se puede sobrescribir la base productiva.")
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                connection = sqlite3.connect(target)
                try:
                    result = connection.execute("PRAGMA integrity_check").fetchone()[0]
                    if result != "ok":
                        raise RuntimeError(f"SQLite integrity_check: {result}")
                finally:
                    connection.close()
                return {"engine": "sqlite", "verified": True}

            if engine != "mysql":
                raise RuntimeError(f"Motor de backup no soportado: {engine}")

            parts = _mysql_parts(restore_url)
            exe = shutil.which("mysql") or shutil.which("mariadb")
            if not exe:
                raise RuntimeError(
                    "No se encontró mysql/mariadb. La imagen debe incluir default-mysql-client."
                )

            env = os.environ.copy()
            if parts["password"]:
                env["MYSQL_PWD"] = parts["password"]

            args = [
                exe,
                "-h",
                parts["host"],
                "-P",
                str(parts["port"]),
                "-u",
                parts["user"],
                parts["database"],
            ]
            with source.open("rb") as handle:
                subprocess.run(
                    args,
                    stdin=handle,
                    stderr=subprocess.PIPE,
                    check=True,
                    env=env,
                )

            table_count = _verify_mysql(parts)
            return {
                "engine": "mysql",
                "verified": True,
                "table_count": table_count,
            }


def main():
    if len(sys.argv) != 2:
        raise SystemExit(
            "Uso: python -m scripts.restore_backup /ruta/impacto_nexora_YYYYMMDD_HHMMSS_UTC.zip"
        )

    path = Path(sys.argv[1])
    manifest = validate_backup(path)
    print("Backup válido:", json.dumps(manifest, ensure_ascii=False))

    restore_url = os.getenv("RESTORE_DATABASE_URL")
    if not restore_url:
        print(
            "Validación estructural completada. "
            "Define RESTORE_DATABASE_URL para ejecutar una restauración de prueba."
        )
        return

    result = restore_backup(path, restore_url)
    print("Restauración de prueba verificada:", result)


if __name__ == "__main__":
    main()
