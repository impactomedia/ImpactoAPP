from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal

from sqlalchemy import or_

from app.catalog_runtime import system_setting
from app.extensions import db
from app.models import (
    AccountReceivable,
    Client,
    Collaborator,
    OwnershipHistory,
    Payment,
    PrintIncident,
    PrintOrder,
    Project,
    Role,
    Sale,
    User,
    Shipment,
    Task,
    VacationMovement,
    LeaveRequest,
)


MONEY = Decimal("0.01")


def _money(value):
    return Decimal(str(value or 0)).quantize(MONEY)


def _item(key, title, severity, count, description, examples=None):
    return {
        "key": key,
        "title": title,
        "severity": severity,
        "count": int(count),
        "description": description,
        "examples": (examples or [])[:10],
        "ok": int(count) == 0,
    }


def _commercial_checks():
    clients_with_sale_but_followup = (
        Client.query
        .filter(
            Client.record_type != "cliente",
            Client.sales.any(),
        )
        .order_by(Client.id)
        .all()
    )

    ownership_without_reason = (
        OwnershipHistory.query
        .filter(
            or_(
                OwnershipHistory.reason.is_(None),
                OwnershipHistory.reason == "",
            )
        )
        .order_by(OwnershipHistory.id.desc())
        .all()
    )

    return [
        _item(
            "sale_without_conversion",
            "Venta confirmada sin conversión a Cliente",
            "critical",
            len(clients_with_sale_but_followup),
            (
                "Todo Seguimiento con una compra debe conservar el mismo registro "
                "y quedar convertido a Cliente."
            ),
            [
                f"{row.code} · {row.business_name}"
                for row in clients_with_sale_but_followup
            ],
        ),
        _item(
            "ownership_without_reason",
            "Reasignaciones comerciales sin motivo",
            "warning",
            len(ownership_without_reason),
            (
                "Los cambios de responsable comercial deben conservar motivo, "
                "usuario y fecha."
            ),
            [
                f"Historial #{row.id} · Cliente #{row.client_id}"
                for row in ownership_without_reason
            ],
        ),
    ]


def _sales_checks():
    mismatches = []
    invalid_payments = []

    sales = Sale.query.order_by(Sale.id).all()
    for sale in sales:
        confirmed = sum(
            (
                _money(payment.amount)
                for payment in sale.payments
                if payment.status == "confirmado"
            ),
            Decimal("0.00"),
        )
        expected_balance = max(
            Decimal("0.00"),
            _money(sale.total) - confirmed,
        ).quantize(MONEY)

        problems = []
        if abs(_money(sale.amount_paid) - confirmed) > MONEY:
            problems.append("monto pagado")
        if abs(_money(sale.balance) - expected_balance) > MONEY:
            problems.append("saldo")

        receivable = sale.receivable
        if receivable:
            if abs(_money(receivable.total_amount) - _money(sale.total)) > MONEY:
                problems.append("CxC total")
            if abs(_money(receivable.paid_amount) - confirmed) > MONEY:
                problems.append("CxC pagado")
        elif expected_balance > 0:
            problems.append("CxC faltante")

        if problems:
            mismatches.append(
                f"{sale.sale_no} · {', '.join(problems)}"
            )

    for payment in Payment.query.filter(Payment.amount <= 0).all():
        invalid_payments.append(
            f"Pago #{payment.id} · Venta #{payment.sale_id}"
        )

    return [
        _item(
            "sale_balance_mismatch",
            "Ventas con saldo/cobranza inconsistente",
            "critical",
            len(mismatches),
            (
                "Los pagos confirmados, saldo de la venta y cuenta por cobrar "
                "deben cuadrar entre sí."
            ),
            mismatches,
        ),
        _item(
            "invalid_payment_amount",
            "Pagos con monto no positivo",
            "critical",
            len(invalid_payments),
            "Ningún pago puede ser cero o negativo.",
            invalid_payments,
        ),
    ]


def _operations_checks():
    blocking_tasks = (
        Task.query
        .join(Project, Task.project_id == Project.id)
        .filter(
            Project.status == "completado",
            Task.checklist.isnot(None),
            Task.checklist != "",
            Task.status.notin_(["completada", "cancelada"]),
        )
        .order_by(Project.id, Task.id)
        .all()
    )

    confirmed_sales = (
        Sale.query
        .filter(Sale.status == "confirmada")
        .order_by(Sale.id)
        .all()
    )
    missing_operation = []

    for sale in confirmed_sales:
        physical = any(
            item.product and item.product.is_physical
            for item in sale.items
        )
        non_physical = any(
            not (item.product and item.product.is_physical)
            for item in sale.items
        )

        if physical:
            has_sale_order = PrintOrder.query.filter_by(
                sale_id=sale.id
            ).first()
            if not has_sale_order:
                missing_operation.append(
                    f"{sale.sale_no} · falta orden de imprenta"
                )

        if non_physical:
            has_project = Project.query.filter_by(
                sale_id=sale.id
            ).first()
            if not has_project:
                missing_operation.append(
                    f"{sale.sale_no} · falta proyecto operativo"
                )

    return [
        _item(
            "closed_project_with_required_tasks",
            "Proyectos cerrados con requisitos obligatorios pendientes",
            "critical",
            len(blocking_tasks),
            (
                "Un proyecto no debe cerrarse mientras existan tareas "
                "obligatorias de plantilla pendientes."
            ),
            [
                f"{task.project.project_no} · {task.title}"
                for task in blocking_tasks
            ],
        ),
        _item(
            "sale_without_operation",
            "Ventas sin artefacto operativo esperado",
            "warning",
            len(missing_operation),
            (
                "Una venta confirmada debería generar proyecto/tareas o una "
                "orden de imprenta según el producto vendido. Registros "
                "históricos importados pueden requerir revisión manual."
            ),
            missing_operation,
        ),
    ]


