from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.client_v2_models import ClientInstallment
from app.decorators import permission_required
from app.extensions import db
from app.models import Client, Renewal
from app.renewal_services import (
    OPEN_RENEWAL_STATUSES,
    complete_contract_renewal,
    complete_external_renewal,
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
    return query


def _visible_installments_query():
    query = ClientInstallment.query.join(Client, ClientInstallment.client_id == Client.id)
    role = _role_name()
    collaborator = current_user.collaborator
    if role == "advisor" and collaborator:
        query = query.filter(Client.owner_id == collaborator.id)
    elif role == "supervisor" and collaborator:
        query = query.filter(Client.owner_id.in_(_team_ids()))
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


@bp.route("/<int:renewal_id>/complete", methods=["POST"])
@login_required
@permission_required("sales.create")
def complete(renewal_id):
    renewal = _visible_renewals_query().filter(Renewal.id == renewal_id).first_or_404()
    balance_due = _parse_date(request.form.get("balance_due"))

    try:
        if renewal.contract_id:
            sale, _contract = complete_contract_renewal(
                renewal,
                amount=request.form.get("amount"),
                initial_payment=request.form.get("initial_payment") or 0,
                payment_method=request.form.get("payment_method", "transferencia"),
                balance_due=balance_due,
                user_id=current_user.id,
            )
        else:
            next_due_date = _parse_date(request.form.get("next_due_date"))
            sale = complete_external_renewal(
                renewal,
                next_due_date=next_due_date,
                amount=request.form.get("amount") or 0,
                initial_payment=request.form.get("initial_payment") or 0,
                payment_method=request.form.get("payment_method", "transferencia"),
                balance_due=balance_due,
                user_id=current_user.id,
            )

        db.session.commit()
        flash("Renovación confirmada y nuevo período registrado.", "success")
        if sale:
            return redirect(url_for("sales.detail", sale_id=sale.id))
        return redirect(url_for("renewals.index"))
    except (ValueError, InvalidOperation) as exc:
        db.session.rollback()
        flash(str(exc), "danger")
        return redirect(url_for("renewals.index"))


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
