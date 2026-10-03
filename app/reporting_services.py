from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from flask_login import current_user
from sqlalchemy import or_

from app.extensions import db
from app.models import (
    AccountReceivable,
    AttendanceMark,
    Client,
    Collaborator,
    Commission,
    Expense,
    Interaction,
    LeaveRequest,
    Payable,
    Payment,
    PrintIncident,
    PrintOrder,
    Project,
    Renewal,
    Sale,
    Shipment,
    Task,
)


CLOSED_PROJECTS = {"completado", "cancelado"}
CLOSED_TASKS = {"completada", "cancelada"}


def _d(value):
    return Decimal(str(value or 0))


def role_name():
    return current_user.role.name if current_user.role else ""


def is_audit():
    return role_name() == "audit"


def team_ids():
    collaborator = current_user.collaborator
    if not collaborator:
        return []
    return [collaborator.id] + [row.id for row in collaborator.subordinates]


def date_bounds(start_date, end_date):
    return (
        datetime.combine(start_date, time.min),
        datetime.combine(end_date, time.max),
    )


def client_query():
    query = Client.query
    role = role_name()
    collaborator = current_user.collaborator
    if role == "advisor" and collaborator:
        return query.filter(Client.owner_id == collaborator.id)
    if role == "supervisor" and collaborator:
        return query.filter(Client.owner_id.in_(team_ids()))
    return query


def sale_query():
    query = Sale.query
    role = role_name()
    collaborator = current_user.collaborator
    if role == "advisor" and collaborator:
        return query.filter(
            or_(
                Sale.advisor_id == collaborator.id,
                Sale.client.has(Client.owner_id == collaborator.id),
            )
        )
    if role == "supervisor" and collaborator:
        ids = team_ids()
        return query.filter(
            or_(
                Sale.advisor_id.in_(ids),
                Sale.client.has(Client.owner_id.in_(ids)),
            )
        )
    return query


def payment_query():
    query = Payment.query
    role = role_name()
    collaborator = current_user.collaborator
    if role == "advisor" and collaborator:
        return query.filter(
            or_(
                Payment.sale.has(Sale.advisor_id == collaborator.id),
                Payment.client.has(Client.owner_id == collaborator.id),
            )
        )
    if role == "supervisor" and collaborator:
        ids = team_ids()
        return query.filter(
            or_(
                Payment.sale.has(Sale.advisor_id.in_(ids)),
                Payment.client.has(Client.owner_id.in_(ids)),
            )
        )
    return query


def commission_query():
    query = Commission.query
    role = role_name()
    collaborator = current_user.collaborator
    if role == "advisor" and collaborator:
        return query.filter(Commission.advisor_id == collaborator.id)
    if role == "supervisor" and collaborator:
        return query.filter(Commission.advisor_id.in_(team_ids()))
    return query


def project_query():
    query = Project.query
    role = role_name()
    collaborator = current_user.collaborator
    if not collaborator:
        return query
    if role == "advisor":
        return query.filter(Project.client.has(Client.owner_id == collaborator.id))
    if role == "supervisor":
        return query.filter(Project.client.has(Client.owner_id.in_(team_ids())))
    if role == "production":
        return query.filter(
            or_(
                Project.coordinator_id == collaborator.id,
                Project.members.any(id=collaborator.id),
            )
        )
    return query


def task_query():
    query = Task.query
    role = role_name()
    collaborator = current_user.collaborator

    if role in {"superadmin", "admin", "manager", "audit"}:
        return query
    if not collaborator:
        return query.filter(Task.id == -1)

    own = or_(
        Task.assignee_id == collaborator.id,
        Task.collaborators.any(id=collaborator.id),
    )

    if role == "supervisor":
        ids = team_ids()
        return query.filter(
            or_(
                Task.assignee_id.in_(ids),
                Task.collaborators.any(Collaborator.id.in_(ids)),
                Task.project.has(Project.client.has(Client.owner_id.in_(ids))),
            )
        )
    if role == "advisor":
        return query.filter(
            or_(
                own,
                Task.project.has(Project.client.has(Client.owner_id == collaborator.id)),
            )
        )
    if role == "production":
        return query.filter(own)
    return query.filter(own)


