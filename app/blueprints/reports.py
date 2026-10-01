import csv
import io
from datetime import date, datetime

from flask import Blueprint, abort, render_template, request, Response
from flask_login import current_user, login_required
from sqlalchemy import func, or_

from app.extensions import db
from app.helpers import audit
from app.models import Client, Collaborator, Commission, Expense, Payment, PrintOrder, Project, Sale, Task

bp = Blueprint("reports", __name__, url_prefix="/reports")


def _role_name():
    return current_user.role.name if current_user.role else ""


def _team_ids():
    collaborator = current_user.collaborator
    if not collaborator:
        return []
    return [collaborator.id] + [row.id for row in collaborator.subordinates]


def _parse_date(value, fallback):
    if not value:
        return fallback
    try:
        return date.fromisoformat(value)
    except ValueError:
        return fallback


def _client_query():
    query = Client.query
    role = _role_name()
    collaborator = current_user.collaborator
    if role == "advisor" and collaborator:
        query = query.filter(Client.owner_id == collaborator.id)
    elif role == "supervisor" and collaborator:
        query = query.filter(Client.owner_id.in_(_team_ids()))
    return query


def _sale_query():
    query = Sale.query
    role = _role_name()
    collaborator = current_user.collaborator
    if role == "advisor" and collaborator:
        query = query.filter(
            or_(
                Sale.advisor_id == collaborator.id,
                Sale.client.has(Client.owner_id == collaborator.id),
            )
        )
    elif role == "supervisor" and collaborator:
        ids = _team_ids()
        query = query.filter(
            or_(
                Sale.advisor_id.in_(ids),
                Sale.client.has(Client.owner_id.in_(ids)),
            )
        )
    return query


def _payment_query():
    query = Payment.query
    role = _role_name()
    collaborator = current_user.collaborator
    if role == "advisor" and collaborator:
        query = query.filter(
            or_(
                Payment.sale.has(Sale.advisor_id == collaborator.id),
                Payment.client.has(Client.owner_id == collaborator.id),
            )
        )
    elif role == "supervisor" and collaborator:
        ids = _team_ids()
        query = query.filter(
            or_(
                Payment.sale.has(Sale.advisor_id.in_(ids)),
                Payment.client.has(Client.owner_id.in_(ids)),
            )
        )
    return query


def _commission_query():
    query = Commission.query
    role = _role_name()
    collaborator = current_user.collaborator
    if role == "advisor" and collaborator:
        query = query.filter(Commission.advisor_id == collaborator.id)
    elif role == "supervisor" and collaborator:
        query = query.filter(Commission.advisor_id.in_(_team_ids()))
    return query


def _project_query():
    query = Project.query
    role = _role_name()
    collaborator = current_user.collaborator
    if not collaborator:
        return query
    if role == "advisor":
        query = query.filter(Project.client.has(Client.owner_id == collaborator.id))
    elif role == "supervisor":
        query = query.filter(Project.client.has(Client.owner_id.in_(_team_ids())))
    elif role == "production":
        query = query.filter(
            or_(
                Project.coordinator_id == collaborator.id,
                Project.members.any(id=collaborator.id),
            )
        )
    return query


def _print_query():
    query = PrintOrder.query
    role = _role_name()
    collaborator = current_user.collaborator
    if role == "advisor" and collaborator:
        query = query.filter(PrintOrder.client.has(Client.owner_id == collaborator.id))
    elif role == "supervisor" and collaborator:
        query = query.filter(PrintOrder.client.has(Client.owner_id.in_(_team_ids())))
    return query


def _task_query():
    query = Task.query
    role = _role_name()
    collaborator = current_user.collaborator
    if role in {"superadmin", "admin", "manager", "audit"}:
        return query
    if not collaborator:
        return query.filter(Task.id == -1)

    own = or_(Task.assignee_id == collaborator.id, Task.collaborators.any(id=collaborator.id))
    if role == "supervisor":
        ids = _team_ids()
        return query.filter(
            or_(
                Task.assignee_id.in_(ids),
                Task.collaborators.any(Collaborator.id.in_(ids)),
                Task.project.has(Project.client.has(Client.owner_id.in_(ids))),
            )
        )
    if role == "advisor":
        return query.filter(or_(own, Task.project.has(Project.client.has(Client.owner_id == collaborator.id))))
    if role == "production":
        return query.filter(
            or_(
                own,
                Task.project.has(
                    or_(
                        Project.coordinator_id == collaborator.id,
                        Project.members.any(id=collaborator.id),
                    )
                ),
            )
        )
    return query.filter(own)


def _is_audit():
    return _role_name() == "audit"


