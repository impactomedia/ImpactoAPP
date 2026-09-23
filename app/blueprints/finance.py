from datetime import date, datetime
from decimal import Decimal
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required
from sqlalchemy import func

from app.extensions import db
from app.decorators import roles_required
from app.helpers import audit
from app.models import (
    AccountReceivable,
    Commission,
    CommissionRule,
    Collaborator,
    Expense,
    Income,
    Payable,
    Payment,
    PayrollLine,
    PayrollPeriod,
    ProductService,
)

bp = Blueprint("finance", __name__, url_prefix="/finance")


@bp.route("/")
@login_required
@roles_required("superadmin", "admin", "manager", "finance")
def dashboard():
    income = db.session.query(func.coalesce(func.sum(Payment.amount), 0)).filter(Payment.status == "confirmado").scalar()
    expenses = db.session.query(func.coalesce(func.sum(Expense.amount), 0)).filter(Expense.status == "pagado").scalar()
    receivables = db.session.query(func.coalesce(func.sum(AccountReceivable.total_amount - AccountReceivable.paid_amount), 0)).filter(AccountReceivable.status != "pagado").scalar()
    payables = db.session.query(func.coalesce(func.sum(Payable.amount - Payable.paid_amount), 0)).filter(Payable.status != "pagado").scalar()
    commissions = Commission.query.order_by(Commission.created_at.desc()).limit(8).all()
    recent_expenses = Expense.query.order_by(Expense.expense_date.desc()).limit(8).all()
    return render_template("finance/dashboard.html", income=income, expenses=expenses, receivables=receivables, payables=payables, result=Decimal(str(income or 0))-Decimal(str(expenses or 0)), commissions=commissions, recent_expenses=recent_expenses)


@bp.route("/expenses", methods=["GET", "POST"])
@login_required
@roles_required("superadmin", "admin", "manager", "finance")
def expenses():
    if request.method == "POST":
        expense = Expense(
            expense_date=date.fromisoformat(request.form.get("expense_date")) if request.form.get("expense_date") else date.today(),
            category=request.form.get("category", "otros"),
            beneficiary=request.form.get("beneficiary"),
            description=request.form.get("description", "Gasto"),
            amount=request.form.get("amount") or 0,
            currency=request.form.get("currency", "USD"),
            method=request.form.get("method"),
            status=request.form.get("status", "pagado"),
            recurring=bool(request.form.get("recurring")),
            next_due_date=date.fromisoformat(request.form.get("next_due_date")) if request.form.get("next_due_date") else None,
            notes=request.form.get("notes"),
        )
        db.session.add(expense)
        audit("registrar_egreso", "Expense", after={"category": expense.category, "amount": str(expense.amount)})
        db.session.commit()
        flash("Egreso registrado.", "success")
        return redirect(url_for("finance.expenses"))
    return render_template("finance/expenses.html", expenses=Expense.query.order_by(Expense.expense_date.desc()).all())


@bp.route("/receivables")
@login_required
@roles_required("superadmin", "admin", "manager", "finance")
def receivables():
    rows = AccountReceivable.query.order_by(AccountReceivable.due_date.is_(None), AccountReceivable.due_date.asc()).all()
    return render_template("finance/receivables.html", rows=rows)


@bp.route("/receivables/<int:row_id>/promise", methods=["POST"])
@login_required
@roles_required("superadmin", "admin", "manager", "finance")
def promise(row_id):
    row = db.get_or_404(AccountReceivable, row_id)
    row.promise_date = date.fromisoformat(request.form.get("promise_date")) if request.form.get("promise_date") else None
    row.notes = request.form.get("notes")
    row.status = "promesa_pago"
    audit("promesa_pago", "AccountReceivable", row.id, after={"promise_date": str(row.promise_date)})
    db.session.commit()
    flash("Promesa de pago registrada.", "success")
    return redirect(url_for("finance.receivables"))


