from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.client_v2_models import ClientInstallment
from app.project_admin_scope import is_project_admin, client_scope_condition
from app.decorators import permission_required
from app.commercial_plans import HOSTING_TYPE, add_months
from app.extensions import db
from app.models import Client, Renewal
from app.renewal_services import (
    OPEN_RENEWAL_STATUSES,
    complete_contract_renewal,
    complete_external_renewal,
    _next_contract_period,
    create_renewal_quote,
    decline_renewal,
    ensure_renewal_alerts,
    record_renewal_contact,
    renewal_alert_days,
    renewal_amount,
)


bp = Blueprint("renewals", __name__, url_prefix="/renewals")


def _role_name():
    return current_user.role.name if current_user.role else ""


def _team_ids():
    collaborator = current_user.collaborator
    if not collaborator:
        return []
    return [collaborator.id] + [row.id for row in collaborator.subordinates]


def _visible_renewals_query():
    query = Renewal.query
    role = _role_name()
    collaborator = current_user.collaborator
    if role == "advisor" and collaborator:
        query = query.filter(Renewal.client.has(Client.owner_id == collaborator.id))
    elif role == "supervisor" and collaborator:
        query = query.filter(Renewal.client.has(Client.owner_id.in_(_team_ids())))
    elif role == "admin" and is_project_admin():
        query = query.filter(Renewal.client.has(client_scope_condition()))
    return query


def _visible_installments_query():
    query = ClientInstallment.query.join(Client, ClientInstallment.client_id == Client.id)
    role = _role_name()
    collaborator = current_user.collaborator
    if role == "advisor" and collaborator:
        query = query.filter(Client.owner_id == collaborator.id)
    elif role == "supervisor" and collaborator:
        query = query.filter(Client.owner_id.in_(_team_ids()))
    elif role == "admin" and is_project_admin():
        query = query.filter(client_scope_condition())
    return query


def _parse_date(value):
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _parse_datetime(value):
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


@bp.route("/")
@login_required
@permission_required("sales.view")
def index():
    ensure_renewal_alerts()
    db.session.commit()

    scope = request.args.get("scope", "open")
    query = _visible_renewals_query()
    if scope == "open":
        query = query.filter(Renewal.status.in_(OPEN_RENEWAL_STATUSES))
    elif scope == "closed":
        query = query.filter(Renewal.status.in_(["renovado", "no_renueva", "cancelado"]))

    rows = query.order_by(Renewal.due_date.asc(), Renewal.id.asc()).all()
    installments = (
        _visible_installments_query()
        .filter(ClientInstallment.status.notin_(["pagada", "cancelada"]))
        .order_by(ClientInstallment.due_date.asc(), ClientInstallment.id.asc())
        .all()
    )

    today = date.today()
    amounts = {row.id: renewal_amount(row) for row in rows}
    overdue = sum(1 for row in rows if row.status in OPEN_RENEWAL_STATUSES and row.due_date < today)
    next_7 = sum(1 for row in rows if row.status in OPEN_RENEWAL_STATUSES and 0 <= (row.due_date - today).days <= 7)
    next_30 = sum(1 for row in rows if row.status in OPEN_RENEWAL_STATUSES and 0 <= (row.due_date - today).days <= 30)
    overdue_installments = sum(1 for row in installments if row.due_date < today)

    return render_template(
        "renewals/index.html",
        rows=rows,
        installments=installments,
        amounts=amounts,
        today=today,
        scope=scope,
        alert_days=renewal_alert_days(),
        overdue=overdue,
        next_7=next_7,
        next_30=next_30,
        overdue_installments=overdue_installments,
    )


@bp.route("/<int:renewal_id>/contact", methods=["POST"])
@login_required
@permission_required("sales.edit")
def contact(renewal_id):
    renewal = _visible_renewals_query().filter(Renewal.id == renewal_id).first_or_404()
    next_followup = _parse_datetime(request.form.get("next_followup_at"))

    record_renewal_contact(
        renewal,
        user_id=current_user.id,
        channel=request.form.get("channel", "llamada"),
        notes=request.form.get("notes"),
        result=request.form.get("result"),
        next_followup_at=next_followup,
    )
    db.session.commit()
    flash("Contacto de renovación registrado en el historial del cliente.", "success")
    return redirect(url_for("renewals.index"))


@bp.route("/<int:renewal_id>/quote", methods=["POST"])
@login_required
@permission_required("crm.edit")
def quote(renewal_id):
    renewal = _visible_renewals_query().filter(Renewal.id == renewal_id).first_or_404()
    try:
        row = create_renewal_quote(
            renewal,
            amount=request.form.get("amount"),
        )
        db.session.commit()
        flash("Cotización de renovación creada.", "success")
        return redirect(url_for("crm.quote_detail", quote_id=row.id))
    except (ValueError, InvalidOperation) as exc:
        db.session.rollback()
        flash(str(exc), "danger")
        return redirect(url_for("renewals.index"))


