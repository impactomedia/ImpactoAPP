from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required
from sqlalchemy import text

from app.backup_scheduler import automatic_backup_status
from app.catalog_runtime import save_system_setting, system_setting
from app.critical_audit import build_critical_audit
from app.decorators import roles_required
from app.extensions import db
from app.helpers import audit


bp = Blueprint("acceptance", __name__, url_prefix="/acceptance")


CRITERIA = [
    {
        "key": "separate_test_environment",
        "category": "Infraestructura",
        "title": "Ambiente de pruebas separado de producción",
        "description": (
            "Existe un ambiente temporal/staging independiente para ejecutar "
            "pruebas de aceptación y restauración sin tocar producción."
        ),
        "manual": True,
    },
    {
        "key": "roles_real_examples",
        "category": "Seguridad y acceso",
        "title": "Roles y permisos probados con usuarios de ejemplo",
        "description": (
            "Se validaron al menos administración, supervisor, asesor y producción "
            "con su alcance real de información."
        ),
        "manual": True,
    },
    {
        "key": "prospect_to_client_history",
        "category": "CRM y ventas",
        "title": "Seguimiento → Cliente conserva historial",
        "description": (
            "Un contacto nuevo entra como Seguimiento y, al comprar, el mismo "
            "registro pasa a Cliente sin perder interacciones."
        ),
        "manual": True,
    },
    {
        "key": "portfolio_scope",
        "category": "CRM y ventas",
        "title": "Cartera y asignaciones respetan alcance",
        "description": (
            "Asesores ven su cartera y colaboradores acceden únicamente a clientes "
            "donde tienen asignación/permiso vigente."
        ),
        "manual": True,
    },
    {
        "key": "sale_creates_finance_operation",
        "category": "CRM y ventas",
        "title": "Venta genera saldos y operación correspondiente",
        "description": (
            "La venta genera saldos/pagos y proyecto, tareas u orden de imprenta "
            "según el producto."
        ),
        "manual": True,
    },
    {
        "key": "client_record_complete",
        "category": "Clientes",
        "title": "Ficha completa del cliente",
        "description": (
            "Estado, paquetes, modalidad, equipo, compras, pagos, servicios, "
            "tareas, archivos y timeline se visualizan correctamente."
        ),
        "manual": True,
    },
    {
        "key": "package_modalities",
        "category": "Clientes",
        "title": "Modalidades de paquetes correctas",
        "description": (
            "3/6/8 meses = Inquilino; Golden = Dueño sin nuestro mantenimiento; "
            "Premium/VIP = Dueño con nuestro mantenimiento."
        ),
        "manual": True,
    },
    {
        "key": "package_status_history",
        "category": "Clientes",
        "title": "Status e historial de paquetes",
        "description": (
            "Activo/Inactivo/Caducado conservan fechas e historial de cambios."
        ),
        "manual": True,
    },
    {
        "key": "multi_assignments",
        "category": "Clientes",
        "title": "Asignación simultánea a varios colaboradores",
        "description": (
            "La ficha conserva múltiples asignaciones e historial individual."
        ),
        "manual": True,
    },
    {
        "key": "attendance_flow",
        "category": "RR. HH.",
        "title": "Jornada completa y horas netas",
        "description": (
            "Entrada, breaks, almuerzo y salida funcionan y el cálculo de horas "
            "netas es consistente."
        ),
        "manual": True,
    },
    {
        "key": "attendance_corrections",
        "category": "RR. HH.",
        "title": "Correcciones conservan evidencia original",
        "description": (
            "Una incidencia de marcación puede justificarse/corregirse sin borrar "
            "la evidencia de origen."
        ),
        "manual": True,
    },
    {
        "key": "vacation_policy",
        "category": "RR. HH.",
        "title": "Vacaciones según política configurada",
        "description": (
            "Acumulación/saldo y solicitudes/aprobaciones/cancelaciones respetan "
            "la política definida por Impacto."
        ),
        "manual": True,
    },
    {
        "key": "reports_reconcile",
        "category": "Reportes",
        "title": "Reportes cuadran con registros fuente",
        "description": (
            "Las cifras exportadas y mostradas coinciden con la muestra de ventas, "
            "pagos, tareas, imprenta y finanzas usada en aceptación."
        ),
        "manual": True,
    },
    {
        "key": "alerts_dates",
        "category": "Notificaciones",
        "title": "Alertas con fechas de prueba",
        "description": (
            "Seguimiento, pago, tarea y renovación generan alertas esperadas."
        ),
        "manual": True,
    },
    {
        "key": "sensitive_audit",
        "category": "Seguridad y acceso",
        "title": "Operaciones sensibles aparecen en auditoría",
        "description": (
            "Reversos, cambios de permisos, transferencias y otras operaciones "
            "sensibles quedan registradas."
        ),
        "manual": True,
    },
    {
        "key": "desktop_mobile",
        "category": "Experiencia",
        "title": "Prueba final en PC y móvil",
        "description": (
            "Pantallas críticas, navegación, formularios y tablas son utilizables "
            "en escritorio y móvil."
        ),
        "manual": True,
    },
    {
        "key": "backup_restore_drill",
        "category": "Continuidad",
        "title": "Backup y restauración de prueba",
        "description": (
            "Se generó un backup y se restauró en una base temporal separada, "
            "verificando tablas y acceso."
        ),
        "manual": True,
    },
    {
        "key": "documentation_delivered",
        "category": "Entrega",
        "title": "Documentación técnica y manual administrativo entregados",
        "description": (
            "Impacto recibe documentación de arquitectura, operación, respaldo, "
            "restore, seguridad y administración."
        ),
        "manual": True,
    },
    {
        "key": "admin_access_delivered",
        "category": "Entrega",
        "title": "Acceso administrativo completo entregado",
        "description": (
            "Impacto Media Agency conserva acceso acordado a Nexora, Railway, "
            "base de datos y repositorio/código fuente."
        ),
        "manual": True,
    },
    {
        "key": "physical_sale_print_order",
        "category": "Imprenta",
        "title": "Venta física genera orden de imprenta vinculada",
        "description": (
            "La orden queda asociada al cliente y a la venta correspondiente."
        ),
        "manual": True,
    },
    {
        "key": "printing_state_flow",
        "category": "Imprenta",
        "title": "Flujo Diseño → Recepción claramente diferenciado",
        "description": (
            "Diseño, aprobación, aprobación de envío, enviado y recibido/no "
            "recibido se distinguen correctamente."
        ),
        "manual": True,
    },
    {
        "key": "partial_shipments",
        "category": "Imprenta",
        "title": "Productos múltiples y envíos parciales",
        "description": (
            "Los artículos pendientes permanecen controlados después de un envío "
            "parcial."
        ),
        "manual": True,
    },
    {
        "key": "tracking_eta",
        "category": "Imprenta",
        "title": "Tracking/comprobante y fecha estimada",
        "description": (
            "El envío registra tracking cuando aplica y una fecha aproximada de "
            "recepción."
        ),
        "manual": True,
    },
    {
        "key": "late_printing",
        "category": "Imprenta",
        "title": "Pedidos vencidos aparecen atrasados",
        "description": (
            "La orden continúa atrasada hasta confirmar recepción o abrir incidencia."
        ),
        "manual": True,
    },
    {
        "key": "not_received_incident",
        "category": "Imprenta",
        "title": "No recibido conserva seguimiento e historial",
        "description": (
            "La incidencia no destruye la orden original ni su trazabilidad."
        ),
        "manual": True,
    },
    {
        "key": "client_printing_history",
        "category": "Imprenta",
        "title": "Ficha de cliente muestra productos físicos",
        "description": (
            "Estados, tracking, fechas estimadas/reales e incidencias son visibles."
        ),
        "manual": True,
    },
    {
        "key": "printing_reports",
        "category": "Imprenta",
        "title": "Reportes de imprenta cubren estados requeridos",
        "description": (
            "Se identifican vendidos, pendientes de envío, en tránsito, recibidos "
            "y no recibidos."
        ),
        "manual": True,
    },
]


