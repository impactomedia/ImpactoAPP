from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func

from app.decorators import roles_required
from app.extensions import db
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


def _decimal(value, default=Decimal("0")):
    try:
        return Decimal(str(value if value not in (None, "") else default))
    except (InvalidOperation, ValueError, TypeError):
        return default


def _parse_date(value):
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


@bp.route("/")
@login_required
@roles_required("superadmin", "admin", "manager", "finance")
def dashboard():
    income = db.session.query(func.coalesce(func.sum(Payment.amount), 0)).filter(Payment.status == "confirmado").scalar()
    expenses = db.session.query(func.coalesce(func.sum(Expense.amount), 0)).filter(Expense.status == "pagado").scalar()
    receivables = db.session.query(
        func.coalesce(func.sum(AccountReceivable.total_amount - AccountReceivable.paid_amount), 0)
    ).filter(AccountReceivable.status != "pagado").scalar()
    payables = db.session.query(
        func.coalesce(func.sum(Payable.amount - Payable.paid_amount), 0)
    ).filter(Payable.status != "pagado").scalar()
    commissions = Commission.query.order_by(Commission.created_at.desc()).limit(8).all()
    recent_expenses = Expense.query.order_by(Expense.expense_date.desc()).limit(8).all()
    return render_template(
        "finance/dashboard.html",
        income=income,
        expenses=expenses,
        receivables=receivables,
        payables=payables,
        result=Decimal(str(income or 0)) - Decimal(str(expenses or 0)),
        commissions=commissions,
        recent_expenses=recent_expenses,
    )


