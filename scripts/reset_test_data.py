"""Limpieza controlada de datos de prueba de Impacto Nexora."""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass

from sqlalchemy import and_, func, or_, select, text

from app import create_app
from app.extensions import db
import app.client_v2_models  # noqa: F401,E402
import app.nexora_models  # noqa: F401,E402
from scripts.backup import create_backup  # noqa: E402

CONFIRM_TOKEN = "BORRAR-DATOS-DE-PRUEBA"

PRESERVED_TABLES = {
    "roles", "permissions", "role_permissions", "advisor_projects",
    "work_schedules", "product_services", "commission_rules",
    "task_templates", "task_template_items", "catalog_items",
    "system_settings", "holidays",
}
PARTIAL_TABLES = {"users", "collaborators"}


@dataclass(frozen=True)
class KeepIdentity:
    user_id: int
    email: str
    name: str
    collaborator_id: int | None


def _operational_tables():
    return [
        table
        for table in reversed(db.metadata.sorted_tables)
        if table.name not in PRESERVED_TABLES
        and table.name not in PARTIAL_TABLES
    ]


def _resolve_keep_identity(connection, keep_email=None):
    users = db.metadata.tables["users"]
    roles = db.metadata.tables["roles"]
    collaborators = db.metadata.tables["collaborators"]
    requested = (keep_email or "").strip().lower() or None

    query = (
        select(
            users.c.id.label("user_id"),
            users.c.email,
            users.c.name,
            roles.c.name.label("role_name"),
            users.c.active,
        )
        .select_from(users.join(roles, users.c.role_id == roles.c.id))
    )

    if requested:
        row = connection.execute(
            query.where(func.lower(users.c.email) == requested)
        ).mappings().first()
        if not row:
            raise RuntimeError(f"No existe el usuario a conservar: {requested}")
        if row["role_name"] != "superadmin" or not row["active"]:
            raise RuntimeError(
                "El usuario a conservar debe ser un superadmin activo."
            )
    else:
        rows = connection.execute(
            query.where(
                roles.c.name == "superadmin",
                users.c.active.is_(True),
            ).order_by(users.c.id)
        ).mappings().all()
        if len(rows) != 1:
            candidates = ", ".join(r["email"] for r in rows) or "ninguno"
            raise RuntimeError(
                "No existe un único superadmin activo. "
                f"Encontrados: {candidates}. Usa --keep-email CORREO."
            )
        row = rows[0]

    collaborator_id = connection.execute(
        select(collaborators.c.id).where(
            collaborators.c.user_id == row["user_id"]
        )
    ).scalar_one_or_none()

    return KeepIdentity(
        int(row["user_id"]),
        str(row["email"]),
        str(row["name"]),
        int(collaborator_id) if collaborator_id is not None else None,
    )


def _count(connection, table):
    return int(
        connection.execute(select(func.count()).select_from(table)).scalar_one()
    )


def _preview(connection, keep):
    users = db.metadata.tables["users"]
    collaborators = db.metadata.tables["collaborators"]

    users_to_delete = int(
        connection.execute(
            select(func.count()).select_from(users).where(users.c.id != keep.user_id)
        ).scalar_one()
    )

    if keep.collaborator_id is None:
        collaborators_to_delete = _count(connection, collaborators)
    else:
        collaborators_to_delete = int(
            connection.execute(
                select(func.count())
                .select_from(collaborators)
                .where(collaborators.c.id != keep.collaborator_id)
            ).scalar_one()
        )

    nonempty = []
    for table in _operational_tables():
        count = _count(connection, table)
        if count:
            nonempty.append((table.name, count))

    clients = db.metadata.tables.get("clients")
    sales = db.metadata.tables.get("sales")
    return {
        "users": users_to_delete,
        "collaborators": collaborators_to_delete,
        "clients": _count(connection, clients) if clients is not None else 0,
        "sales": _count(connection, sales) if sales is not None else 0,
        "nonempty": nonempty,
    }


def _print_preview(keep, preview):
    print("\n=== IMPACTO NEXORA · PREVISUALIZACIÓN DE LIMPIEZA ===")
    print(f"Superadmin conservado: {keep.name} <{keep.email}> (ID {keep.user_id})")
    print(f"Usuarios de prueba a borrar: {preview['users']}")
    print(f"Colaboradores de prueba a borrar: {preview['collaborators']}")
    print(f"Clientes a borrar: {preview['clients']}")
    print(f"Ventas a borrar: {preview['sales']}")
    print("\nTablas operativas con datos:")
    for name, count in preview["nonempty"]:
        print(f"  - {name}: {count}")
    if not preview["nonempty"]:
        print("  - Ninguna")
    print("\nSe conservan configuración, roles, permisos, productos y catálogos.\n")