def _state_key(key):
    return f"acceptance:v1.23:{key}"


def _load_manual_state(key):
    raw = system_setting(_state_key(key))
    if not raw:
        return {
            "status": "pendiente",
            "notes": "",
            "updated_at": None,
            "updated_by": None,
        }
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        parsed = {}
    return {
        "status": parsed.get("status", "pendiente"),
        "notes": parsed.get("notes", ""),
        "updated_at": parsed.get("updated_at"),
        "updated_by": parsed.get("updated_by"),
    }


def _automatic_checks():
    checks = []

    try:
        db.session.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db.session.rollback()
        db_ok = False

    checks.append({
        "key": "database",
        "title": "Base de datos responde",
        "passed": db_ok,
        "detail": "SELECT 1 ejecutado correctamente." if db_ok else "No se pudo consultar la base.",
    })

    secret = current_app.config.get("SECRET_KEY")
    secret_ok = bool(secret and secret != "change-this-in-production")
    checks.append({
        "key": "secret_key",
        "title": "SECRET_KEY de producción configurada",
        "passed": secret_ok,
        "detail": "La clave no usa el valor predeterminado." if secret_ok else "Configura una SECRET_KEY real en Railway.",
    })

    mount = os.getenv("RAILWAY_VOLUME_MOUNT_PATH")
    backup_dir = Path(current_app.config.get("BACKUP_DIR", ""))
    storage_ok = bool(mount or (backup_dir.is_absolute() and str(backup_dir).startswith("/data")))
    checks.append({
        "key": "persistent_storage",
        "title": "Almacenamiento persistente detectado",
        "passed": storage_ok,
        "detail": mount or str(backup_dir) or "No detectado.",
    })

    try:
        backup = automatic_backup_status()
        backup_enabled = bool(backup.get("enabled"))
        last_file = backup.get("last_file")
        backup_ok = backup_enabled and bool(last_file)
        detail = (
            f"Activo. Último archivo: {last_file}"
            if backup_ok
            else "Verifica que el scheduler esté activo y exista al menos un backup."
        )
    except Exception:
        backup_ok = False
        detail = "No fue posible leer el estado de backups automáticos."

    checks.append({
        "key": "backup_status",
        "title": "Backups automáticos activos con copia existente",
        "passed": backup_ok,
        "detail": detail,
    })

    try:
        quality = build_critical_audit()
        quality_ok = bool(quality.get("ready"))
        critical = int(quality.get("critical", 0))
        detail = f"Hallazgos críticos: {critical}"
    except Exception:
        quality_ok = False
        detail = "No fue posible ejecutar la Auditoría de Flujos."

    checks.append({
        "key": "critical_audit",
        "title": "Auditoría de Flujos sin hallazgos críticos",
        "passed": quality_ok,
        "detail": detail,
    })

    testing_ok = not bool(current_app.config.get("TESTING"))
    checks.append({
        "key": "production_mode",
        "title": "Instancia actual fuera de TESTING",
        "passed": testing_ok,
        "detail": "Modo productivo." if testing_ok else "Esta instancia está en modo TESTING.",
    })

    return checks


