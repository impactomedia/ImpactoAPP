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
    Commission,
    Expense,
    Interaction,
    LeaveRequest,
    Payable,
    Payment,
    Project,
    Quote,
    Renewal,
    Sale,
    SalesGoal,
    SupportTicket,
    Task,
)


CLOSED_PROJECTS = {"completado", "cancelado"}
CLOSED_TASKS = {"completada", "cancelada"}
CLOSED_TICKETS = {"resuelto", "cerrado"}


def _d(value):
    return Decimal(str(value or 0))


def _month_window(today=None):
    today = today or date.today()
    start = today.replace(day=1)
    if start.month == 12:
        nxt = date(start.year + 1, 1, 1)
    else:
        nxt = date(start.year, start.month + 1, 1)
    end = nxt - timedelta(days=1)
    return start, end


def _dt_bounds(start, end):
    return (
        datetime.combine(start, time.min),
        datetime.combine(end, time.max),
    )


def _management_dashboard():
    today = date.today()
    start, end = _month_window(today)
    start_dt, end_dt = _dt_bounds(start, end)
    now = datetime.utcnow()

    sales = Sale.query.filter(Sale.sale_date >= start, Sale.sale_date <= end).all()
    payments = Payment.query.filter(
        Payment.status == "confirmado",
        Payment.effective_date >= start,
        Payment.effective_date <= end,
    ).all()
    expenses = Expense.query.filter(
        Expense.status == "pagado",
        Expense.expense_date >= start,
        Expense.expense_date <= end,
    ).all()

    receivables = AccountReceivable.query.filter(
        AccountReceivable.status.notin_(["pagado", "cancelado"])
    ).all()
    payables = Payable.query.filter(
        Payable.status.notin_(["pagado", "cancelado"])
    ).all()

    sales_by_advisor = defaultdict(lambda: {"count": 0, "total": Decimal("0")})
    for sale in sales:
        name = (
            sale.advisor.user.name
            if sale.advisor and sale.advisor.user
            else "Sin asesor"
        )
        sales_by_advisor[name]["count"] += 1
        sales_by_advisor[name]["total"] += _d(sale.total)

    portfolio_total = Client.query.filter(
        Client.record_type.in_(["seguimiento", "cliente"])
    ).count()
    converted_total = Client.query.filter(Client.record_type == "cliente").count()
    conversion = round((converted_total / portfolio_total) * 100, 1) if portfolio_total else 0

    upcoming_leave = (
        LeaveRequest.query
        .filter(
            LeaveRequest.status == "aprobada",
            LeaveRequest.start_date >= today,
            LeaveRequest.start_date <= today + timedelta(days=30),
        )
        .order_by(LeaveRequest.start_date.asc())
        .limit(8)
        .all()
    )

    attendance_today = (
        db.session.query(AttendanceMark.collaborator_id)
        .filter(
            AttendanceMark.mark_type == "entrada",
            AttendanceMark.marked_at >= datetime.combine(today, time.min),
            AttendanceMark.marked_at <= datetime.combine(today, time.max),
        )
        .distinct()
        .count()
    )

    metrics = {
        "period_label": start.strftime("%m/%Y"),
        "income": sum((_d(row.amount) for row in payments), Decimal("0")),
        "expenses": sum((_d(row.amount) for row in expenses), Decimal("0")),
        "sales_total": sum((_d(row.total) for row in sales), Decimal("0")),
        "sales_count": len(sales),
        "receivable": sum(
            (max(Decimal("0"), _d(row.total_amount) - _d(row.paid_amount)) for row in receivables),
            Decimal("0"),
        ),
        "payable": sum(
            (max(Decimal("0"), _d(row.amount) - _d(row.paid_amount)) for row in payables),
            Decimal("0"),
        ),
        "active_clients": Client.query.filter_by(record_type="cliente", client_status="activo").count(),
        "new_clients": Client.query.filter(
            Client.record_type == "cliente",
            Client.created_at >= start_dt,
            Client.created_at <= end_dt,
        ).count(),
        "renewals_30": Renewal.query.filter(
            Renewal.status.in_(["pendiente", "contactado"]),
            Renewal.due_date >= today,
            Renewal.due_date <= today + timedelta(days=30),
        ).count(),
        "pipeline_conversion": conversion,
        "late_projects": Project.query.filter(
            Project.due_on.isnot(None),
            Project.due_on < today,
            Project.status.notin_(CLOSED_PROJECTS),
        ).count(),
        "overdue_tasks": Task.query.filter(
            Task.due_at.isnot(None),
            Task.due_at < now,
            Task.status.notin_(CLOSED_TASKS),
        ).count(),
        "attendance_today": attendance_today,
        "upcoming_leave_count": len(upcoming_leave),
    }

    return {
        "kind": "management",
        "title": "Dashboard gerencial",
        "subtitle": "Indicadores ejecutivos del mes actual.",
        "metrics": metrics,
        "sales_by_advisor": sorted(
            (
                {"name": name, "count": values["count"], "total": values["total"]}
                for name, values in sales_by_advisor.items()
            ),
            key=lambda row: row["total"],
            reverse=True,
        )[:8],
        "upcoming_leave": upcoming_leave,
    }


