"""Cálculo financiero comprobable en USD por proyecto y quincena.

Fuente de ingresos de ventas: Payment confirmado (no Sale.total ni Income espejo).
Fuente de egresos nuevos: FinanceProjectMovement ejecutado.
Nunca se cambia una quincena anterior al registrar un gasto en otra.
"""
import calendar
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from flask import current_app
from sqlalchemy import and_, or_

from app.extensions import db
from app.models import (
    AdvisorProject, Client, Collaborator, Expense, Income, Payment, Sale, PayrollLine, PayrollPeriod,
)
from app.finance_control_models import (
    AdvisorFortnightGoal, AdvisorFortnightPayout,
    FinanceProjectAllocation, FinanceProjectMovement,
)


def money(value):
    return Decimal(str(value or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def local_today():
    try:
        return datetime.now(ZoneInfo(current_app.config.get("COMPANY_TIMEZONE") or "America/Managua")).date()
    except (KeyError, ValueError):
        return date.today()


def fortnight_bounds(day):
    last = calendar.monthrange(day.year, day.month)[1]
    if day.day <= 15:
        return date(day.year, day.month, 1), date(day.year, day.month, 15)
    return date(day.year, day.month, 16), date(day.year, day.month, last)


def is_commercial_advisor(collaborator):
    return bool(
        collaborator and collaborator.user and collaborator.user.role
        and collaborator.user.role.name == "advisor"
        and collaborator.advisor_project_id
    )


def project_of_sale(sale):
    advisor = sale.advisor or (sale.client.owner if sale.client else None)
    return advisor.advisor_project_id if advisor else None


def payments_for_advisor(advisor_id, starts, ends):
    return Payment.query.join(Sale, Payment.sale_id == Sale.id).filter(
        Payment.status == "confirmado",
        Payment.currency == "USD",
        Payment.effective_date >= starts,
        Payment.effective_date <= ends,
        or_(
            Sale.advisor_id == advisor_id,
            and_(
                Sale.advisor_id.is_(None),
                Sale.client.has(Client.owner_id == advisor_id),
            ),
        ),
    ).all()


def commission_expenses(advisor_id, project_id, starts, ends):
    return FinanceProjectMovement.query.join(
        Sale, FinanceProjectMovement.sale_id == Sale.id
    ).filter(
        FinanceProjectMovement.project_id == project_id,
        FinanceProjectMovement.status == "ejecutado",
        FinanceProjectMovement.category.in_(("imprenta", "meta_ads")),
        FinanceProjectMovement.executed_on >= starts,
        FinanceProjectMovement.executed_on <= ends,
        or_(
            Sale.advisor_id == advisor_id,
            and_(
                Sale.advisor_id.is_(None),
                Sale.client.has(Client.owner_id == advisor_id),
            ),
        ),
    ).all()


def new_client_categories(advisor_id, starts, ends):
    """Primera compra pagada con servicio: un cliente nuevo, incluso si adquiere un combo.

    Propinas solas no cuentan como clientes nuevos. Una futura compra real sí cuenta.
    """
    sales = Sale.query.filter(
        Sale.sale_date <= ends,
        or_(
            Sale.advisor_id == advisor_id,
            and_(Sale.advisor_id.is_(None), Sale.client.has(Client.owner_id == advisor_id)),
        ),
    ).order_by(Sale.sale_date.asc(), Sale.id.asc()).all()
    first_valid, tip_clients = set(), set()
    counts = {"membresia": 0, "accesorios": 0, "propinas": 0}
    for sale in sales:
        if sale.status in {"cancelada", "anulada"}:
            continue
        payments = [
            p for p in sale.payments if p.status == "confirmado" and p.currency == "USD"
        ]
        if not payments:
            continue
        earliest = min(p.effective_date for p in payments)
        if earliest > ends:
            continue
        memberships, accessories, tips = False, False, False
        for item in sale.items:
            title = " ".join(filter(None, (
                getattr(item.product, "name", ""), item.description
            ))).lower().strip()
            if "propina" in title or title in {"tip", "tips", "gratuity"}:
                tips = True
                continue
            if item.product and (item.product.category in {
                "paquete", "community_manager",
            } or "community manager" in title):
                memberships = True
            elif title:
                accessories = True
        if memberships or accessories:
            if sale.client_id not in first_valid:
                first_valid.add(sale.client_id)
                if starts <= earliest <= ends:
                    counts["membresia" if memberships else "accesorios"] += 1
        elif tips and starts <= earliest <= ends:
            tip_clients.add(sale.client_id)
    counts["propinas"] = len(tip_clients)
    counts["total"] = counts["membresia"] + counts["accesorios"]
    return counts


def qualifying_new_clients(advisor_id, starts, ends):
    return new_client_categories(advisor_id, starts, ends)["total"]


def fortnight_summary(advisor, period_start=None, today=None):
    today = today or local_today()
    starts, ends = fortnight_bounds(period_start or today)
    goal = AdvisorFortnightGoal.query.filter_by(advisor_id=advisor.id, period_start=starts).first()
    project_id = advisor.advisor_project_id
    payments = payments_for_advisor(advisor.id, starts, ends)
    deductions = commission_expenses(advisor.id, project_id, starts, ends) if project_id else []
    gross = money(sum((money(p.amount) for p in payments), Decimal("0")))
    deduction = money(sum((money(e.amount) for e in deductions), Decimal("0")))
    net = gross - deduction
    total_goal = money(goal.total_goal) if goal else Decimal("0")
    net_goal = money(goal.net_goal) if goal else Decimal("0")
    percentage = Decimal(str(goal.percentage or 0)) if goal else Decimal("0")
    eligible = bool(goal and net_goal > 0 and net >= net_goal)
    earned = money(net * percentage / 100) if eligible else money(0)
    paid = money(sum((
        money(row.amount) for row in AdvisorFortnightPayout.query.filter_by(
            advisor_id=advisor.id, period_start=starts
        ).all()
    ), Decimal("0")))
    remaining_days = max(0, (ends - today).days + 1) if starts <= today <= ends else 0
    gross_left = max(money(0), total_goal - gross)
    net_left = max(money(0), net_goal - net)
    client_counts = new_client_categories(advisor.id, starts, ends)
    return {
        "advisor": advisor, "project_id": project_id, "goal": goal,
        "starts": starts, "ends": ends, "gross": gross, "net": net,
        "deductions": deduction, "print_deductions": money(sum((money(e.amount) for e in deductions if e.category == "imprenta"), Decimal("0"))),
        "ads_deductions": money(sum((money(e.amount) for e in deductions if e.category == "meta_ads"), Decimal("0"))),
        "total_goal": total_goal, "net_goal": net_goal,
        "gross_left": gross_left, "net_left": net_left,
        "gross_progress": min(100, float(gross / total_goal * 100)) if total_goal > 0 else 0,
        "net_progress": min(100, float(net / net_goal * 100)) if net_goal > 0 else 0,
        "percentage": percentage, "eligible": eligible, "earned": earned, "paid": paid,
        "days_remaining": remaining_days,
        "daily_required": money((gross_left / remaining_days).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)) if remaining_days else money(0),
        "new_clients": client_counts["total"],
        "new_memberships": client_counts["membresia"],
        "new_accessories": client_counts["accesorios"],
        "tip_only_clients": client_counts["propinas"],
    }


def adjustment_balance(advisor, before):
    """Sobrepagos previos se compensan sin reabrir ni alterar quincenas cerradas."""
    goals = AdvisorFortnightGoal.query.filter(
        AdvisorFortnightGoal.advisor_id == advisor.id,
        AdvisorFortnightGoal.period_start < before,
    ).order_by(AdvisorFortnightGoal.period_start.asc()).all()
    total_earned = money(0)
    for goal in goals:
        total_earned += fortnight_summary(advisor, goal.period_start)["earned"]
    old_payouts = AdvisorFortnightPayout.query.filter(
        AdvisorFortnightPayout.advisor_id == advisor.id,
        AdvisorFortnightPayout.period_start < before,
    ).all()
    total_paid = sum((money(x.amount) for x in old_payouts), money(0))
    return max(money(0), total_paid - total_earned)


def available_payout(summary):
    if not summary["eligible"]:
        return money(0)
    balance = adjustment_balance(summary["advisor"], summary["starts"])
    return max(money(0), summary["earned"] - summary["paid"] - balance)


def monthly_ledger(year, month):
    """Incluye registros existentes sin duplicar Income automático derivado de Payment."""
    start = date(year, month, 1)
    end = date(year, month, calendar.monthrange(year, month)[1])
    allocations = {
        (a.record_type, a.record_id): a.project_id
        for a in FinanceProjectAllocation.query.all()
    }
    entries = []
    for payment in Payment.query.filter(
        Payment.effective_date >= start, Payment.effective_date <= end,
        Payment.status == "confirmado",
    ).all():
        entries.append({
            "on": payment.effective_date, "type": "ingreso", "source": "Cobro",
            "project_id": project_of_sale(payment.sale),
            "description": payment.sale.sale_no if payment.sale else "Cobro",
            "amount": money(payment.amount), "currency": payment.currency,
        })
    for income in Income.query.filter(
        Income.sale_id.is_(None),
        Income.effective_date >= start, Income.effective_date <= end,
        Income.status == "confirmado",
    ).all():
        entries.append({
            "on": income.effective_date, "type": "ingreso", "source": "Ingreso manual",
            "project_id": allocations.get(("income", income.id)),
            "description": income.concept, "amount": money(income.amount),
            "currency": income.currency,
        })
    for expense in Expense.query.filter(
        Expense.expense_date >= start, Expense.expense_date <= end,
        Expense.status == "pagado",
    ).all():
        entries.append({
            "on": expense.expense_date, "type": "egreso", "source": "Egreso histórico",
            "project_id": allocations.get(("expense", expense.id)),
            "description": expense.description, "amount": money(expense.amount),
            "currency": expense.currency,
        })
    for movement in FinanceProjectMovement.query.filter(
        FinanceProjectMovement.executed_on >= start,
        FinanceProjectMovement.executed_on <= end,
        FinanceProjectMovement.status == "ejecutado",
    ).all():
        entries.append({
            "on": movement.executed_on, "type": "egreso",
            "source": movement.category.replace("_", " ").title(),
            "project_id": movement.project_id, "description": movement.description,
            "amount": money(movement.amount), "currency": movement.currency,
        })
    # Comisiones desembolsadas, sin registrar otro Expense duplicado.
    for payout in AdvisorFortnightPayout.query.filter(
        AdvisorFortnightPayout.paid_on >= start,
        AdvisorFortnightPayout.paid_on <= end,
    ).all():
        entries.append({
            "on": payout.paid_on, "type": "egreso", "source": "Comisión quincenal pagada",
            "project_id": payout.project_id,
            "description": payout.advisor.user.name if payout.advisor and payout.advisor.user else "Asesor",
            "amount": money(payout.amount), "currency": "USD",
        })
    # La planilla se contabiliza únicamente cuando aparece como pagada.
    # Los movimientos de personal no adscrito a un proyecto quedan sin asignar.
    for line in PayrollLine.query.join(
        PayrollPeriod, PayrollLine.period_id == PayrollPeriod.id
    ).filter(
        PayrollPeriod.status == "pagada",
        PayrollPeriod.paid_at.is_not(None),
        PayrollPeriod.paid_at >= datetime.combine(start, datetime.min.time()),
        PayrollPeriod.paid_at <= datetime.combine(end, datetime.max.time()),
    ).all():
        entries.append({
            "on": line.period.paid_at.date(), "type": "egreso", "source": "Planilla pagada",
            "project_id": allocations.get(("payroll_line", line.id))
                or (line.collaborator.advisor_project_id if line.collaborator else None),
            "description": line.collaborator.user.name if line.collaborator and line.collaborator.user else "Colaborador",
            "amount": money(line.total), "currency": "USD",
        })
    entries.sort(key=lambda row: row["on"], reverse=True)
    result = {}
    for project in AdvisorProject.query.order_by(AdvisorProject.name).all():
        result[project.id] = {
            "project": project, "income": money(0), "expenses": money(0),
            "result": money(0), "entries": [],
        }
    result[None] = {
        "project": None, "income": money(0), "expenses": money(0),
        "result": money(0), "entries": [],
    }
    unconverted_nio = {"income": money(0), "expenses": money(0)}
    for entry in entries:
        if entry["project_id"] not in result:
            entry["project_id"] = None
        bucket = result[entry["project_id"]]
        bucket["entries"].append(entry)
        if entry["currency"] != "USD":
            unconverted_nio["income" if entry["type"] == "ingreso" else "expenses"] += entry["amount"]
            continue
        field = "income" if entry["type"] == "ingreso" else "expenses"
        bucket[field] += entry["amount"]
    for row in result.values():
        row["result"] = row["income"] - row["expenses"]
    return result, entries, unconverted_nio


def legacy_allocation_context():
    projects = AdvisorProject.query.order_by(AdvisorProject.name).all()
    lookup = {p.id: p for p in projects}
    allocations = {
        (a.record_type, a.record_id): lookup.get(a.project_id)
        for a in FinanceProjectAllocation.query.all()
    }
    return {"finance_projects": projects, "allocations": allocations}