def _clean_transient_settings(connection, keep):
    settings = db.metadata.tables["system_settings"]
    keep_2fa_key = f"security_2fa_user:{keep.user_id}"
    connection.execute(
        settings.delete().where(
            or_(
                settings.c.key.like("temporary_scope:%"),
                and_(
                    settings.c.key.like("security_2fa_user:%"),
                    settings.c.key != keep_2fa_key,
                ),
            )
        )
    )


def _execute_cleanup(connection, keep):
    users = db.metadata.tables["users"]
    collaborators = db.metadata.tables["collaborators"]
    dialect = connection.dialect.name
    mysql = dialect in {"mysql", "mariadb"}

    if mysql:
        connection.execute(text("SET FOREIGN_KEY_CHECKS=0"))
    elif dialect == "sqlite":
        connection.execute(text("PRAGMA foreign_keys=OFF"))

    try:
        for table in _operational_tables():
            connection.execute(table.delete())

        _clean_transient_settings(connection, keep)

        if keep.collaborator_id is not None:
            connection.execute(
                collaborators.update()
                .where(collaborators.c.id == keep.collaborator_id)
                .values(supervisor_id=None)
            )
            connection.execute(
                collaborators.delete().where(
                    collaborators.c.id != keep.collaborator_id
                )
            )
        else:
            connection.execute(collaborators.delete())

        connection.execute(users.delete().where(users.c.id != keep.user_id))
    finally:
        if mysql:
            connection.execute(text("SET FOREIGN_KEY_CHECKS=1"))
        elif dialect == "sqlite":
            connection.execute(text("PRAGMA foreign_keys=ON"))


def _verify(connection, keep):
    users = db.metadata.tables["users"]
    collaborators = db.metadata.tables["collaborators"]

    remaining = connection.execute(
        select(users.c.id, users.c.email, users.c.active)
    ).mappings().all()
    if len(remaining) != 1:
        raise RuntimeError(f"Verificación fallida: quedaron {len(remaining)} usuarios.")
    if int(remaining[0]["id"]) != keep.user_id or not remaining[0]["active"]:
        raise RuntimeError("Verificación fallida: superadmin incorrecto o inactivo.")

    expected_collabs = 1 if keep.collaborator_id is not None else 0
    if _count(connection, collaborators) != expected_collabs:
        raise RuntimeError("Verificación fallida en colaboradores.")

    dirty = []
    for table in _operational_tables():
        count = _count(connection, table)
        if count:
            dirty.append(f"{table.name}={count}")
    if dirty:
        raise RuntimeError(
            "Verificación fallida: aún hay datos operativos: " + ", ".join(dirty)
        )


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep-email", default=os.getenv("ADMIN_EMAIL"))
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args(argv)

    app = create_app()
    with app.app_context():
        with db.engine.connect() as connection:
            keep = _resolve_keep_identity(connection, args.keep_email)
            preview = _preview(connection, keep)
            _print_preview(keep, preview)

        if not args.execute:
            print("MODO PREVISUALIZACIÓN: no se borró ningún dato.")
            print(
                "Para ejecutar: python -m scripts.reset_test_data "
                f"--execute --confirm {CONFIRM_TOKEN}"
            )
            return 0

        if args.confirm != CONFIRM_TOKEN:
            print(
                f"ABORTADO. Usa --confirm {CONFIRM_TOKEN}",
                file=sys.stderr,
            )
            return 2

        print("Creando backup obligatorio antes de borrar...")
        backup_path = create_backup(source="pre_reset_test_data")
        print(f"Backup creado: {backup_path}")

        with db.engine.begin() as connection:
            keep = _resolve_keep_identity(connection, args.keep_email)
            _execute_cleanup(connection, keep)

        with db.engine.connect() as connection:
            keep = _resolve_keep_identity(connection, args.keep_email)
            _verify(connection, keep)

        print("\nLIMPIEZA COMPLETADA CORRECTAMENTE.")
        print(f"Superadmin conservado: {keep.email}")
        print(f"Backup previo: {backup_path}")
        print("Nexora quedó lista para comenzar con información real.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