def _advisor_dashboard():
    today = date.today()
    start, end = _month_window(today)
    start_dt, end_dt = _dt_bounds(start, end)
    collaborator = current_user.collaborator
    if not collaborator:
        return {"kind": "advisor", "title": "Dashboard del asesor", "metrics": {}}

    owned = Client.query.filter(Client.owner_id == collaborator.id)
    followups = owned.filter(Client.record_type == "seguimiento").all()

    pipeline = defaultdict(int)
    for row in followups:
        pipeline[row.pipeline_stage or "sin_etapa"] += 1

    latest_followups = {}
    rows = (
        Interaction.query
        .join(Client, Interaction.client_id == Client.id)
        .filter(
            Client.owner_id == collaborator.id,
            Interaction.next_followup_at.isnot(None),
        )
        .order_by(Interaction.occurred_at.desc(), Interaction.id.desc())
        .all()
    )
    for row in rows:
        latest_followups.setdefault(row.client_id, row)

    followup_stats = {"overdue": 0, "today": 0, "upcoming": 0}
    for row in latest_followups.values():
        due = row.next_followup_at.date()
        if due < today:
            followup_stats["overdue"] += 1
        elif due == today:
            followup_stats["today"] += 1
        else:
            followup_stats["upcoming"] += 1

    sales = Sale.query.filter(
        or_(
            Sale.advisor_id == collaborator.id,
            Sale.client.has(Client.owner_id == collaborator.id),
        ),
        Sale.sale_date >= start,
        Sale.sale_date <= end,
    ).all()

    payments = Payment.query.filter(
        Payment.status == "confirmado",
        Payment.effective_date >= start,
        Payment.effective_date <= end,
        or_(
            Payment.sale.has(Sale.advisor_id == collaborator.id),
            Payment.client.has(Client.owner_id == collaborator.id),
        ),
    ).all()

    goals = SalesGoal.query.filter(
        SalesGoal.collaborator_id == collaborator.id,
        SalesGoal.period_start <= end,
        SalesGoal.period_end >= start,
    ).all()
    goal_amount = sum((_d(row.target_amount) for row in goals), Decimal("0"))
    sales_amount = sum((_d(row.total) for row in sales), Decimal("0"))

    commissions = Commission.query.filter(
        Commission.advisor_id == collaborator.id,
        Commission.created_at >= start_dt,
        Commission.created_at <= end_dt,
        Commission.status != "anulada",
    ).all()

    renewals = (
        Renewal.query
        .filter(
            Renewal.client.has(Client.owner_id == collaborator.id),
            Renewal.status.in_(["pendiente", "contactado"]),
            Renewal.due_date <= today + timedelta(days=60),
        )
        .order_by(Renewal.due_date.asc())
        .limit(8)
        .all()
    )

    quotes_pending = Quote.query.filter(
        or_(
            Quote.advisor_id == collaborator.id,
            Quote.client.has(Client.owner_id == collaborator.id),
        ),
        Quote.status.in_(["borrador", "enviada"]),
    ).count()

    return {
        "kind": "advisor",
        "title": "Dashboard del asesor",
        "subtitle": "Tu cartera, seguimiento, ventas y renovaciones.",
        "metrics": {
            "new_prospects": owned.filter(
                Client.record_type == "seguimiento",
                Client.created_at >= start_dt,
                Client.created_at <= end_dt,
            ).count(),
            "followup_overdue": followup_stats["overdue"],
            "followup_today": followup_stats["today"],
            "followup_upcoming": followup_stats["upcoming"],
            "quotes_pending": quotes_pending,
            "sales_count": len(sales),
            "sales_total": sales_amount,
            "collected": sum((_d(row.amount) for row in payments), Decimal("0")),
            "goal_amount": goal_amount,
            "goal_percent": round((float(sales_amount / goal_amount) * 100), 1) if goal_amount > 0 else None,
            "commission_estimated": sum(
                (_d(row.amount) for row in commissions if row.status == "estimada"),
                Decimal("0"),
            ),
            "commission_approved": sum(
                (_d(row.amount) for row in commissions if row.status == "aprobada"),
                Decimal("0"),
            ),
            "clients": owned.filter(Client.record_type == "cliente").count(),
            "renewals": len(renewals),
        },
        "pipeline": dict(pipeline),
        "renewals": renewals,
        "followups": sorted(
            latest_followups.values(),
            key=lambda row: row.next_followup_at,
        )[:8],
    }


