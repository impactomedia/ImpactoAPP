from __future__ import annotations

import calendar
import json
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
from decimal import Decimal, InvalidOperation

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import or_

from app.catalog_runtime import save_system_setting, system_setting
from app.extensions import db
from app.helpers import audit
from app.models import (
    AttendanceMark,
    Client,
    Collaborator,
    Expense,
    Interaction,
    LeaveRequest,
    Payable,
    Project,
    Sale,
    ScheduleAssignment,
    SystemSetting,
    Task,
)
from app.security_controls import BUILTIN_ROLES, permission_scope


bp = Blueprint("planning", __name__, url_prefix="/planning")


def _role_name():
    return current_user.role.name if current_user.role else ""


def _has_any(*permissions):
    return any(current_user.has_permission(code) for code in permissions)


def _require_any(*permissions):
    if not _has_any(*permissions):
        abort(403)


def _period(raw=None):
    raw = (raw or "").strip()
    try:
        parsed = datetime.strptime(raw, "%Y-%m").date()
    except (TypeError, ValueError):
        parsed = date.today().replace(day=1)

    start = parsed.replace(day=1)
    last_day = calendar.monthrange(start.year, start.month)[1]
    end = start.replace(day=last_day)
    previous = (start - timedelta(days=1)).replace(day=1)
    next_month = (end + timedelta(days=1)).replace(day=1)
    weeks = calendar.Calendar(firstweekday=0).monthdatescalendar(start.year, start.month)

    return {
        "key": start.strftime("%Y-%m"),
        "start": start,
        "end": end,
        "previous": previous.strftime("%Y-%m"),
        "next": next_month.strftime("%Y-%m"),
        "label": start.strftime("%m/%Y"),
        "weeks": weeks,
    }


def _visible_crm_clients_query():
    query = Client.query
    role = _role_name()
    collaborator = current_user.collaborator

    if role not in BUILTIN_ROLES:
        if permission_scope(current_user, "crm.view") == "company":
            return query
        if collaborator:
            return query.filter(Client.owner_id == collaborator.id)
        return query.filter(Client.id == -1)

    if role == "advisor":
        if not collaborator:
            return query.filter(Client.id == -1)
        return query.filter(Client.owner_id == collaborator.id)

    if role == "supervisor":
        if not collaborator:
            return query.filter(Client.id == -1)
        ids = [collaborator.id] + [row.id for row in collaborator.subordinates]
        return query.filter(Client.owner_id.in_(ids))

    return query


def _visible_sales_collaborators():
    role = _role_name()
    collaborator = current_user.collaborator

    if role not in BUILTIN_ROLES:
        scope = (
            permission_scope(current_user, "sales.view")
            if current_user.has_permission("sales.view")
            else permission_scope(current_user, "crm.view")
        )
        if scope != "company":
            return [collaborator] if collaborator else []

    query = Collaborator.query.filter_by(status="activo")

    if role == "advisor":
        return [collaborator] if collaborator else []
    if role == "supervisor":
        if not collaborator:
            return []
        ids = [collaborator.id] + [row.id for row in collaborator.subordinates]
        query = query.filter(Collaborator.id.in_(ids))

    rows = query.order_by(Collaborator.id).all()
    return [
        row
        for row in rows
        if row.user and row.user.role and row.user.role.name == "advisor"
    ]


def _visible_hr_collaborators():
    collaborator = current_user.collaborator
    role = _role_name()

    if current_user.has_permission("hr.view"):
        if role in {"superadmin", "admin", "manager", "hr"}:
            return Collaborator.query.order_by(Collaborator.id).all()
        if role == "supervisor" and collaborator:
            ids = [collaborator.id] + [row.id for row in collaborator.subordinates]
            return (
                Collaborator.query
                .filter(Collaborator.id.in_(ids))
                .order_by(Collaborator.id)
                .all()
            )
        if role not in BUILTIN_ROLES:
            if permission_scope(current_user, "hr.view") == "company":
                return Collaborator.query.order_by(Collaborator.id).all()

    return [collaborator] if collaborator else []