def print_query():
    query = PrintOrder.query
    role = role_name()
    collaborator = current_user.collaborator
    if role == "advisor" and collaborator:
        return query.filter(PrintOrder.client.has(Client.owner_id == collaborator.id))
    if role == "supervisor" and collaborator:
        return query.filter(PrintOrder.client.has(Client.owner_id.in_(team_ids())))
    return query


def renewal_query():
    query = Renewal.query
    role = role_name()
    collaborator = current_user.collaborator
    if role == "advisor" and collaborator:
        return query.filter(Renewal.client.has(Client.owner_id == collaborator.id))
    if role == "supervisor" and collaborator:
        return query.filter(Renewal.client.has(Client.owner_id.in_(team_ids())))
    return query


def allowed_areas():
    role = role_name()
    audit = is_audit()
    areas = set()

    if current_user.has_permission("finance.view") or audit:
        areas.add("finance")
    if current_user.has_permission("hr.view") or audit:
        areas.add("hr")
    if current_user.has_permission("crm.view") or audit:
        areas.add("commercial")
    if (current_user.has_permission("clients.view") or audit) and role != "production":
        areas.add("clients")
    if current_user.has_permission("projects.view") or current_user.has_permission("tasks.view") or audit:
        areas.add("operations")
    if current_user.has_permission("printing.view") or audit:
        areas.add("printing")
    return areas


def allowed_export_reports():
    areas = allowed_areas()
    result = set()

    if "finance" in areas:
        result.update({"expenses", "receivables", "payables", "payments"})
    if "hr" in areas:
        result.update({"attendance", "leaves"})
    if "commercial" in areas:
        result.update({"sales", "payments", "prospects", "renewals"})
    if "clients" in areas:
        result.update({"clients", "renewals"})
    if "operations" in areas:
        result.update({"projects", "tasks"})
    if "printing" in areas:
        result.add("printing")

    return result