def _report():
    criteria = []
    for definition in CRITERIA:
        state = _load_manual_state(definition["key"])
        criteria.append({**definition, **state})

    checks = _automatic_checks()
    manual_approved = sum(
        1 for row in criteria if row["status"] == "aprobado"
    )
    manual_rejected = sum(
        1 for row in criteria if row["status"] == "rechazado"
    )
    automatic_passed = sum(1 for row in checks if row["passed"])

    return {
        "criteria": criteria,
        "automatic_checks": checks,
        "manual_approved": manual_approved,
        "manual_rejected": manual_rejected,
        "manual_total": len(criteria),
        "automatic_passed": automatic_passed,
        "automatic_total": len(checks),
        "ready": (
            manual_approved == len(criteria)
            and manual_rejected == 0
            and automatic_passed == len(checks)
        ),
    }


@bp.route("/")
@login_required
@roles_required("superadmin", "admin", "manager")
def index():
    return render_template(
        "acceptance/index.html",
        report=_report(),
    )


@bp.route("/criterion/<key>", methods=["POST"])
@login_required
@roles_required("superadmin", "admin", "manager")
def update_criterion(key):
    definition = next(
        (row for row in CRITERIA if row["key"] == key),
        None,
    )
    if not definition:
        abort(404)

    status = (request.form.get("status") or "").strip()
    notes = (request.form.get("notes") or "").strip()[:1500]

    if status not in {"pendiente", "aprobado", "rechazado"}:
        abort(400)

    before = _load_manual_state(key)
    payload = {
        "status": status,
        "notes": notes,
        "updated_at": datetime.utcnow().isoformat(),
        "updated_by": current_user.name,
    }

    save_system_setting(
        _state_key(key),
        json.dumps(payload, ensure_ascii=False),
        f"Aceptación final: {definition['title']}",
    )
    audit(
        "actualizar_criterio_aceptacion",
        "SystemSetting",
        before=before,
        after={
            "criterion": key,
            **payload,
        },
        reason=notes or f"Estado {status}",
    )
    db.session.commit()

    flash("Criterio de aceptación actualizado.", "success")
    return redirect(url_for("acceptance.index") + f"#{key}")


@bp.route("/export.txt")
@login_required
@roles_required("superadmin", "admin", "manager")
def export_report():
    report = _report()
    lines = [
        "IMPACTO NEXORA - REPORTE DE ACEPTACION FINAL",
        "============================================",
        "",
        f"Generado UTC: {datetime.utcnow().isoformat()}",
        f"Estado global: {'LISTO PARA ENTREGA' if report['ready'] else 'PENDIENTE'}",
        "",
        "VALIDACIONES AUTOMATICAS",
        "------------------------",
    ]

    for row in report["automatic_checks"]:
        lines.append(
            f"[{'OK' if row['passed'] else 'PENDIENTE'}] "
            f"{row['title']} — {row['detail']}"
        )

    lines.extend(["", "CRITERIOS MANUALES", "------------------"])
    for row in report["criteria"]:
        lines.append(
            f"[{row['status'].upper()}] {row['title']}"
        )
        if row["notes"]:
            lines.append(f"    Nota: {row['notes']}")
        if row["updated_at"]:
            lines.append(
                f"    Actualizado: {row['updated_at']} · {row['updated_by'] or '—'}"
            )

    lines.extend([
        "",
        f"Aprobados manuales: {report['manual_approved']}/{report['manual_total']}",
        f"Automáticos correctos: {report['automatic_passed']}/{report['automatic_total']}",
        "",
    ])

    return Response(
        "\n".join(lines),
        mimetype="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": (
                "attachment; filename=aceptacion_final_nexora_v1.23.txt"
            )
        },
    )
