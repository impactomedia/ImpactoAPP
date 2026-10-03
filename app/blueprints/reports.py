import csv
import io
from datetime import date

from flask import Blueprint, Response, abort, render_template, request
from flask_login import login_required

from app.extensions import db
from app.helpers import audit
from app.reporting_services import (
    allowed_export_reports,
    build_report_context,
    export_dataset,
)

bp = Blueprint("reports", __name__, url_prefix="/reports")

KNOWN_REPORTS = {
    "sales",
    "payments",
    "expenses",
    "receivables",
    "payables",
    "attendance",
    "leaves",
    "prospects",
    "clients",
    "renewals",
    "projects",
    "tasks",
    "printing",
}


def _parse_date(value, fallback):
    if not value:
        return fallback
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return fallback


def _requested_dates():
    start_date = _parse_date(request.args.get("starts"), date.today().replace(day=1))
    end_date = _parse_date(request.args.get("ends"), date.today())
    if end_date < start_date:
        start_date, end_date = end_date, start_date
    return start_date, end_date


@bp.route("/")
@login_required
def index():
    start_date, end_date = _requested_dates()
    context = build_report_context(start_date, end_date)
    return render_template(
        "reports/index.html",
        **context,
        export_reports=allowed_export_reports(),
    )


@bp.route("/export.csv")
@login_required
def export_csv():
    report = request.args.get("report", "sales")
    if report not in KNOWN_REPORTS:
        abort(404)
    if report not in allowed_export_reports():
        abort(403)

    start_date, end_date = _requested_dates()
    dataset = export_dataset(report, start_date, end_date)
    if not dataset:
        abort(403)

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(dataset["headers"])
    writer.writerows(dataset["rows"])

    audit(
        "exportar_reporte",
        report,
        after={"starts": start_date.isoformat(), "ends": end_date.isoformat()},
    )
    db.session.commit()

    return Response(
        "\ufeff" + buffer.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{dataset["filename"]}"'},
    )


@bp.route("/print")
@login_required
def printable():
    start_date, end_date = _requested_dates()
    area = (request.args.get("area") or "all").strip().lower()
    context = build_report_context(start_date, end_date)

    if area != "all":
        if area not in context["areas"]:
            abort(403)
        context["sections"] = {
            key: value
            for key, value in context["sections"].items()
            if key == area
        }

    return render_template(
        "reports/printable.html",
        **context,
        selected_area=area,
    )