def build_report_context(start_date, end_date):
    start_dt, end_dt = date_bounds(start_date, end_date)
    today = date.today()
    areas = allowed_areas()
    sections = {}

    if "finance" in areas:
        payments = payment_query().filter(
            Payment.status == "confirmado",
            Payment.effective_date >= start_date,
            Payment.effective_date <= end_date,
        ).all()
        expenses = Expense.query.filter(
            Expense.status == "pagado",
            Expense.expense_date >= start_date,
            Expense.expense_date <= end_date,
        ).all()
        receivables = AccountReceivable.query.filter(
            AccountReceivable.status.notin_(["pagado", "cancelado"])
        ).all()
        payables = Payable.query.filter(
            Payable.status.notin_(["pagado", "cancelado"])
        ).all()

        expense_categories = defaultdict(Decimal)
        for row in expenses:
            expense_categories[row.category or "Otros"] += _d(row.amount)

        income_total = sum((_d(row.amount) for row in payments), Decimal("0"))
        expense_total = sum((_d(row.amount) for row in expenses), Decimal("0"))

        sections["finance"] = {
            "income": income_total,
            "expenses": expense_total,
            "result": income_total - expense_total,
            "receivable": sum(
                (max(Decimal("0"), _d(row.total_amount) - _d(row.paid_amount)) for row in receivables),
                Decimal("0"),
            ),
            "payable": sum(
                (max(Decimal("0"), _d(row.amount) - _d(row.paid_amount)) for row in payables),
                Decimal("0"),
            ),
            "expense_categories": sorted(
                (
                    {"name": name, "amount": amount}
                    for name, amount in expense_categories.items()
                ),
                key=lambda row: row["amount"],
                reverse=True,
            ),
        }

    if "hr" in areas:
        marks = AttendanceMark.query.filter(
            AttendanceMark.marked_at >= start_dt,
            AttendanceMark.marked_at <= end_dt,
        ).all()
        mark_counts = defaultdict(int)
        for row in marks:
            mark_counts[row.mark_type or "sin_tipo"] += 1

        leaves = LeaveRequest.query.filter(
            LeaveRequest.start_date <= end_date,
            LeaveRequest.end_date >= start_date,
        ).all()
        leave_counts = defaultdict(lambda: {"count": 0, "days": Decimal("0")})
        for row in leaves:
            key = row.leave_type or "otro"
            leave_counts[key]["count"] += 1
            if row.status == "aprobada":
                leave_counts[key]["days"] += _d(row.days)

        sections["hr"] = {
            "marks": dict(mark_counts),
            "employees_with_entry": len(
                {
                    row.collaborator_id
                    for row in marks
                    if row.mark_type == "entrada"
                }
            ),
            "corrected_marks": sum(1 for row in marks if row.corrected),
            "leave_total": len(leaves),
            "leave_pending": sum(1 for row in leaves if row.status == "pendiente"),
            "leave_types": [
                {"name": name, "count": values["count"], "days": values["days"]}
                for name, values in sorted(leave_counts.items())
            ],
        }

    if "commercial" in areas:
        clients = client_query().filter(
            Client.created_at >= start_dt,
            Client.created_at <= end_dt,
        ).all()
        interactions = (
            Interaction.query
            .filter(
                Interaction.client_id.in_(
                    client_query().with_entities(Client.id)
                ),
                Interaction.occurred_at >= start_dt,
                Interaction.occurred_at <= end_dt,
            )
            .count()
        )
        sales = sale_query().filter(
            Sale.sale_date >= start_date,
            Sale.sale_date <= end_date,
        ).all()
        payments = payment_query().filter(
            Payment.status == "confirmado",
            Payment.effective_date >= start_date,
            Payment.effective_date <= end_date,
        ).all()
        commissions = commission_query().filter(
            Commission.created_at >= start_dt,
            Commission.created_at <= end_dt,
            Commission.status != "anulada",
        ).all()

        pipeline = defaultdict(int)
        for row in client_query().filter(Client.record_type == "seguimiento").all():
            pipeline[row.pipeline_stage or "sin_etapa"] += 1

        total_created = len(clients)
        converted = sum(1 for row in clients if row.record_type == "cliente")
        sections["commercial"] = {
            "prospects": sum(1 for row in clients if row.record_type == "seguimiento"),
            "interactions": interactions,
            "sales_count": len(sales),
            "sales_total": sum((_d(row.total) for row in sales), Decimal("0")),
            "collected": sum((_d(row.amount) for row in payments), Decimal("0")),
            "lost": sum(1 for row in clients if row.pipeline_stage == "perdido"),
            "conversion": round((converted / total_created) * 100, 1) if total_created else 0,
            "commissions": sum((_d(row.amount) for row in commissions), Decimal("0")),
            "pipeline": dict(pipeline),
        }

    if "clients" in areas:
        clients = client_query().filter(Client.record_type == "cliente").all()
        client_ids = [row.id for row in clients] or [-1]
        purchases = sale_query().filter(
            Sale.client_id.in_(client_ids),
            Sale.sale_date >= start_date,
            Sale.sale_date <= end_date,
        ).all()
        payments = payment_query().filter(
            Payment.client_id.in_(client_ids),
            Payment.status == "confirmado",
            Payment.effective_date >= start_date,
            Payment.effective_date <= end_date,
        ).all()
        receivables = AccountReceivable.query.filter(
            AccountReceivable.client_id.in_(client_ids),
            AccountReceivable.status.notin_(["pagado", "cancelado"]),
        ).all()

        active_contracts = sum(
            1
            for client in clients
            for contract in client.contracts
            if contract.status == "activo"
        )
        upcoming_renewals = renewal_query().filter(
            Renewal.status.in_(["pendiente", "contactado"]),
            Renewal.due_date >= today,
            Renewal.due_date <= today + timedelta(days=60),
        ).count()

        sections["clients"] = {
            "active": sum(1 for row in clients if row.client_status == "activo"),
            "inactive": sum(1 for row in clients if row.client_status != "activo"),
            "purchases": len(purchases),
            "purchases_total": sum((_d(row.total) for row in purchases), Decimal("0")),
            "payments": sum((_d(row.amount) for row in payments), Decimal("0")),
            "balance": sum(
                (max(Decimal("0"), _d(row.total_amount) - _d(row.paid_amount)) for row in receivables),
                Decimal("0"),
            ),
            "active_services": active_contracts,
            "renewals": upcoming_renewals,
        }

    if "operations" in areas:
        projects = project_query().filter(
            Project.created_at >= start_dt,
            Project.created_at <= end_dt,
        ).all()
        all_visible_projects = project_query().all()
        tasks = task_query().filter(
            Task.created_at >= start_dt,
            Task.created_at <= end_dt,
        ).all()
        now = datetime.utcnow()

        project_status = defaultdict(int)
        for row in projects:
            project_status[row.status or "sin_estado"] += 1

        task_status = defaultdict(int)
        for row in tasks:
            task_status[row.status or "sin_estado"] += 1

        delivery_days = []
        for row in all_visible_projects:
            if row.completed_on and row.starts_on and row.completed_on >= row.starts_on:
                delivery_days.append((row.completed_on - row.starts_on).days)

        workload = defaultdict(int)
        open_tasks = task_query().filter(Task.status.notin_(CLOSED_TASKS)).all()
        for row in open_tasks:
            if row.assignee and row.assignee.user:
                workload[row.assignee.user.name] += 1
            elif row.assignee:
                workload[row.assignee.code or f"Colaborador {row.assignee.id}"] += 1
            else:
                workload["Sin asignar"] += 1

        sections["operations"] = {
            "projects": len(projects),
            "project_status": dict(project_status),
            "late_projects": project_query().filter(
                Project.due_on.isnot(None),
                Project.due_on < today,
                Project.status.notin_(CLOSED_PROJECTS),
            ).count(),
            "tasks": len(tasks),
            "completed_tasks": sum(1 for row in tasks if row.status == "completada"),
            "overdue_tasks": task_query().filter(
                Task.due_at.isnot(None),
                Task.due_at < now,
                Task.status.notin_(CLOSED_TASKS),
            ).count(),
            "task_status": dict(task_status),
            "average_delivery_days": (
                round(sum(delivery_days) / len(delivery_days), 1)
                if delivery_days else None
            ),
            "workload": sorted(
                ({"name": name, "tasks": count} for name, count in workload.items()),
                key=lambda row: row["tasks"],
                reverse=True,
            ),
        }

    if "printing" in areas:
        orders = print_query().filter(
            PrintOrder.created_at >= start_dt,
            PrintOrder.created_at <= end_dt,
        ).all()
        order_ids = [row.id for row in orders] or [-1]
        status_counts = defaultdict(int)
        for row in orders:
            status_counts[row.status or "sin_estado"] += 1

        incidents = PrintIncident.query.filter(
            PrintIncident.order_id.in_(order_ids)
        ).all()
        shipments = Shipment.query.filter(Shipment.order_id.in_(order_ids)).all()
        delivery_days = []
        for row in shipments:
            if row.shipped_at and row.received_at and row.received_at >= row.shipped_at:
                delivery_days.append((row.received_at - row.shipped_at).total_seconds() / 86400)

        show_financial = (
            current_user.has_permission("finance.view")
            or role_name() in {"superadmin", "admin", "manager", "audit"}
        )
        total_sale = sum((_d(row.total_sale) for row in orders), Decimal("0")) if show_financial else None
        total_cost = sum((_d(row.total_cost) for row in orders), Decimal("0")) if show_financial else None
        sections["printing"] = {
            "orders": len(orders),
            "status": dict(status_counts),
            "open_incidents": sum(1 for row in incidents if row.status == "abierta"),
            "show_financial": show_financial,
            "sale": total_sale,
            "cost": total_cost,
            "margin": (total_sale - total_cost) if show_financial else None,
            "average_delivery_days": (
                round(sum(delivery_days) / len(delivery_days), 1)
                if delivery_days else None
            ),
        }

    return {
        "areas": areas,
        "sections": sections,
        "start_date": start_date,
        "end_date": end_date,
    }