@bp.route("/expenses", methods=["GET", "POST"])
@login_required
@roles_required("superadmin", "admin", "manager", "finance")
def expenses():
    if request.method == "POST":
        amount = _decimal(request.form.get("amount"))
        if amount <= 0:
            flash("El monto del egreso debe ser mayor que cero.", "danger")
            return redirect(url_for("finance.expenses"))

        status = request.form.get("status", "pagado")
        if status not in {"pagado", "pendiente", "anulado"}:
            status = "pagado"

        expense = Expense(
            expense_date=_parse_date(request.form.get("expense_date")) or date.today(),
            category=(request.form.get("category") or "Otros").strip(),
            beneficiary=(request.form.get("beneficiary") or "").strip() or None,
            description=(request.form.get("description") or "Gasto").strip(),
            amount=amount,
            currency=request.form.get("currency", "USD") if request.form.get("currency") in {"USD", "NIO"} else "USD",
            method=request.form.get("method"),
            status=status,
            recurring=bool(request.form.get("recurring")),
            next_due_date=_parse_date(request.form.get("next_due_date")),
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
    promise_date = _parse_date(request.form.get("promise_date"))
    if not promise_date:
        flash("Indica una fecha válida para la promesa de pago.", "danger")
        return redirect(url_for("finance.receivables"))
    if row.status == "pagado":
        flash("La cuenta ya está pagada y no necesita promesa de pago.", "info")
        return redirect(url_for("finance.receivables"))

    row.promise_date = promise_date
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
        amount = _decimal(request.form.get("amount"))
        if amount <= 0:
            flash("El monto de la cuenta por pagar debe ser mayor que cero.", "danger")
            return redirect(url_for("finance.payables"))

        provider = (request.form.get("provider") or "").strip()
        concept = (request.form.get("concept") or "").strip()
        if not provider or not concept:
            flash("Proveedor y concepto son obligatorios.", "danger")
            return redirect(url_for("finance.payables"))

        row = Payable(
            provider=provider,
            concept=concept,
            amount=amount,
            currency=request.form.get("currency", "USD") if request.form.get("currency") in {"USD", "NIO"} else "USD",
            due_date=_parse_date(request.form.get("due_date")),
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
        name = (request.form.get("name") or "").strip()
        percentage = _decimal(request.form.get("percentage"))
        fixed_amount = _decimal(request.form.get("fixed_amount"))
        minimum_commission = _decimal(request.form.get("minimum_commission"))
        threshold_min = _decimal(request.form.get("threshold_min"))
        threshold_max_raw = request.form.get("threshold_max")
        threshold_max = _decimal(threshold_max_raw) if threshold_max_raw not in (None, "") else None

        if not name:
            flash("El nombre de la regla es obligatorio.", "danger")
            return redirect(url_for("finance.commission_rules"))
        if percentage < 0 or percentage > 100:
            flash("El porcentaje de comisión debe estar entre 0 y 100.", "danger")
            return redirect(url_for("finance.commission_rules"))
        if any(value < 0 for value in [fixed_amount, minimum_commission, threshold_min]):
            flash("Los montos de la regla no pueden ser negativos.", "danger")
            return redirect(url_for("finance.commission_rules"))
        if threshold_max is not None and threshold_max < threshold_min:
            flash("El límite máximo no puede ser menor que el mínimo.", "danger")
            return redirect(url_for("finance.commission_rules"))

        trigger = request.form.get("trigger", "paid")
        if trigger not in {"paid", "sale", "proportional"}:
            trigger = "paid"

        rule = CommissionRule(
            name=name,
            product_id=request.form.get("product_id", type=int),
            threshold_min=threshold_min,
            threshold_max=threshold_max,
            percentage=percentage,
            fixed_amount=fixed_amount,
            minimum_commission=minimum_commission,
            trigger=trigger,
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
    commission = db.get_or_404(Commission, commission_id)
    allowed = {
        "aprobar": {"generada", "estimada"},
        "pagar": {"aprobada"},
        "anular": {"estimada", "generada", "aprobada"},
    }
    if action not in allowed:
        flash("Acción de comisión no válida.", "danger")
        return redirect(url_for("finance.commissions"))
    if commission.status not in allowed[action]:
        flash(f"No se puede {action} una comisión en estado {commission.status}.", "warning")
        return redirect(url_for("finance.commissions"))

    commission.status = {"aprobar": "aprobada", "pagar": "pagada", "anular": "anulada"}[action]
    audit(f"comision_{action}", "Commission", commission.id)
    db.session.commit()
    flash("Estado de comisión actualizado.", "success")
    return redirect(url_for("finance.commissions"))


@bp.route("/payroll", methods=["GET", "POST"])
@login_required
@roles_required("superadmin", "admin", "manager", "finance", "hr")
def payroll():
    if request.method == "POST":
        starts = _parse_date(request.form.get("starts_on"))
        ends = _parse_date(request.form.get("ends_on"))
        if not starts or not ends:
            flash("Indica fechas válidas para el período de planilla.", "danger")
            return redirect(url_for("finance.payroll"))
        if ends < starts:
            flash("La fecha final de la planilla no puede ser anterior a la inicial.", "danger")
            return redirect(url_for("finance.payroll"))

        existing = PayrollPeriod.query.filter_by(starts_on=starts, ends_on=ends).first()
        if existing:
            flash("Ya existe una planilla para ese mismo período.", "warning")
            return redirect(url_for("finance.payroll_detail", period_id=existing.id))

        period = PayrollPeriod(
            name=(request.form.get("name") or f"Planilla {starts} - {ends}").strip(),
            starts_on=starts,
            ends_on=ends,
            status="borrador",
        )
        db.session.add(period)
        db.session.flush()

        collaborators = Collaborator.query.filter_by(status="activo").all()
        for collaborator in collaborators:
            # Solo se incluyen comisiones ya aprobadas. Una comisión generada aún
            # requiere validación financiera antes de entrar a planilla.
            commission_total = db.session.query(func.coalesce(func.sum(Commission.amount), 0)).filter(
                Commission.advisor_id == collaborator.id,
                Commission.status == "aprobada",
                Commission.created_at >= datetime.combine(starts, datetime.min.time()),
                Commission.created_at <= datetime.combine(ends, datetime.max.time()),
            ).scalar()
            base = Decimal(str(collaborator.base_salary or 0))
            commissions = Decimal(str(commission_total or 0))
            db.session.add(
                PayrollLine(
                    period_id=period.id,
                    collaborator_id=collaborator.id,
                    base_salary=base,
                    commissions=commissions,
                    bonuses=0,
                    deductions=0,
                    total=base + commissions,
                )
            )

        audit("generar_planilla", "PayrollPeriod", period.id, after={"name": period.name})
        db.session.commit()
        flash("Planilla generada con salario base y comisiones aprobadas del período.", "success")
        return redirect(url_for("finance.payroll_detail", period_id=period.id))

    return render_template("finance/payroll.html", periods=PayrollPeriod.query.order_by(PayrollPeriod.starts_on.desc()).all())


@bp.route("/payroll/<int:period_id>")
@login_required
@roles_required("superadmin", "admin", "manager", "finance", "hr")
def payroll_detail(period_id):
    period = db.get_or_404(PayrollPeriod, period_id)
    role_name = current_user.role.name if current_user.role else ""
    can_mark_paid = current_user.is_superadmin or role_name in {"admin", "manager", "finance"}
    return render_template("finance/payroll_detail.html", period=period, can_mark_paid=can_mark_paid)


@bp.route("/payroll/<int:period_id>/pay", methods=["POST"])
@login_required
@roles_required("superadmin", "admin", "manager", "finance")
def payroll_pay(period_id):
    period = db.get_or_404(PayrollPeriod, period_id)
    if period.status == "pagada":
        flash("Esta planilla ya fue marcada como pagada.", "info")
        return redirect(url_for("finance.payroll_detail", period_id=period.id))

    # Recalcula las comisiones justo antes de pagar. Esto evita pagar dos veces
    # una misma comisión si existen planillas borrador con períodos solapados.
    for line in period.lines:
        commission_total = db.session.query(func.coalesce(func.sum(Commission.amount), 0)).filter(
            Commission.advisor_id == line.collaborator_id,
            Commission.status == "aprobada",
            Commission.created_at >= datetime.combine(period.starts_on, datetime.min.time()),
            Commission.created_at <= datetime.combine(period.ends_on, datetime.max.time()),
        ).scalar()
        line.commissions = Decimal(str(commission_total or 0))
        line.total = max(
            Decimal("0"),
            Decimal(str(line.base_salary or 0))
            + Decimal(str(line.commissions or 0))
            + Decimal(str(line.bonuses or 0))
            - Decimal(str(line.deductions or 0)),
        )

    period.status = "pagada"
    period.paid_at = datetime.utcnow()
    collaborator_ids = [line.collaborator_id for line in period.lines]
    if collaborator_ids:
        Commission.query.filter(
            Commission.advisor_id.in_(collaborator_ids),
            Commission.status == "aprobada",
            Commission.created_at >= datetime.combine(period.starts_on, datetime.min.time()),
            Commission.created_at <= datetime.combine(period.ends_on, datetime.max.time()),
        ).update({Commission.status: "pagada"}, synchronize_session=False)

    audit("pagar_planilla", "PayrollPeriod", period.id)
    db.session.commit()
    flash("Planilla marcada como pagada y comisiones del período conciliadas.", "success")
    return redirect(url_for("finance.payroll_detail", period_id=period.id))


@bp.route("/incomes", methods=["GET", "POST"])
@login_required
@roles_required("superadmin", "admin", "manager", "finance")
def incomes():
    if request.method == "POST":
        amount = _decimal(request.form.get("amount"))
        if amount <= 0:
            flash("El monto del ingreso debe ser mayor que cero.", "danger")
            return redirect(url_for("finance.incomes"))

        concept = (request.form.get("concept") or "").strip()
        if not concept:
            flash("El concepto del ingreso es obligatorio.", "danger")
            return redirect(url_for("finance.incomes"))

        status = request.form.get("status", "confirmado")
        if status not in {"confirmado", "pendiente", "reversado"}:
            status = "confirmado"

        row = Income(
            effective_date=_parse_date(request.form.get("effective_date")) or date.today(),
            concept=concept,
            amount=amount,
            currency=request.form.get("currency", "USD") if request.form.get("currency") in {"USD", "NIO"} else "USD",
            method=request.form.get("method"),
            reference=request.form.get("reference"),
            status=status,
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
    if row.status == "pagado":
        flash("Esta cuenta ya está pagada.", "info")
        return redirect(url_for("finance.payables"))

    amount = _decimal(request.form.get("amount"))
    remaining = Decimal(str(row.amount or 0)) - Decimal(str(row.paid_amount or 0))
    if amount <= 0:
        flash("El pago debe ser mayor que cero.", "danger")
        return redirect(url_for("finance.payables"))
    if amount > remaining:
        flash(f"El pago excede el saldo pendiente de {remaining:,.2f}.", "danger")
        return redirect(url_for("finance.payables"))

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

    bonuses = _decimal(request.form.get("bonuses"))
    deductions = _decimal(request.form.get("deductions"))
    if bonuses < 0 or deductions < 0:
        flash("Bonos y deducciones no pueden ser negativos.", "danger")
        return redirect(url_for("finance.payroll_detail", period_id=line.period_id))

    line.bonuses = bonuses
    line.deductions = deductions
    line.notes = request.form.get("notes")
    line.total = max(
        Decimal("0"),
        Decimal(str(line.base_salary or 0)) + Decimal(str(line.commissions or 0)) + bonuses - deductions,
    )
    audit(
        "ajustar_planilla",
        "PayrollLine",
        line.id,
        after={"bonuses": str(line.bonuses), "deductions": str(line.deductions), "total": str(line.total)},
    )
    db.session.commit()
    flash("Línea de planilla actualizada.", "success")
    return redirect(url_for("finance.payroll_detail", period_id=line.period_id))