def _hierarchy_checks():
    advisor_role = Role.query.filter_by(name="advisor").first()
    if not advisor_role:
        return [
            _item(
                "advisor_hierarchy",
                "Asesores sin jerarquía completa",
                "warning",
                0,
                "No existe un rol advisor activo que auditar.",
            )
        ]

    advisors = (
        Collaborator.query
        .join(User, Collaborator.user_id == User.id)
        .filter(
            Collaborator.status == "activo",
            User.role_id == advisor_role.id,
        )
        .order_by(Collaborator.id)
        .all()
    )

    missing = []
    for row in advisors:
        gaps = []
        if not row.supervisor_id:
            gaps.append("sin supervisor")
        if not row.advisor_project_id:
            gaps.append("sin grupo/proyecto")
        if gaps:
            missing.append(
                f"{row.user.name} · {', '.join(gaps)}"
            )

    return [
        _item(
            "advisor_hierarchy",
            "Asesores sin jerarquía completa",
            "warning",
            len(missing),
            (
                "Cada Agente debe pertenecer a su grupo/proyecto comercial y "
                "tener Supervisor vigente, salvo excepción autorizada."
            ),
            missing,
        )
    ]


def _vacation_checks():
    default_rate_raw = system_setting(
        "vacation_default_rate",
        "0",
    )
    try:
        default_rate = Decimal(str(default_rate_raw or "0"))
    except Exception:
        default_rate = Decimal("0")

    active = Collaborator.query.filter_by(status="activo").all()
    no_rate = []
    for collaborator in active:
        rate = _money(collaborator.vacation_rate)
        if rate <= 0 and default_rate <= 0:
            no_rate.append(
                collaborator.user.name
                if collaborator.user
                else f"Colaborador #{collaborator.id}"
            )

    approved_without_movement = []
    approved = LeaveRequest.query.filter_by(
        leave_type="vacaciones",
        status="aprobada",
    ).all()
    for leave in approved:
        movement = VacationMovement.query.filter_by(
            leave_request_id=leave.id
        ).first()
        if not movement:
            approved_without_movement.append(
                f"Solicitud #{leave.id} · {leave.collaborator.user.name}"
            )

    return [
        _item(
            "vacation_rate_missing",
            "Política/tasa de vacaciones sin definir",
            "warning",
            len(no_rate),
            (
                "La acumulación debe usar una tasa configurable. El auditor no "
                "aplica una fórmula fija cuando la empresa todavía no la ha definido."
            ),
            no_rate,
        ),
        _item(
            "approved_leave_without_movement",
            "Vacaciones aprobadas sin movimiento de saldo",
            "critical",
            len(approved_without_movement),
            (
                "Toda aprobación de vacaciones debe dejar un movimiento trazable "
                "en el libro de vacaciones."
            ),
            approved_without_movement,
        ),
    ]


def _printing_checks():
    today = date.today()
    overdue_without_incident = []

    shipments = (
        Shipment.query
        .filter(
            Shipment.status.in_(["en_transito", "parcial"]),
            Shipment.estimated_delivery.isnot(None),
            Shipment.estimated_delivery < today,
        )
        .order_by(Shipment.estimated_delivery)
        .all()
    )

    for shipment in shipments:
        open_incident = PrintIncident.query.filter_by(
            shipment_id=shipment.id,
            status="abierta",
        ).first()
        if not open_incident:
            overdue_without_incident.append(
                (
                    f"{shipment.order.order_no} · envío #{shipment.id} · "
                    f"estimado {shipment.estimated_delivery}"
                )
            )

    received_incomplete = []
    orders = PrintOrder.query.filter_by(status="recibido").all()
    for order in orders:
        pending = [
            item
            for item in order.items
            if (item.qty_received or 0) < (item.quantity or 0)
        ]
        if pending:
            received_incomplete.append(
                f"{order.order_no} · {len(pending)} artículo(s) pendientes"
            )

    return [
        _item(
            "overdue_print_without_incident",
            "Envíos atrasados sin incidencia abierta",
            "warning",
            len(overdue_without_incident),
            (
                "Un envío vencido debe permanecer destacado hasta confirmar "
                "recepción o registrar una incidencia de no recibido."
            ),
            overdue_without_incident,
        ),
        _item(
            "received_order_with_pending_items",
            "Órdenes recibidas con artículos pendientes",
            "critical",
            len(received_incomplete),
            (
                "Una orden no debe quedar recibida si todavía existen artículos "
                "pendientes por recibir."
            ),
            received_incomplete,
        ),
    ]


def build_critical_audit():
    sections = [
        {
            "name": "Comercial",
            "items": _commercial_checks(),
        },
        {
            "name": "Ventas y cobranza",
            "items": _sales_checks(),
        },
        {
            "name": "Operaciones",
            "items": _operations_checks(),
        },
        {
            "name": "Jerarquía",
            "items": _hierarchy_checks(),
        },
        {
            "name": "Vacaciones",
            "items": _vacation_checks(),
        },
        {
            "name": "Imprenta",
            "items": _printing_checks(),
        },
    ]

    items = [
        item
        for section in sections
        for item in section["items"]
    ]
    critical = sum(
        item["count"]
        for item in items
        if item["severity"] == "critical"
    )
    warnings = sum(
        item["count"]
        for item in items
        if item["severity"] == "warning"
    )
    passed = sum(1 for item in items if item["ok"])

    return {
        "sections": sections,
        "critical": critical,
        "warnings": warnings,
        "passed": passed,
        "total_checks": len(items),
        "ready": critical == 0,
    }