def export_dataset(report, start_date, end_date):
    if report not in allowed_export_reports():
        return None

    start_dt, end_dt = date_bounds(start_date, end_date)

    if report == "sales":
        headers = ["Venta", "Fecha", "Cliente", "Asesor", "Total", "Pagado", "Saldo", "Estado"]
        rows = [
            [
                row.sale_no,
                row.sale_date,
                row.client.business_name,
                row.advisor.user.name if row.advisor and row.advisor.user else "",
                row.total,
                row.amount_paid,
                row.balance,
                row.status,
            ]
            for row in sale_query().filter(
                Sale.sale_date >= start_date,
                Sale.sale_date <= end_date,
            ).order_by(Sale.sale_date.desc()).all()
        ]

    elif report == "payments":
        headers = ["Fecha", "Cliente", "Venta", "Monto", "Método", "Referencia", "Estado"]
        rows = [
            [
                row.effective_date,
                row.client.business_name,
                row.sale.sale_no if row.sale else "",
                row.amount,
                row.method,
                row.reference,
                row.status,
            ]
            for row in payment_query().filter(
                Payment.effective_date >= start_date,
                Payment.effective_date <= end_date,
            ).order_by(Payment.effective_date.desc()).all()
        ]

    elif report == "expenses":
        headers = ["Fecha", "Categoría", "Beneficiario", "Descripción", "Monto", "Método", "Estado"]
        rows = [
            [row.expense_date, row.category, row.beneficiary, row.description, row.amount, row.method, row.status]
            for row in Expense.query.filter(
                Expense.expense_date >= start_date,
                Expense.expense_date <= end_date,
            ).order_by(Expense.expense_date.desc()).all()
        ]

    elif report == "receivables":
        headers = ["Cliente", "Venta", "Total", "Pagado", "Saldo", "Vence", "Estado"]
        rows = [
            [
                row.client.business_name,
                row.sale.sale_no if row.sale else "",
                row.total_amount,
                row.paid_amount,
                max(Decimal("0"), _d(row.total_amount) - _d(row.paid_amount)),
                row.due_date,
                row.status,
            ]
            for row in AccountReceivable.query.order_by(AccountReceivable.due_date.asc()).all()
        ]

    elif report == "payables":
        headers = ["Proveedor", "Concepto", "Monto", "Pagado", "Saldo", "Vence", "Estado"]
        rows = [
            [
                row.provider,
                row.concept,
                row.amount,
                row.paid_amount,
                max(Decimal("0"), _d(row.amount) - _d(row.paid_amount)),
                row.due_date,
                row.status,
            ]
            for row in Payable.query.order_by(Payable.due_date.asc()).all()
        ]

    elif report == "attendance":
        headers = ["Fecha/hora", "Colaborador", "Tipo", "Corregida", "Nota"]
        rows = [
            [
                row.marked_at,
                row.collaborator.user.name if row.collaborator and row.collaborator.user else "",
                row.mark_type,
                "Sí" if row.corrected else "No",
                row.note,
            ]
            for row in AttendanceMark.query.filter(
                AttendanceMark.marked_at >= start_dt,
                AttendanceMark.marked_at <= end_dt,
            ).order_by(AttendanceMark.marked_at.desc()).all()
        ]

    elif report == "leaves":
        headers = ["Colaborador", "Tipo", "Desde", "Hasta", "Días", "Estado", "Motivo"]
        rows = [
            [
                row.collaborator.user.name if row.collaborator and row.collaborator.user else "",
                row.leave_type,
                row.start_date,
                row.end_date,
                row.days,
                row.status,
                row.reason,
            ]
            for row in LeaveRequest.query.filter(
                LeaveRequest.start_date <= end_date,
                LeaveRequest.end_date >= start_date,
            ).order_by(LeaveRequest.start_date.desc()).all()
        ]

    elif report == "prospects":
        headers = ["Código", "Negocio", "Contacto", "Etapa", "Responsable", "Creado"]
        rows = [
            [
                row.code,
                row.business_name,
                row.contact_name,
                row.pipeline_stage,
                row.owner.user.name if row.owner and row.owner.user else "",
                row.created_at,
            ]
            for row in client_query().filter(
                Client.record_type == "seguimiento",
                Client.created_at >= start_dt,
                Client.created_at <= end_dt,
            ).order_by(Client.created_at.desc()).all()
        ]

    elif report == "clients":
        headers = ["Código", "Negocio", "Contacto", "Estado", "Responsable", "Creado"]
        rows = [
            [
                row.code,
                row.business_name,
                row.contact_name,
                row.client_status,
                row.owner.user.name if row.owner and row.owner.user else "",
                row.created_at,
            ]
            for row in client_query().filter(
                Client.record_type == "cliente",
                Client.created_at >= start_dt,
                Client.created_at <= end_dt,
            ).order_by(Client.created_at.desc()).all()
        ]

    elif report == "renewals":
        headers = ["Cliente", "Tipo", "Vence", "Estado", "Último contacto", "Notas"]
        rows = [
            [
                row.client.business_name,
                row.renewal_type,
                row.due_date,
                row.status,
                row.last_contact_at,
                row.notes,
            ]
            for row in renewal_query().filter(
                Renewal.due_date >= start_date,
                Renewal.due_date <= end_date,
            ).order_by(Renewal.due_date.asc()).all()
        ]

    elif report == "projects":
        headers = ["Proyecto", "Cliente", "Área", "Estado", "Progreso", "Inicio", "Vence", "Completado"]
        rows = [
            [
                row.name,
                row.client.business_name,
                row.department,
                row.status,
                row.progress,
                row.starts_on,
                row.due_on,
                row.completed_on,
            ]
            for row in project_query().filter(
                Project.created_at >= start_dt,
                Project.created_at <= end_dt,
            ).order_by(Project.created_at.desc()).all()
        ]

    elif report == "tasks":
        headers = ["Tarea", "Cliente", "Proyecto", "Responsable", "Prioridad", "Estado", "Vence"]
        rows = [
            [
                row.title,
                row.client.business_name if row.client else "",
                row.project.name if row.project else "",
                row.assignee.user.name if row.assignee and row.assignee.user else "",
                row.priority,
                row.status,
                row.due_at,
            ]
            for row in task_query().filter(
                Task.created_at >= start_dt,
                Task.created_at <= end_dt,
            ).order_by(Task.created_at.desc()).all()
        ]

    elif report == "printing":
        show_financial = (
            current_user.has_permission("finance.view")
            or role_name() in {"superadmin", "admin", "manager", "audit"}
        )
        orders = print_query().filter(
            PrintOrder.created_at >= start_dt,
            PrintOrder.created_at <= end_dt,
        ).order_by(PrintOrder.created_at.desc()).all()
        if show_financial:
            headers = ["Orden", "Cliente", "Estado", "Proveedor", "Costo", "Venta", "Margen", "Creada"]
            rows = [
                [
                    row.order_no,
                    row.client.business_name,
                    row.status,
                    row.provider,
                    row.total_cost,
                    row.total_sale,
                    _d(row.total_sale) - _d(row.total_cost),
                    row.created_at,
                ]
                for row in orders
            ]
        else:
            headers = ["Orden", "Cliente", "Estado", "Proveedor", "Creada"]
            rows = [
                [
                    row.order_no,
                    row.client.business_name,
                    row.status,
                    row.provider,
                    row.created_at,
                ]
                for row in orders
            ]

    else:
        return None

    return {
        "headers": headers,
        "rows": rows,
        "filename": f"reporte_{report}_{start_date.isoformat()}_{end_date.isoformat()}.csv",
    }