@bp.route("/")
@login_required
def index():
    default_start = date.today().replace(day=1)
    default_end = date.today()
    start_date = _parse_date(request.args.get("starts"), default_start)
    end_date = _parse_date(request.args.get("ends"), default_end)
    if end_date < start_date:
        start_date, end_date = end_date, start_date

    start_dt = datetime.combine(start_date, datetime.min.time())
    end_dt = datetime.combine(end_date, datetime.max.time())

    audit_role = _is_audit()
    can_sales = current_user.has_permission("sales.view") or audit_role
    can_finance = current_user.has_permission("finance.view") or audit_role
    can_clients = current_user.has_permission("clients.view") or audit_role
    can_projects = current_user.has_permission("projects.view") or audit_role
    can_printing = current_user.has_permission("printing.view") or audit_role
    can_tasks = current_user.has_permission("tasks.view") or audit_role

    sales_total = None
    payment_total = None
    expense_total = None
    commissions_total = None
    new_clients = None
    projects = None
    print_orders = None
    tasks_created = None
    tasks_completed = None

    if can_sales:
        sales_total = db.session.query(func.coalesce(func.sum(Sale.total), 0)).filter(
            Sale.id.in_(_sale_query().with_entities(Sale.id)),
            Sale.sale_date >= start_date,
            Sale.sale_date <= end_date,
        ).scalar()

        payment_total = db.session.query(func.coalesce(func.sum(Payment.amount), 0)).filter(
            Payment.id.in_(_payment_query().with_entities(Payment.id)),
            Payment.status == "confirmado",
            Payment.effective_date >= start_date,
            Payment.effective_date <= end_date,
        ).scalar()

    if can_finance:
        expense_total = db.session.query(func.coalesce(func.sum(Expense.amount), 0)).filter(
            Expense.status == "pagado",
            Expense.expense_date >= start_date,
            Expense.expense_date <= end_date,
        ).scalar()

    if can_finance or _role_name() in {"advisor", "supervisor"} or audit_role:
        commissions_total = db.session.query(func.coalesce(func.sum(Commission.amount), 0)).filter(
            Commission.id.in_(_commission_query().with_entities(Commission.id)),
            Commission.created_at >= start_dt,
            Commission.created_at <= end_dt,
            Commission.status != "anulada",
        ).scalar()

    if can_clients:
        new_clients = _client_query().filter(
            Client.record_type == "cliente",
            Client.created_at >= start_dt,
            Client.created_at <= end_dt,
        ).count()

    if can_projects:
        projects = _project_query().filter(Project.created_at >= start_dt, Project.created_at <= end_dt).count()

    if can_printing:
        print_orders = _print_query().filter(PrintOrder.created_at >= start_dt, PrintOrder.created_at <= end_dt).count()

    if can_tasks:
        task_query = _task_query().filter(Task.created_at >= start_dt, Task.created_at <= end_dt)
        tasks_created = task_query.count()
        tasks_completed = task_query.filter(Task.status == "completada").count()

    can_export_sales = current_user.has_permission("reports.export") and can_sales
    can_export_payments = current_user.has_permission("reports.export") and (can_sales or can_finance)
    can_export_printing = current_user.has_permission("reports.export") and can_printing

    return render_template(
        "reports/index.html",
        start_date=start_date,
        end_date=end_date,
        sales_total=sales_total,
        payment_total=payment_total,
        expense_total=expense_total,
        commissions=commissions_total,
        new_clients=new_clients,
        projects=projects,
        print_orders=print_orders,
        tasks_created=tasks_created,
        tasks_completed=tasks_completed,
        can_export_sales=can_export_sales,
        can_export_payments=can_export_payments,
        can_export_printing=can_export_printing,
    )


@bp.route("/export.csv")
@login_required
def export_csv():
    report = request.args.get("report", "sales")
    audit_role = _is_audit()

    if report == "sales" and not (current_user.has_permission("sales.view") or audit_role):
        abort(403)
    if report == "payments" and not (
        current_user.has_permission("sales.view")
        or current_user.has_permission("finance.view")
        or audit_role
    ):
        abort(403)
    if report == "printing" and not (current_user.has_permission("printing.view") or audit_role):
        abort(403)
    if report not in {"sales", "payments", "printing"}:
        abort(404)

    buffer = io.StringIO()
    writer = csv.writer(buffer)

    if report == "payments":
        writer.writerow(["fecha", "cliente", "venta", "monto", "metodo", "estado"])
        for payment in _payment_query().order_by(Payment.effective_date.desc()).all():
            writer.writerow([
                payment.effective_date,
                payment.client.business_name,
                payment.sale.sale_no,
                payment.amount,
                payment.method,
                payment.status,
            ])
    elif report == "printing":
        writer.writerow(["orden", "cliente", "estado", "proveedor", "costo", "venta"])
        for order in _print_query().order_by(PrintOrder.id.desc()).all():
            writer.writerow([
                order.order_no,
                order.client.business_name,
                order.status,
                order.provider,
                order.total_cost,
                order.total_sale,
            ])
    else:
        writer.writerow(["venta", "fecha", "cliente", "asesor", "total", "pagado", "saldo"])
        for sale in _sale_query().order_by(Sale.sale_date.desc()).all():
            writer.writerow([
                sale.sale_no,
                sale.sale_date,
                sale.client.business_name,
                sale.advisor.user.name if sale.advisor else "",
                sale.total,
                sale.amount_paid,
                sale.balance,
            ])

    audit("exportar_reporte", report)
    db.session.commit()
    return Response(
        buffer.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=reporte_{report}.csv"},
    )
