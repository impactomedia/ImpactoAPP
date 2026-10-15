"""Finanzas por proyecto y quincena, con controles de roles independientes."""
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.decorators import roles_required
from app.extensions import db
from app.helpers import audit
from app.lead_commission_models import LeadCommissionPayout, SaleLeadAttribution
from app.lead_commission_service import lead_commission_rows, net_sale_commission
from app.models import AdvisorProject, Collaborator, Sale, User
from app.finance_control_models import (
    AdvisorFortnightGoal, AdvisorFortnightPayout,
    FinanceProjectAllocation, FinanceProjectMovement,
)
from app.finance_control_service import (
    available_payout, fortnight_bounds, fortnight_summary,
    is_commercial_advisor, local_today, money, monthly_ledger, project_of_sale,
)

bp = Blueprint("finance_control", __name__, url_prefix="/finance-control")
GOAL_ROLES = ("superadmin", "admin", "supervisor")
FINANCE_ROLES = ("superadmin", "admin", "manager", "finance")
REQUEST_ROLES = FINANCE_ROLES + ("supervisor", "advisor")
CATEGORIES = {
    "imprenta": "Imprenta", "meta_ads": "Meta Ads",
    "dominio": "Dominio / hosting", "servicios": "Otros servicios",
    "administracion": "Administración", "nomina": "Nómina",
    "otros": "Otros gastos",
}


def role():
    return current_user.role.name if current_user.role else ""


def positive_amount(raw, *, allow_zero=False):
    try:
        amount = Decimal(str(raw))
        if not amount.is_finite() or amount < 0 or (not allow_zero and amount == 0):
            raise ValueError()
        return money(amount)
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("Introduce un importe válido mayor que cero.")


def user_can_manage_advisor(advisor):
    if role() in ("superadmin", "admin"):
        return True
    if role() == "supervisor" and current_user.collaborator:
        supervisor = current_user.collaborator
        return bool(advisor.supervisor_id == supervisor.id and (not supervisor.advisor_project_id or advisor.advisor_project_id == supervisor.advisor_project_id))
    return False


def user_can_request_for_sale(sale):
    if role() in FINANCE_ROLES:
        return True
    actor = current_user.collaborator
    if not actor:
        return False
    owner = sale.advisor_id or (sale.client.owner_id if sale.client else None)
    if role() == "advisor":
        return owner == actor.id
    if role() == "supervisor":
        person = db.session.get(Collaborator, owner) if owner else None
        return bool(person and person.supervisor_id == actor.id and (not actor.advisor_project_id or person.advisor_project_id == actor.advisor_project_id))
    return False


def parse_day(raw, default=None):
    if not raw:
        return default
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise ValueError("Indica una fecha válida.")


@bp.route("/")
@login_required
@roles_required(*FINANCE_ROLES)
def index():
    today = local_today()
    year = request.args.get("year", today.year, type=int)
    month = request.args.get("month", today.month, type=int)
    if year < 2000 or year > 2100 or month not in range(1, 13):
        abort(400)
    grouped, all_entries, nio = monthly_ledger(year, month)
    projects = AdvisorProject.query.order_by(AdvisorProject.name).all()
    requested_id = request.args.get("project_id", type=int)
    if requested_id and requested_id not in grouped:
        abort(400)
    # Solo la superadministradora puede consultar el consolidado completo.
    if role() != "superadmin":
        requested_id = requested_id or (projects[0].id if projects else None)
    selected = grouped.get(requested_id) if requested_id else None
    if selected:
        entries = selected["entries"]
        income, expenses = selected["income"], selected["expenses"]
    else:
        entries = all_entries
        income = sum((row["income"] for row in grouped.values()), money(0))
        expenses = sum((row["expenses"] for row in grouped.values()), money(0))
    return render_template(
        "finance_control/index.html", can_manage_lead_commissions=role() == "superadmin",
        year=year, month=month,
        projects=projects, grouped=grouped, selected_id=requested_id,
        entries=entries[:300], income=income, expenses=expenses,
        result=income - expenses, nio=nio, can_consolidate=role() == "superadmin",
        unassigned=grouped[None],
    )