def _production_dashboard():
    today = date.today()
    now = datetime.utcnow()
    week_end = today + timedelta(days=7)
    collaborator = current_user.collaborator
    if not collaborator:
        return {"kind": "production", "title": "Dashboard operativo", "metrics": {}}

    project_query = Project.query.filter(
        or_(
            Project.coordinator_id == collaborator.id,
            Project.members.any(id=collaborator.id),
        )
    )
    task_query = Task.query.filter(
        or_(
            Task.assignee_id == collaborator.id,
            Task.collaborators.any(id=collaborator.id),
        )
    )
    open_tasks = task_query.filter(Task.status.notin_(CLOSED_TASKS))

    due_today = open_tasks.filter(
        Task.due_at >= datetime.combine(today, time.min),
        Task.due_at <= datetime.combine(today, time.max),
    ).count()
    due_week = open_tasks.filter(
        Task.due_at >= datetime.combine(today, time.min),
        Task.due_at <= datetime.combine(week_end, time.max),
    ).count()

    waiting_delivery_projects = project_query.filter(
        Project.status.in_(["revision_interna", "aprobacion_cliente", "correcciones"])
    ).all()
    waiting_client = project_query.filter(Project.status == "esperando_cliente").count()
    blocked_tasks = open_tasks.filter(Task.status == "bloqueada").count()

    tickets = SupportTicket.query.filter(
        SupportTicket.responsible_id == collaborator.id,
        SupportTicket.status.notin_(CLOSED_TICKETS),
    ).count()

    upcoming = (
        open_tasks
        .filter(Task.due_at.isnot(None))
        .order_by(Task.due_at.asc())
        .limit(10)
        .all()
    )

    return {
        "kind": "production",
        "title": "Dashboard operativo",
        "subtitle": "Tus proyectos, entregas, bloqueos y solicitudes.",
        "metrics": {
            "projects": project_query.filter(Project.status.notin_(CLOSED_PROJECTS)).count(),
            "tasks": open_tasks.count(),
            "due_today": due_today,
            "due_week": due_week,
            "overdue": open_tasks.filter(Task.due_at.isnot(None), Task.due_at < now).count(),
            "waiting_delivery": len({row.client_id for row in waiting_delivery_projects}),
            "blocked": blocked_tasks + waiting_client,
            "tickets": tickets,
        },
        "upcoming_tasks": upcoming,
    }


def build_role_dashboard():
    role = current_user.role.name if current_user.role else ""
    if role in {"superadmin", "admin", "manager"}:
        return _management_dashboard()
    if role == "advisor":
        return _advisor_dashboard()
    if role == "production":
        return _production_dashboard()
    return None