def _visible_operations_queries():
    from app.blueprints.operations import _visible_projects_query, _visible_tasks_query

    projects_query = _visible_projects_query()
    tasks_query = _visible_tasks_query()

    role = _role_name()
    collaborator = current_user.collaborator

    if role not in BUILTIN_ROLES and collaborator:
        if (
            current_user.has_permission("projects.view")
            and permission_scope(current_user, "projects.view") != "company"
        ):
            projects_query = projects_query.filter(
                or_(
                    Project.coordinator_id == collaborator.id,
                    Project.members.any(id=collaborator.id),
                    Project.client.has(Client.owner_id == collaborator.id),
                )
            )

    return projects_query, tasks_query


def _company_timezone():
    try:
        return ZoneInfo(current_app.config.get("COMPANY_TIMEZONE", "America/Managua"))
    except Exception:
        return ZoneInfo("UTC")


def _attendance_local_dt(raw_utc):
    if raw_utc is None:
        return None
    aware_utc = raw_utc.replace(tzinfo=timezone.utc)
    return aware_utc.astimezone(_company_timezone()).replace(tzinfo=None)


def _load_hr_month_data(collaborators, period):
    ids = [row.id for row in collaborators]
    if not ids:
        return {}, {}, {}

    zone = _company_timezone()
    start_local = datetime.combine(period["start"], time.min).replace(tzinfo=zone)
    end_local = datetime.combine(
        period["end"] + timedelta(days=1),
        time.min,
    ).replace(tzinfo=zone)
    start_dt = start_local.astimezone(timezone.utc).replace(tzinfo=None)
    end_dt = end_local.astimezone(timezone.utc).replace(tzinfo=None)

    marks = (
        AttendanceMark.query
        .filter(
            AttendanceMark.collaborator_id.in_(ids),
            AttendanceMark.marked_at >= start_dt,
            AttendanceMark.marked_at < end_dt,
        )
        .order_by(AttendanceMark.marked_at)
        .all()
    )
    marks_by_day = defaultdict(list)
    for mark in marks:
        local_dt = _attendance_local_dt(mark.marked_at)
        marks_by_day[(mark.collaborator_id, local_dt.date())].append(
            (mark.mark_type, local_dt)
        )

    assignments = (
        ScheduleAssignment.query
        .filter(
            ScheduleAssignment.collaborator_id.in_(ids),
            ScheduleAssignment.starts_on <= period["end"],
            or_(
                ScheduleAssignment.ends_on.is_(None),
                ScheduleAssignment.ends_on >= period["start"],
            ),
        )
        .order_by(ScheduleAssignment.starts_on.desc(), ScheduleAssignment.id.desc())
        .all()
    )
    assignments_by_collab = defaultdict(list)
    for assignment in assignments:
        assignments_by_collab[assignment.collaborator_id].append(assignment)

    leaves = (
        LeaveRequest.query
        .filter(
            LeaveRequest.collaborator_id.in_(ids),
            LeaveRequest.start_date <= period["end"],
            LeaveRequest.end_date >= period["start"],
        )
        .order_by(LeaveRequest.start_date)
        .all()
    )
    leaves_by_collab = defaultdict(list)
    for leave in leaves:
        leaves_by_collab[leave.collaborator_id].append(leave)

    return marks_by_day, assignments_by_collab, leaves_by_collab


def _schedule_for_day(assignments, day):
    for assignment in assignments:
        if assignment.starts_on > day:
            continue
        if assignment.ends_on and assignment.ends_on < day:
            continue
        return assignment.schedule
    return None


def _approved_leave_for_day(leaves, day):
    for leave in leaves:
        if (
            leave.status == "aprobada"
            and leave.start_date <= day <= leave.end_date
        ):
            return leave
    return None