@bp.route("/lead-commissions", methods=["GET", "POST"])
@login_required
@roles_required("superadmin")
def lead_commissions():
    """Los generadores ganan sobre neto cobrado; el pago es un egreso separado."""
    if request.method == "POST":
        sale = db.get_or_404(Sale, request.form.get("sale_id", type=int))
        attribution = sale.lead_attribution
        if not attribution or attribution.percentage <= 0:
            abort(403)
        row = net_sale_commission(sale)
        if not row["project_id"]:
            flash("Asigna primero el proyecto comercial de la venta.", "danger")
            return redirect(url_for("finance_control.lead_commissions"))
        try:
            amount = positive_amount(request.form.get("amount"))
            if amount > row["available"]:
                raise ValueError("El importe excede la comisión disponible de esta venta.")
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("finance_control.lead_commissions"))
        payout = LeadCommissionPayout(
            sale_id=sale.id, generator_id=attribution.generator_id,
            project_id=row["project_id"], amount=amount, paid_on=local_today(),
            paid_by_id=current_user.id,
            notes=(request.form.get("notes") or "")[:2000],
        )
        db.session.add(payout)
        audit("pagar_comision_generador_leads", "LeadCommissionPayout",
              after={"sale_id": sale.id, "generador": attribution.generator.name,
                     "porcentaje": str(attribution.percentage),
                     "base_neta": str(row["base"]), "monto": str(amount)})
        db.session.commit()
        flash("Pago registrado como gasto comercial, sin modificar la base neta del asesor.", "success")
        return redirect(url_for("finance_control.lead_commissions"))

    rows = lead_commission_rows()
    total_earned = sum((x["commission"] for x in rows), money(0))
    total_paid = sum((x["paid"] for x in rows), money(0))
    payouts = LeadCommissionPayout.query.order_by(
        LeadCommissionPayout.paid_on.desc(), LeadCommissionPayout.id.desc()
    ).limit(100).all()
    return render_template(
        "finance_control/lead_commissions.html", rows=rows,
        earned=total_earned, paid=total_paid,
        outstanding=max(money(0), total_earned-total_paid),
        payouts=payouts,
    )


@bp.route("/export.csv")
@login_required
@roles_required(*FINANCE_ROLES)
def export_csv():
    """CSV compatible con Excel, limitado al proyecto seleccionado."""
    import csv
    from io import StringIO
    from flask import Response

    today = local_today()
    year = request.args.get("year", today.year, type=int)
    month = request.args.get("month", today.month, type=int)
    if year < 2000 or year > 2100 or month not in range(1, 13):
        abort(400)
    requested_id = request.args.get("project_id", type=int)
    if role() != "superadmin" and not requested_id:
        abort(403)
    grouped, entries, _ = monthly_ledger(year, month)
    if requested_id:
        if requested_id not in grouped or requested_id is None:
            abort(404)
        entries = grouped[requested_id]["entries"]
    result = StringIO()
    writer = csv.writer(result)
    writer.writerow(["Fecha", "Proyecto", "Tipo", "Fuente", "Descripción",
                     "Importe", "Moneda"])
    for item in entries:
        project = grouped[item["project_id"]]["project"]
        # Evitar que celdas de texto suministradas por usuarios ejecuten
        # fórmulas cuando se abra el CSV en Excel.
        description = str(item["description"] or "")
        if description.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
            description = "'" + description
        writer.writerow([
            item["on"].isoformat(), project.name if project else "Sin asignar",
            item["type"], item["source"], description, str(item["amount"]),
            item["currency"],
        ])
    audit("exportar_finanzas_proyecto", "FinanceProjectMovement",
          after={"project_id": requested_id, "year": year, "month": month})
    db.session.commit()
    return Response(
        "\ufeff" + result.getvalue(), content_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename=finanzas_{year}_{month:02d}.csv"},
    )