@bp.route("/payables", methods=["GET", "POST"])
@login_required
@roles_required("superadmin", "admin", "manager", "finance")
def payables():
    if request.method == "POST":
        row = Payable(
            provider=request.form.get("provider", "Proveedor"),
            concept=request.form.get("concept", "Obligación"),
            amount=request.form.get("amount") or 0,
            currency=request.form.get("currency", "USD"),
            due_date=date.fromisoformat(request.form.get("due_date")) if request.form.get("due_date") else None,
            status="pendiente",
            recurring=bool(request.form.get("recurring")),
            notes=request.form.get("notes"),
        )
        db.session.add(row)
        audit("registrar_cuenta_pagar", "Payable", after={"provider": row.provider, "amount": str(row.amount)})
        db.session.commit()
        flash("Cuenta por pagar registrada.", "success")
        return redirect(url_for("finance.payables"))
    return render_template("finance/payables.html", rows=Payable.query.order_by(Payable.due_date.is_(None), Payable.due_date.asc()).all())


@bp.route("/commission-rules", methods=["GET", "POST"])
@login_required
@roles_required("superadmin", "admin", "manager", "finance")
def commission_rules():
    products = ProductService.query.filter_by(active=True).order_by(ProductService.name).all()
    if request.method == "POST":
        rule = CommissionRule(
            name=request.form.get("name", "Regla"),
            product_id=request.form.get("product_id", type=int),
            threshold_min=request.form.get("threshold_min") or 0,
            threshold_max=request.form.get("threshold_max") or None,
            percentage=request.form.get("percentage") or 0,
            fixed_amount=request.form.get("fixed_amount") or 0,
            minimum_commission=request.form.get("minimum_commission") or 0,
            trigger=request.form.get("trigger", "paid"),
            active=True,
        )
        db.session.add(rule)
        audit("crear_regla_comision", "CommissionRule", after={"name": rule.name})
        db.session.commit()
        flash("Regla de comisión creada.", "success")
        return redirect(url_for("finance.commission_rules"))
    return render_template("finance/commission_rules.html", rules=CommissionRule.query.order_by(CommissionRule.threshold_min).all(), products=products)


@bp.route("/commissions")
@login_required
@roles_required("superadmin", "admin", "manager", "finance")
def commissions():
    rows = Commission.query.order_by(Commission.created_at.desc()).all()
    return render_template("finance/commissions.html", rows=rows)


@bp.route("/commissions/<int:commission_id>/<action>", methods=["POST"])
@login_required
@roles_required("superadmin", "admin", "manager", "finance")
def commission_action(commission_id, action):
    c = db.get_or_404(Commission, commission_id)
    if action in {"aprobar", "pagar", "anular"}:
        c.status = {"aprobar": "aprobada", "pagar": "pagada", "anular": "anulada"}[action]
        audit(f"comision_{action}", "Commission", c.id)
        db.session.commit()
        flash("Estado de comisión actualizado.", "success")
    return redirect(url_for("finance.commissions"))


@bp.route("/payroll", methods=["GET", "POST"])
@login_required
@roles_required("superadmin", "admin", "manager", "finance", "hr")
def payroll():
    if request.method == "POST":
        starts = date.fromisoformat(request.form.get("starts_on"))
        ends = date.fromisoformat(request.form.get("ends_on"))
        period = PayrollPeriod(name=request.form.get("name", f"Planilla {starts} - {ends}"), starts_on=starts, ends_on=ends, status="borrador")
        db.session.add(period)
        db.session.flush()
        collaborators = Collaborator.query.filter_by(status="activo").all()
        for c in collaborators:
            commission_total = db.session.query(func.coalesce(func.sum(Commission.amount), 0)).filter(
                Commission.advisor_id == c.id,
                Commission.status.in_(["generada", "aprobada"]),
                Commission.created_at >= datetime.combine(starts, datetime.min.time()),
                Commission.created_at <= datetime.combine(ends, datetime.max.time()),
            ).scalar()
            base = Decimal(str(c.base_salary or 0))
            commissions = Decimal(str(commission_total or 0))
            line = PayrollLine(period_id=period.id, collaborator_id=c.id, base_salary=base, commissions=commissions, bonuses=0, deductions=0, total=base+commissions)
            db.session.add(line)
        audit("generar_planilla", "PayrollPeriod", period.id, after={"name": period.name})
        db.session.commit()
        flash("Planilla generada con base y comisiones del período.", "success")
        return redirect(url_for("finance.payroll_detail", period_id=period.id))
    return render_template("finance/payroll.html", periods=PayrollPeriod.query.order_by(PayrollPeriod.starts_on.desc()).all())