def _interval_minutes(marks, start_type, end_type):
    opened = None
    total = 0
    incomplete = False

    for mark_type, marked_at in marks:
        if mark_type == start_type:
            opened = marked_at
        elif mark_type == end_type and opened is not None:
            delta = marked_at - opened
            total += max(0, int(delta.total_seconds() // 60))
            opened = None

    if opened is not None:
        incomplete = True

    return total, incomplete


def _incident_key(collaborator_id, day, kind):
    return f"attendance_incident:{collaborator_id}:{day.isoformat()}:{kind}"


def _incident_state(collaborator_id, day, kind):
    raw = system_setting(_incident_key(collaborator_id, day, kind))
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {}


def _detect_incidents(collaborators, period):
    marks_by_day, assignments_by_collab, leaves_by_collab = _load_hr_month_data(
        collaborators,
        period,
    )
    incidents = []
    today = date.today()

    for collaborator in collaborators:
        day = period["start"]
        while day <= period["end"] and day <= today:
            schedule = _schedule_for_day(
                assignments_by_collab.get(collaborator.id, []),
                day,
            )
            if not schedule:
                day += timedelta(days=1)
                continue

            try:
                workdays = {
                    int(value)
                    for value in (schedule.workdays or "").split(",")
                    if value != ""
                }
            except ValueError:
                workdays = {0, 1, 2, 3, 4}

            if day.weekday() not in workdays:
                day += timedelta(days=1)
                continue

            if _approved_leave_for_day(
                leaves_by_collab.get(collaborator.id, []),
                day,
            ):
                day += timedelta(days=1)
                continue

            marks = marks_by_day.get((collaborator.id, day), [])
            entries = [marked_at for mark_type, marked_at in marks if mark_type == "entrada"]
            exits = [marked_at for mark_type, marked_at in marks if mark_type == "salida"]

            day_incidents = []

            if not entries:
                day_incidents.append(
                    ("ausencia_sin_marcacion", "No se registró entrada.")
                )
            else:
                if schedule.start_time:
                    programmed = datetime.combine(day, schedule.start_time)
                    tolerance = timedelta(
                        minutes=max(0, schedule.tolerance_minutes or 0)
                    )
                    if entries[0] > programmed + tolerance:
                        minutes = int(
                            (
                                entries[0]
                                - programmed
                                - tolerance
                            ).total_seconds()
                            // 60
                        )
                        day_incidents.append(
                            (
                                "tardanza",
                                f"Tardanza de {max(1, minutes)} min después de tolerancia.",
                            )
                        )

                if not exits:
                    day_incidents.append(
                        ("marcacion_incompleta", "Entrada sin marcación de salida.")
                    )
                elif schedule.end_time:
                    programmed_exit = datetime.combine(day, schedule.end_time)
                    if exits[-1] < programmed_exit - timedelta(minutes=5):
                        minutes = int(
                            (
                                programmed_exit - exits[-1]
                            ).total_seconds()
                            // 60
                        )
                        day_incidents.append(
                            (
                                "salida_anticipada",
                                f"Salida anticipada aproximadamente {max(1, minutes)} min.",
                            )
                        )

            break_minutes, break_incomplete = _interval_minutes(
                marks, "break_inicio", "break_fin"
            )
            lunch_minutes, lunch_incomplete = _interval_minutes(
                marks, "almuerzo_inicio", "almuerzo_fin"
            )

            if break_incomplete or lunch_incomplete:
                day_incidents.append(
                    (
                        "pausa_incompleta",
                        "Existe un break o almuerzo sin marcación de cierre.",
                    )
                )

            allowed_break = max(0, schedule.break_minutes or 0)
            if allowed_break and break_minutes > allowed_break:
                day_incidents.append(
                    (
                        "break_excesivo",
                        f"Break: {break_minutes} min / permitido {allowed_break} min.",
                    )
                )

            allowed_lunch = max(0, schedule.lunch_minutes or 0)
            if allowed_lunch and lunch_minutes > allowed_lunch:
                day_incidents.append(
                    (
                        "almuerzo_excesivo",
                        f"Almuerzo: {lunch_minutes} min / esperado {allowed_lunch} min.",
                    )
                )

            for kind, detail in day_incidents:
                state = _incident_state(collaborator.id, day, kind)
                incidents.append(
                    {
                        "collaborator": collaborator,
                        "day": day,
                        "kind": kind,
                        "detail": detail,
                        "state": state.get("status", "pendiente"),
                        "reason": state.get("reason"),
                    }
                )

            day += timedelta(days=1)

    incidents.sort(
        key=lambda row: (row["day"], row["collaborator"].id, row["kind"]),
        reverse=True,
    )
    return incidents


def _overtime_key(collaborator_id, day):
    return f"overtime:{collaborator_id}:{day.isoformat()}"


def _overtime_state(collaborator_id, day):
    raw = system_setting(_overtime_key(collaborator_id, day))
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {}


def _detect_overtime(collaborators, period):
    marks_by_day, assignments_by_collab, _ = _load_hr_month_data(
        collaborators,
        period,
    )
    rows = []

    for collaborator in collaborators:
        day = period["start"]
        while day <= period["end"] and day <= date.today():
            schedule = _schedule_for_day(
                assignments_by_collab.get(collaborator.id, []),
                day,
            )
            marks = marks_by_day.get((collaborator.id, day), [])

            if schedule and schedule.end_time:
                exits = [marked_at for mark_type, marked_at in marks if mark_type == "salida"]
                if exits:
                    scheduled_end = datetime.combine(day, schedule.end_time)
                    detected = max(
                        0,
                        int(
                            (
                                exits[-1] - scheduled_end
                            ).total_seconds()
                            // 60
                        ),
                    )
                    if detected > 0:
                        state = _overtime_state(collaborator.id, day)
                        rows.append(
                            {
                                "collaborator": collaborator,
                                "day": day,
                                "detected_minutes": detected,
                                "status": state.get("status", "pendiente"),
                                "approved_minutes": state.get("approved_minutes"),
                                "reason": state.get("reason"),
                            }
                        )

            day += timedelta(days=1)

    rows.sort(
        key=lambda row: (row["day"], row["collaborator"].id),
        reverse=True,
    )
    return rows


def _can_manage_hr_row(collaborator):
    role = _role_name()
    if role in {"superadmin", "admin", "manager", "hr"}:
        return True
    if role == "supervisor" and current_user.collaborator:
        allowed = {
            current_user.collaborator.id,
            *[row.id for row in current_user.collaborator.subordinates],
        }
        return collaborator.id in allowed
    return False


@bp.route("/")
@login_required
def index():
    return render_template(
        "planning/index.html",
        can_crm=current_user.has_permission("crm.view"),
        can_goals=_has_any("crm.view", "sales.view"),
        can_hr=bool(current_user.collaborator or current_user.has_permission("hr.view")),
        can_finance=current_user.has_permission("finance.view"),
        can_operations=_has_any("projects.view", "tasks.view"),
    )


@bp.route("/crm-agenda")
@login_required
def crm_agenda():
    _require_any("crm.view")
    period = _period(request.args.get("period"))
    start_dt = datetime.combine(period["start"], time.min)
    end_dt = datetime.combine(period["end"] + timedelta(days=1), time.min)

    client_ids = [
        row[0]
        for row in _visible_crm_clients_query()
        .with_entities(Client.id)
        .all()
    ]

    interactions = (
        Interaction.query
        .filter(
            Interaction.client_id.in_(client_ids or [-1]),
            Interaction.next_followup_at.isnot(None),
            Interaction.next_followup_at >= start_dt,
            Interaction.next_followup_at < end_dt,
        )
        .order_by(Interaction.next_followup_at)
        .all()
    )

    today = date.today()
    rows = []
    for interaction in interactions:
        follow_day = interaction.next_followup_at.date()
        state = (
            "vencido"
            if follow_day < today
            else "hoy"
            if follow_day == today
            else "proximo"
        )
        rows.append({"interaction": interaction, "state": state})

    return render_template(
        "planning/crm_agenda.html",
        period=period,
        rows=rows,
    )


@bp.route("/sales-goals", methods=["GET", "POST"])
@login_required
def sales_goals():
    _require_any("crm.view", "sales.view")
    period = _period(
        request.form.get("period")
        if request.method == "POST"
        else request.args.get("period")
    )
    collaborators = _visible_sales_collaborators()
    allowed_ids = {row.id for row in collaborators}

    can_edit = _role_name() in {
        "superadmin",
        "admin",
        "manager",
        "supervisor",
    }

    if request.method == "POST":
        if not can_edit:
            abort(403)

        collaborator_id = request.form.get("collaborator_id", type=int)
        if collaborator_id not in allowed_ids:
            abort(403)

        try:
            amount = Decimal(request.form.get("goal_amount") or "0")
        except (InvalidOperation, ValueError):
            amount = Decimal("-1")

        if amount < 0:
            flash("La meta debe ser un monto válido mayor o igual a cero.", "danger")
            return redirect(
                url_for("planning.sales_goals", period=period["key"])
            )

        key = f"sales_goal:{period['key']}:{collaborator_id}:USD"
        before = system_setting(key, "0")
        save_system_setting(
            key,
            amount,
            "Meta comercial mensual USD por colaborador.",
        )
        audit(
            "actualizar_meta_comercial",
            "SystemSetting",
            before={"value": before},
            after={
                "period": period["key"],
                "collaborator_id": collaborator_id,
                "currency": "USD",
                "value": str(amount),
            },
        )
        db.session.commit()
        flash("Meta comercial actualizada.", "success")
        return redirect(
            url_for("planning.sales_goals", period=period["key"])
        )

    achieved = defaultdict(lambda: Decimal("0"))
    if allowed_ids:
        sales = (
            Sale.query
            .filter(
                Sale.advisor_id.in_(allowed_ids),
                Sale.sale_date >= period["start"],
                Sale.sale_date <= period["end"],
                Sale.currency == "USD",
                Sale.status != "cancelada",
            )
            .all()
        )
        for sale in sales:
            achieved[sale.advisor_id] += Decimal(str(sale.total or 0))

    rows = []
    for collaborator in collaborators:
        key = f"sales_goal:{period['key']}:{collaborator.id}:USD"
        try:
            goal = Decimal(system_setting(key, "0") or "0")
        except InvalidOperation:
            goal = Decimal("0")
        total = achieved[collaborator.id]
        percent = (
            min(999, float(total / goal * 100))
            if goal > 0
            else 0
        )
        rows.append(
            {
                "collaborator": collaborator,
                "goal": goal,
                "achieved": total,
                "percent": percent,
            }
        )

    return render_template(
        "planning/sales_goals.html",
        period=period,
        rows=rows,
        can_edit=can_edit,
    )


@bp.route("/hr-calendar")
@login_required
def hr_calendar():
    if not current_user.collaborator and not current_user.has_permission("hr.view"):
        abort(403)

    period = _period(request.args.get("period"))
    collaborators = _visible_hr_collaborators()
    ids = [row.id for row in collaborators]

    events = defaultdict(list)
    if ids:
        leaves = (
            LeaveRequest.query
            .filter(
                LeaveRequest.collaborator_id.in_(ids),
                LeaveRequest.start_date <= period["end"],
                LeaveRequest.end_date >= period["start"],
            )
            .order_by(LeaveRequest.start_date)
            .all()
        )
        for leave in leaves:
            day = max(leave.start_date, period["start"])
            last = min(leave.end_date, period["end"])
            while day <= last:
                events[day.isoformat()].append(
                    {
                        "title": leave.collaborator.user.name,
                        "type": leave.leave_type,
                        "status": leave.status,
                    }
                )
                day += timedelta(days=1)

    return render_template(
        "planning/hr_calendar.html",
        period=period,
        events=events,
    )


@bp.route("/hr-incidents")
@login_required
def hr_incidents():
    if not current_user.collaborator and not current_user.has_permission("hr.view"):
        abort(403)

    period = _period(request.args.get("period"))
    collaborators = _visible_hr_collaborators()
    rows = _detect_incidents(collaborators, period)

    return render_template(
        "planning/hr_incidents.html",
        period=period,
        rows=rows,
        can_manage=any(_can_manage_hr_row(row) for row in collaborators),
    )


@bp.route("/hr-incidents/resolve", methods=["POST"])
@login_required
def resolve_hr_incident():
    collaborator = db.get_or_404(
        Collaborator,
        request.form.get("collaborator_id", type=int),
    )
    if not _can_manage_hr_row(collaborator):
        abort(403)

    try:
        day = date.fromisoformat(request.form.get("day") or "")
    except ValueError:
        abort(400)

    kind = (request.form.get("kind") or "").strip()
    status = (request.form.get("status") or "justificada").strip()
    reason = (request.form.get("reason") or "").strip()

    allowed_kinds = {
        "ausencia_sin_marcacion",
        "tardanza",
        "marcacion_incompleta",
        "salida_anticipada",
        "pausa_incompleta",
        "break_excesivo",
        "almuerzo_excesivo",
    }
    if kind not in allowed_kinds or status not in {"justificada", "resuelta", "pendiente"}:
        abort(400)
    if status != "pendiente" and not reason:
        flash("Indica el motivo de la justificación o resolución.", "danger")
        return redirect(
            url_for(
                "planning.hr_incidents",
                period=day.strftime("%Y-%m"),
            )
        )

    key = _incident_key(collaborator.id, day, kind)
    before = system_setting(key)
    payload = {
        "status": status,
        "reason": reason,
        "user_id": current_user.id,
        "updated_at": datetime.utcnow().isoformat(),
    }
    save_system_setting(
        key,
        json.dumps(payload, ensure_ascii=False),
        "Resolución de incidencia calculada de asistencia.",
    )
    audit(
        "resolver_incidencia_asistencia",
        "SystemSetting",
        before={"value": before},
        after={
            "collaborator_id": collaborator.id,
            "day": day.isoformat(),
            "kind": kind,
            **payload,
        },
        reason=reason or "Reapertura de incidencia",
    )
    db.session.commit()
    flash("Incidencia actualizada sin alterar las marcaciones originales.", "success")
    return redirect(
        url_for(
            "planning.hr_incidents",
            period=day.strftime("%Y-%m"),
        )
    )


@bp.route("/overtime")
@login_required
def overtime():
    if not current_user.collaborator and not current_user.has_permission("hr.view"):
        abort(403)

    period = _period(request.args.get("period"))
    collaborators = _visible_hr_collaborators()
    rows = _detect_overtime(collaborators, period)

    return render_template(
        "planning/overtime.html",
        period=period,
        rows=rows,
        can_manage=any(_can_manage_hr_row(row) for row in collaborators),
    )


@bp.route("/overtime/decision", methods=["POST"])
@login_required
def overtime_decision():
    collaborator = db.get_or_404(
        Collaborator,
        request.form.get("collaborator_id", type=int),
    )
    if not _can_manage_hr_row(collaborator):
        abort(403)

    try:
        day = date.fromisoformat(request.form.get("day") or "")
    except ValueError:
        abort(400)

    status = (request.form.get("status") or "").strip()
    reason = (request.form.get("reason") or "").strip()

    if status not in {"aprobada", "rechazada", "pendiente"}:
        abort(400)
    if status != "pendiente" and not reason:
        flash("La decisión de horas extra requiere motivo.", "danger")
        return redirect(
            url_for("planning.overtime", period=day.strftime("%Y-%m"))
        )

    try:
        approved_minutes = max(
            0,
            int(request.form.get("approved_minutes") or "0"),
        )
    except ValueError:
        approved_minutes = 0

    key = _overtime_key(collaborator.id, day)
    before = system_setting(key)
    payload = {
        "status": status,
        "approved_minutes": (
            approved_minutes if status == "aprobada" else 0
        ),
        "reason": reason,
        "user_id": current_user.id,
        "updated_at": datetime.utcnow().isoformat(),
    }
    save_system_setting(
        key,
        json.dumps(payload, ensure_ascii=False),
        "Validación administrativa de tiempo adicional.",
    )
    audit(
        "validar_horas_extra",
        "SystemSetting",
        before={"value": before},
        after={
            "collaborator_id": collaborator.id,
            "day": day.isoformat(),
            **payload,
        },
        reason=reason or "Reapertura de validación",
    )
    db.session.commit()
    flash("Validación de horas extra actualizada.", "success")
    return redirect(
        url_for("planning.overtime", period=day.strftime("%Y-%m"))
    )


@bp.route("/recurring-expenses")
@login_required
def recurring_expenses():
    _require_any("finance.view")

    role = _role_name()
    if role not in BUILTIN_ROLES and permission_scope(current_user, "finance.view") != "company":
        abort(403)

    expenses = (
        Expense.query
        .filter(Expense.recurring.is_(True))
        .order_by(Expense.next_due_date, Expense.expense_date.desc())
        .all()
    )
    payables = (
        Payable.query
        .filter(Payable.recurring.is_(True))
        .order_by(Payable.due_date, Payable.id.desc())
        .all()
    )

    rows = []
    for row in expenses:
        rows.append(
            {
                "source": "Egreso",
                "name": row.beneficiary or row.description,
                "concept": row.description,
                "amount": row.amount,
                "currency": row.currency,
                "due": row.next_due_date,
                "status": row.status,
            }
        )
    for row in payables:
        rows.append(
            {
                "source": "Cuenta por pagar",
                "name": row.provider,
                "concept": row.concept,
                "amount": row.amount,
                "currency": row.currency,
                "due": row.due_date,
                "status": row.status,
            }
        )

    rows.sort(key=lambda item: (item["due"] is None, item["due"] or date.max))
    return render_template(
        "planning/recurring_expenses.html",
        rows=rows,
    )


@bp.route("/operations-calendar")
@login_required
def operations_calendar():
    _require_any("projects.view", "tasks.view")
    period = _period(request.args.get("period"))
    projects_query, tasks_query = _visible_operations_queries()

    events = defaultdict(list)

    if current_user.has_permission("projects.view"):
        projects = (
            projects_query
            .filter(
                Project.due_on >= period["start"],
                Project.due_on <= period["end"],
            )
            .order_by(Project.due_on)
            .all()
        )
        for project in projects:
            events[project.due_on.isoformat()].append(
                {
                    "kind": "Proyecto",
                    "title": project.name,
                    "subtitle": project.client.business_name if project.client else "",
                    "status": project.status,
                    "url": url_for(
                        "operations.project_detail",
                        project_id=project.id,
                    ),
                }
            )

    if current_user.has_permission("tasks.view"):
        start_dt = datetime.combine(period["start"], time.min)
        end_dt = datetime.combine(period["end"] + timedelta(days=1), time.min)
        tasks = (
            tasks_query
            .filter(
                Task.due_at.isnot(None),
                Task.due_at >= start_dt,
                Task.due_at < end_dt,
            )
            .order_by(Task.due_at)
            .all()
        )
        for task in tasks:
            events[task.due_at.date().isoformat()].append(
                {
                    "kind": "Tarea",
                    "title": task.title,
                    "subtitle": (
                        task.project.name
                        if task.project
                        else task.client.business_name
                        if task.client
                        else ""
                    ),
                    "status": task.status,
                    "url": url_for(
                        "operations.task_detail",
                        task_id=task.id,
                    ),
                }
            )

    return render_template(
        "planning/operations_calendar.html",
        period=period,
        events=events,
    )