@bp.route("/<int:renewal_id>/renew", methods=["GET"])
@login_required
@permission_required("sales.create")
def renew(renewal_id):
    """Pantalla independiente: servicio original fijo y datos solo de pago."""
    renewal = _visible_renewals_query().filter(Renewal.id == renewal_id).first_or_404()
    if renewal.status not in OPEN_RENEWAL_STATUSES:
        flash("Esta renovación ya se encuentra cerrada.", "warning")
        return redirect(url_for("renewals.index"))
    if renewal.contract_id:
        contract = renewal.contract
        if not contract or not contract.product:
            flash("El contrato no tiene un servicio válido para renovar.", "danger")
            return redirect(url_for("renewals.index"))
        starts_on, ends_on = _next_contract_period(contract)
    else:
        starts_on = max(date.today(), renewal.due_date)
        ends_on = add_months(starts_on, 12)
    return render_template(
        "renewals/renew_form.html",
        renewal=renewal, amount=renewal_amount(renewal),
        starts_on=starts_on, ends_on=ends_on,
        today=date.today(),
    )


@bp.route("/<int:renewal_id>/complete", methods=["POST"])
@login_required
@permission_required("sales.create")
def complete(renewal_id):
    renewal = _visible_renewals_query().filter(Renewal.id == renewal_id).first_or_404()
    if renewal.status not in OPEN_RENEWAL_STATUSES:
        flash("Esta renovación ya fue procesada.", "warning")
        return redirect(url_for("renewals.index"))

    # Peticiones anteriores sin modalidad mantienen compatibilidad histórica.
    modern_form = bool(request.form.get("payment_mode"))
    balance_due = _parse_date(request.form.get("balance_due"))
    payment_date = None
    payment_reference = None
    payment_mode = None
    payment_terms = None
    installment_rows = None
    try:
        amount_raw = (renewal_amount(renewal) if renewal.contract_id or
                      renewal.renewal_type == HOSTING_TYPE
                      else request.form.get("amount"))
        amount = Decimal(str(amount_raw or "0"))
        if not amount.is_finite() or amount <= 0:
            raise ValueError("El monto de este servicio debe ser mayor que cero.")

        if modern_form:
            from app.blueprints.sales import (
                PAYMENT_METHODS, _parse_money, _payment_plan_from_request,
            )
            payment_mode = request.form.get("payment_mode", "").strip().lower()
            if payment_mode not in {"contado", "cuotas", "financiamiento"}:
                raise ValueError("Elige contado, cuotas o financiamiento.")
            payment_date = _parse_date(request.form.get("payment_date"))
            if not payment_date:
                raise ValueError("Indica una fecha de pago válida.")
            method = request.form.get("payment_method", "transferencia")
            if method not in PAYMENT_METHODS:
                raise ValueError("Selecciona un método de pago válido.")
            payment_reference = (request.form.get("payment_reference") or "").strip()[:120]
            payment_terms = (request.form.get("payment_terms") or "").strip()[:1000]
            initial = (
                amount if payment_mode == "contado"
                else _parse_money(request.form.get("initial_payment") or "0", "El pago inicial")
            )
            if initial > amount:
                raise ValueError("El pago inicial no puede superar el importe del servicio.")
            if initial >= amount:
                payment_mode = "contado"
            installment_rows = _payment_plan_from_request(
                amount - initial, date.today(), payment_mode=payment_mode,
            )
            next_due_date = add_months(max(date.today(), renewal.due_date), 12)
        else:
            # Se conserva el flujo de solicitudes antiguas y pruebas existentes.
            initial = request.form.get("initial_payment") or "0"
            method = request.form.get("payment_method", "transferencia")
            next_due_date = _parse_date(request.form.get("next_due_date"))
            if not next_due_date and not renewal.contract_id:
                next_due_date = add_months(max(date.today(), renewal.due_date), 12)

        if renewal.contract_id:
            sale, _contract = complete_contract_renewal(
                renewal, amount=amount,
                initial_payment=initial, payment_method=method,
                balance_due=balance_due, user_id=current_user.id,
                payment_date=payment_date, payment_reference=payment_reference,
                payment_mode=payment_mode, payment_terms=payment_terms,
                installment_rows=installment_rows,
            )
        else:
            sale = complete_external_renewal(
                renewal, next_due_date=next_due_date,
                amount=amount, initial_payment=initial,
                payment_method=method, balance_due=balance_due,
                user_id=current_user.id,
                payment_date=payment_date, payment_reference=payment_reference,
                payment_mode=payment_mode, payment_terms=payment_terms,
                installment_rows=installment_rows,
            )
        db.session.commit()
        flash("Renovación confirmada: se conservó el mismo servicio y su nueva vigencia fue calculada automáticamente.", "success")
        return redirect(url_for("sales.detail", sale_id=sale.id)) if sale else redirect(url_for("renewals.index"))
    except (ValueError, InvalidOperation) as exc:
        db.session.rollback()
        flash(str(exc), "danger")
        return redirect(url_for("renewals.renew", renewal_id=renewal_id))


@bp.route("/<int:renewal_id>/decline", methods=["POST"])
@login_required
@permission_required("sales.edit")
def decline(renewal_id):
    renewal = _visible_renewals_query().filter(Renewal.id == renewal_id).first_or_404()
    try:
        decline_renewal(renewal, reason=request.form.get("reason"))
        db.session.commit()
        flash("La no renovación quedó registrada con su motivo.", "warning")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("renewals.index"))
