import csv
import io
from datetime import date, datetime
from flask import Blueprint, render_template, request, Response
from flask_login import login_required
from sqlalchemy import func

from app.extensions import db
from app.helpers import audit
from app.models import Client, Sale, Payment, Expense, Commission, Project, Task, PrintOrder, AttendanceMark

bp = Blueprint("reports", __name__, url_prefix="/reports")


@bp.route("/")
@login_required
def index():
    starts = request.args.get("starts")
    ends = request.args.get("ends")
    start_date = date.fromisoformat(starts) if starts else date.today().replace(day=1)
    end_date = date.fromisoformat(ends) if ends else date.today()
    payment_total = db.session.query(func.coalesce(func.sum(Payment.amount), 0)).filter(Payment.status == "confirmado", Payment.effective_date >= start_date, Payment.effective_date <= end_date).scalar()
    expense_total = db.session.query(func.coalesce(func.sum(Expense.amount), 0)).filter(Expense.status == "pagado", Expense.expense_date >= start_date, Expense.expense_date <= end_date).scalar()
    sales_total = db.session.query(func.coalesce(func.sum(Sale.total), 0)).filter(Sale.sale_date >= start_date, Sale.sale_date <= end_date).scalar()
    new_clients = Client.query.filter(Client.record_type == "cliente", Client.created_at >= datetime.combine(start_date, datetime.min.time()), Client.created_at <= datetime.combine(end_date, datetime.max.time())).count()
    commissions = db.session.query(func.coalesce(func.sum(Commission.amount), 0)).filter(Commission.created_at >= datetime.combine(start_date, datetime.min.time()), Commission.created_at <= datetime.combine(end_date, datetime.max.time())).scalar()
    projects = Project.query.filter(Project.created_at >= datetime.combine(start_date, datetime.min.time()), Project.created_at <= datetime.combine(end_date, datetime.max.time())).count()
    print_orders = PrintOrder.query.filter(PrintOrder.created_at >= datetime.combine(start_date, datetime.min.time()), PrintOrder.created_at <= datetime.combine(end_date, datetime.max.time())).count()
    return render_template("reports/index.html", start_date=start_date, end_date=end_date, payment_total=payment_total, expense_total=expense_total, sales_total=sales_total, new_clients=new_clients, commissions=commissions, projects=projects, print_orders=print_orders)


@bp.route("/export.csv")
@login_required
def export_csv():
    report = request.args.get("report", "sales")
    buf = io.StringIO()
    writer = csv.writer(buf)
    if report == "payments":
        writer.writerow(["fecha", "cliente", "venta", "monto", "metodo", "estado"])
        for p in Payment.query.order_by(Payment.effective_date.desc()).all():
            writer.writerow([p.effective_date, p.client.business_name, p.sale.sale_no, p.amount, p.method, p.status])
    elif report == "printing":
        writer.writerow(["orden", "cliente", "estado", "proveedor", "costo", "venta"])
        for o in PrintOrder.query.order_by(PrintOrder.id.desc()).all():
            writer.writerow([o.order_no, o.client.business_name, o.status, o.provider, o.total_cost, o.total_sale])
    else:
        writer.writerow(["venta", "fecha", "cliente", "asesor", "total", "pagado", "saldo"])
        for s in Sale.query.order_by(Sale.sale_date.desc()).all():
            writer.writerow([s.sale_no, s.sale_date, s.client.business_name, s.advisor.user.name if s.advisor else "", s.total, s.amount_paid, s.balance])
    audit("exportar_reporte", report)
    db.session.commit()
    return Response(buf.getvalue(), mimetype="text/csv", headers={"Content-Disposition": f"attachment; filename=reporte_{report}.csv"})