@bp.route("/goals", methods=["GET", "POST"])
@login_required
@roles_required(*GOAL_ROLES)
def goals():
    today = local_today()
    starts, ends = fortnight_bounds(today)
    day = parse_day(request.values.get("period"), today)
    starts, ends = fortnight_bounds(day)
    if request.method == "POST":
        advisor = db.get_or_404(Collaborator, request.form.get("advisor_id", type=int))
        if not is_commercial_advisor(advisor) or not user_can_manage_advisor(advisor):
            abort(403)
        try:
            total_goal = positive_amount(request.form.get("total_goal"))
            net_goal = positive_amount(request.form.get("net_goal"))
            percent = positive_amount(request.form.get("percentage"), allow_zero=True)
            if percent > 100:
                raise ValueError("El porcentaje no puede superar 100 %.")
            if starts < fortnight_bounds(today)[0]:
                raise ValueError("No se pueden modificar metas de quincenas pasadas.")
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("finance_control.goals", period=starts.isoformat()))
        goal = AdvisorFortnightGoal.query.filter_by(advisor_id=advisor.id, period_start=starts).first()
        if not goal:
            goal = AdvisorFortnightGoal(
                advisor_id=advisor.id, period_start=starts,
                project_id=advisor.advisor_project_id, updated_by_id=current_user.id,
            )
            db.session.add(goal)
        else:
            if AdvisorFortnightPayout.query.filter_by(advisor_id=advisor.id, period_start=starts).first():
                flash("No se pueden modificar metas de una quincena con comisiones pagadas.", "danger")
                return redirect(url_for("finance_control.goals", period=starts.isoformat()))
            if goal.project_id != advisor.advisor_project_id:
                abort(409)
        goal.total_goal, goal.net_goal, goal.percentage = total_goal, net_goal, percent
        goal.updated_by_id = current_user.id
        audit("configurar_meta_financiera", "AdvisorFortnightGoal", after={
            "advisor_id": advisor.id, "project_id": goal.project_id,
            "quincena": str(starts), "meta_total": str(total_goal),
            "meta_neta": str(net_goal), "porcentaje": str(percent),
        })
        db.session.commit()
        flash("Metas quincenales del asesor guardadas.", "success")
        return redirect(url_for("finance_control.goals", period=starts.isoformat()))
    advisors = [x for x in Collaborator.query.order_by(Collaborator.id).all()
                if is_commercial_advisor(x) and user_can_manage_advisor(x)]
    saved = {
        g.advisor_id: g for g in AdvisorFortnightGoal.query.filter_by(period_start=starts).all()
        if any(advisor.id == g.advisor_id for advisor in advisors)
    }
    return render_template("finance_control/goals.html",
        advisors=advisors, saved=saved, starts=starts, ends=ends, day=day)


@bp.route("/advisor")
@login_required
@roles_required("advisor")
def advisor():
    worker = current_user.collaborator
    if not worker or not is_commercial_advisor(worker):
        abort(403)
    return render_template("finance_control/advisor.html",
        summary=fortnight_summary(worker))