@bp.route("/payroll/<int:period_id>")
@login_required
@roles_required("superadmin", "admin", "manager", "finance", "hr")
def payroll_detail(period_id):
    period = db.get_or_404(PayrollPeriod, period_id)
    return render_template("finance/payroll_detail.html", period=period)


@bp.route("/payroll/<int:period_id>/pay", methods=["POST"])
@login_required
@roles_required("superadmin", "admin", "manager", "finance")
def payroll_pay(period_id):
    period = db.get_or_404(PayrollPeriod, period_id)
    period.status = "pagada"
    period.paid_at = datetime.utcnow()
    collaborator_ids = [line.collaborator_id for line in period.lines]
    Commission.query.filter(Commission.advisor_id.in_(collaborator_ids), Commission.status == "aprobada").update({Commission.status: "pagada"}, synchronize_session=False)
    audit("pagar_planilla", "PayrollPeriod", period.id)
    db.session.commit()
    flash("Planilla marcada como pagada.", "success")
    return redirect(url_for("finance.payroll_detail", period_id=period.id))


@bp.route("/incomes", methods=["GET", "POST"])
@login_required
@roles_required("superadmin", "admin", "manager", "finance")
def incomes():
    if request.method == "POST":
        row = Income(
            effective_date=date.fromisoformat(request.form.get("effective_date")) if request.form.get("effective_date") else date.today(),
            concept=request.form.get("concept", "Ingreso"),
            amount=request.form.get("amount") or 0,
            currency=request.form.get("currency", "USD"),
            method=request.form.get("method"),
            reference=request.form.get("reference"),
            status=request.form.get("status", "confirmado"),
            notes=request.form.get("notes"),
        )
        db.session.add(row)
        audit("registrar_ingreso", "Income", after={"concept": row.concept, "amount": str(row.amount)})
        db.session.commit()
        flash("Ingreso registrado.", "success")
        return redirect(url_for("finance.incomes"))
    rows = Income.query.order_by(Income.effective_date.desc(), Income.id.desc()).all()
    return render_template("finance/incomes.html", rows=rows)


@bp.route("/payables/<int:row_id>/payment", methods=["POST"])
@login_required
@roles_required("superadmin", "admin", "manager", "finance")
def payable_payment(row_id):
    row = db.get_or_404(Payable, row_id)
    amount = Decimal(str(request.form.get("amount") or 0))
    remaining = Decimal(str(row.amount or 0)) - Decimal(str(row.paid_amount or 0))
    amount = max(Decimal("0"), min(amount, remaining))
    row.paid_amount = Decimal(str(row.paid_amount or 0)) + amount
    if row.paid_amount >= row.amount:
        row.status = "pagado"
    audit("pago_cuenta_pagar", "Payable", row.id, after={"amount": str(amount), "paid": str(row.paid_amount)})
    db.session.commit()
    flash("Pago aplicado a la cuenta por pagar.", "success")
    return redirect(url_for("finance.payables"))


@bp.route("/payroll/line/<int:line_id>/adjust", methods=["POST"])
@login_required
@roles_required("superadmin", "admin", "manager", "finance", "hr")
def payroll_adjust(line_id):
    line = db.get_or_404(PayrollLine, line_id)
    if line.period.status == "pagada":
        flash("No se puede modificar una planilla pagada.", "danger")
        return redirect(url_for("finance.payroll_detail", period_id=line.period_id))
    line.bonuses = Decimal(str(request.form.get("bonuses") or 0))
    line.deductions = Decimal(str(request.form.get("deductions") or 0))
    line.notes = request.form.get("notes")
    line.total = Decimal(str(line.base_salary or 0)) + Decimal(str(line.commissions or 0)) + line.bonuses - line.deductions
    audit("ajustar_planilla", "PayrollLine", line.id, after={"bonuses": str(line.bonuses), "deductions": str(line.deductions), "total": str(line.total)})
    db.session.commit()
    flash("Línea de planilla actualizada.", "success")
    return redirect(url_for("finance.payroll_detail", period_id=line.period_id))