@bp.route("/movements", methods=["GET", "POST"])
@login_required
@roles_required(*REQUEST_ROLES)
def movements():
    if request.method == "POST":
        category = request.form.get("category") or ""
        if category not in CATEGORIES:
            abort(400)
        sale_id = request.form.get("sale_id", type=int)
        sale = db.get_or_404(Sale, sale_id) if sale_id else None
        if sale and not user_can_request_for_sale(sale):
            abort(403)
        if not sale and role() not in FINANCE_ROLES:
            abort(403)
        project_id = project_of_sale(sale) if sale else request.form.get("project_id", type=int)
        project = db.session.get(AdvisorProject, project_id) if project_id else None
        if not project:
            flash("Se requiere un proyecto comercial válido para registrar el gasto.", "danger")
            return redirect(url_for("finance_control.movements"))
        if sale and request.form.get("project_id", type=int) not in (None, project_id):
            abort(403)
        description = (request.form.get("description") or "").strip()
        try:
            amount = positive_amount(request.form.get("amount"))
            executed_on = parse_day(request.form.get("executed_on"), local_today())
            if executed_on > local_today():
                raise ValueError("No se pueden ejecutar gastos con fecha futura.")
            if fortnight_bounds(executed_on)[0] != fortnight_bounds(local_today())[0]:
                raise ValueError("Los gastos deben registrarse en su quincena de ejecución; no se permite alterar quincenas anteriores.")
            if not description:
                raise ValueError("Describe el concepto del gasto.")
            if sale and sale.status == "cancelada":
                raise ValueError("No se pueden registrar gastos en una venta cancelada.")
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("finance_control.movements"))
        amount_paid_at_execution = money(sum((
            money(p.amount) for p in sale.payments
            if p.status == "confirmado" and p.currency == "USD" and p.effective_date <= executed_on
        ), money(0))) if sale else money(0)
        is_early = bool(sale and amount_paid_at_execution < money(sale.total))
        if is_early:
            status = "pendiente_autorizacion"
            executed_on = None
        else:
            # La fecha registrada significa ejecución REAL, no reserva futura.
            if role() not in FINANCE_ROLES:
                status, executed_on = "pendiente", None
            else:
                status = "ejecutado"
        row = FinanceProjectMovement(
            project_id=project.id, sale_id=sale.id if sale else None,
            category=category, description=description[:255], amount=amount,
            status=status, executed_on=executed_on,
            requested_by_id=current_user.id, notes=(request.form.get("notes") or "")[:2000],
            executed_by_id=current_user.id if status == "ejecutado" else None,
        )
        db.session.add(row)
        audit("registrar_solicitud_gasto_financiero", "FinanceProjectMovement", after={
            "project_id": project.id, "sale_id": sale_id, "category": category,
            "amount": str(amount), "status": status,
        })
        db.session.commit()
        flash("Gasto registrado." if status == "ejecutado" else "Solicitud registrada; pendiente de autorización o ejecución.", "success")
        return redirect(url_for("finance_control.movements"))
    if role() in FINANCE_ROLES:
        rows = FinanceProjectMovement.query.order_by(FinanceProjectMovement.id.desc()).limit(250).all()
    else:
        rows = FinanceProjectMovement.query.filter_by(requested_by_id=current_user.id).order_by(FinanceProjectMovement.id.desc()).limit(100).all()
    projects = AdvisorProject.query.order_by(AdvisorProject.name).all()
    if role() == "advisor":
        projects = [p for p in projects if current_user.collaborator and p.id == current_user.collaborator.advisor_project_id]
    if role() in FINANCE_ROLES:
        sales = Sale.query.order_by(Sale.id.desc()).limit(200).all()
    else:
        from app.blueprints.sales import _visible_sales_query
        sales = _visible_sales_query().order_by(Sale.id.desc()).limit(200).all()
    return render_template("finance_control/movements.html", rows=rows,
        projects=projects, sales=sales, categories=CATEGORIES,
        can_execute=role() in FINANCE_ROLES,
        can_approve=role() == "superadmin")


@bp.route("/movements/<int:movement_id>/decision", methods=["POST"])
@login_required
@roles_required("superadmin")
def decide(movement_id):
    row = db.get_or_404(FinanceProjectMovement, movement_id)
    if row.status != "pendiente_autorizacion":
        abort(409)
    decision = request.form.get("decision")
    if decision not in {"aprobar", "rechazar"}:
        abort(400)
    reason = (request.form.get("reason") or "").strip()
    if not reason:
        flash("Debes indicar el motivo de la decisión.", "danger")
        return redirect(url_for("finance_control.movements"))
    row.status = "autorizado" if decision == "aprobar" else "rechazado"
    row.authorized_by_id = current_user.id
    row.authorized_at = datetime.utcnow()
    row.authorization_reason = reason
    audit("autorizar_gasto_anticipado", "FinanceProjectMovement", row.id,
          after={"decision": decision, "reason": reason})
    db.session.commit()
    flash("Solicitud actualizada. La autorización no ejecuta el gasto.", "success")
    return redirect(url_for("finance_control.movements"))


@bp.route("/movements/<int:movement_id>/execute", methods=["POST"])
@login_required
@roles_required(*FINANCE_ROLES)
def execute(movement_id):
    row = db.get_or_404(FinanceProjectMovement, movement_id)
    if row.status not in {"pendiente", "autorizado"}:
        abort(409)
    if row.status == "pendiente" and row.sale:
        paid = sum((money(p.amount) for p in row.sale.payments if p.status == "confirmado" and p.currency == "USD" and p.effective_date <= local_today()), money(0))
        if paid < money(row.sale.total):
            flash("El cliente no ha completado su contrato; solicita autorización de Superadministración.", "danger")
            return redirect(url_for("finance_control.movements"))
    try:
        day = parse_day(request.form.get("executed_on"), local_today())
        if day > local_today():
            raise ValueError("La fecha de ejecución no puede ser futura.")
        if fortnight_bounds(day)[0] != fortnight_bounds(local_today())[0]:
            raise ValueError("La ejecución debe registrarse en la quincena vigente, sin alterar períodos anteriores.")
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("finance_control.movements"))
    row.status, row.executed_on, row.executed_by_id = "ejecutado", day, current_user.id
    audit("ejecutar_gasto_financiero", "FinanceProjectMovement", row.id,
          after={"executed_on": str(day), "amount": str(row.amount)})
    db.session.commit()
    flash("Gasto ejecutado y contabilizado en su quincena.", "success")
    return redirect(url_for("finance_control.movements"))


@bp.route("/payouts", methods=["GET", "POST"])
@login_required
@roles_required(*FINANCE_ROLES)
def payouts():
    today = local_today()
    day = parse_day(request.values.get("period"), today)
    starts, ends = fortnight_bounds(day)
    if request.method == "POST":
        worker = db.get_or_404(Collaborator, request.form.get("advisor_id", type=int))
        if not is_commercial_advisor(worker):
            abort(403)
        summary = fortnight_summary(worker, starts)
        try:
            amount = positive_amount(request.form.get("amount"))
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("finance_control.payouts", period=str(starts)))
        available = available_payout(summary)
        if not summary["eligible"] or amount > available:
            flash("El monto supera la comisión disponible o la meta neta aún no fue alcanzada.", "danger")
            return redirect(url_for("finance_control.payouts", period=str(starts)))
        row = AdvisorFortnightPayout(
            advisor_id=worker.id, project_id=worker.advisor_project_id,
            period_start=starts, amount=amount, paid_on=today,
            paid_by_id=current_user.id,
            notes=(request.form.get("notes") or "")[:2000],
        )
        db.session.add(row)
        audit("pagar_comision_quincenal", "AdvisorFortnightPayout",
              after={"advisor_id": worker.id, "period": str(starts), "amount": str(amount)})
        db.session.commit()
        flash("Pago de comisión registrado sin modificar quincenas anteriores.", "success")
        return redirect(url_for("finance_control.payouts", period=str(starts)))
    advisors = [x for x in Collaborator.query.order_by(Collaborator.id).all() if is_commercial_advisor(x)]
    summaries = [(fortnight_summary(a, starts), None) for a in advisors]
    summaries = [(item, available_payout(item)) for item, _ in summaries]
    records = AdvisorFortnightPayout.query.filter_by(period_start=starts).order_by(
        AdvisorFortnightPayout.paid_on.desc(), AdvisorFortnightPayout.id.desc()
    ).all()
    return render_template("finance_control/payouts.html",
        starts=starts, ends=ends, day=day, summaries=summaries, records=records)


@bp.route("/allocate", methods=["POST"])
@login_required
@roles_required(*FINANCE_ROLES)
def allocate():
    from app.models import Expense, Income, PayrollLine
    record_type = request.form.get("record_type")
    models = {"expense": Expense, "income": Income, "payroll_line": PayrollLine, "sale": Sale}
    if record_type not in models:
        abort(400)
    model = models[record_type]
    record_id = request.form.get("record_id", type=int)
    source = db.get_or_404(model, record_id)
    if record_type == "sale":
        worker = source.advisor or (source.client.owner if source.client else None)
        if worker and worker.advisor_project_id:
            abort(409)  # El proyecto del asesor no se modifica manualmente.
    project = db.get_or_404(AdvisorProject, request.form.get("project_id", type=int))
    row = FinanceProjectAllocation.query.filter_by(record_type=record_type, record_id=record_id).first()
    if not row:
        row = FinanceProjectAllocation(record_type=record_type, record_id=record_id)
        db.session.add(row)
    row.project_id = project.id
    row.assigned_by_id = current_user.id
    audit("asignar_proyecto_movimiento", "FinanceProjectAllocation",
          after={"type": record_type, "id": record_id, "project": project.code})
    db.session.commit()
    flash("Proyecto asignado al movimiento histórico.", "success")
    return redirect(request.referrer or url_for("finance_control.index"))
