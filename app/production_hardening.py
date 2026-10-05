from __future__ import annotations

import os
import secrets
from datetime import date, datetime

from flask import (
    g,
    jsonify,
    render_template,
    request,
    url_for,
)
from flask_login import current_user
from sqlalchemy import or_, text
from sqlalchemy.orm import joinedload

from app.extensions import db


PAGINATED_ENDPOINTS = {
    "clients.index",
    "crm.prospects",
    "sales.index",
    "operations.projects",
    "operations.tasks",
    "printing.index",
    "support.index",
    "finance.expenses",
    "finance.receivables",
    "finance.payables",
    "finance.commissions",
    "finance.incomes",
}


def _role_name():
    return current_user.role.name if current_user.role else ""


def _page_config():
    default = int(current_app_config("LIST_PAGE_SIZE", 25) or 25)
    maximum = int(current_app_config("LIST_MAX_PAGE_SIZE", 100) or 100)

    page = request.args.get("page", 1, type=int) or 1
    per_page = request.args.get("per_page", default, type=int) or default

    page = max(1, page)
    per_page = max(1, min(maximum, per_page))
    return page, per_page


def current_app_config(key, default=None):
    from flask import current_app

    return current_app.config.get(key, default)


def _paginate(query):
    page, per_page = _page_config()
    return query.paginate(
        page=page,
        per_page=per_page,
        error_out=False,
        max_per_page=current_app_config("LIST_MAX_PAGE_SIZE", 100),
    )


def _page_url(page_number):
    args = request.args.to_dict(flat=True)
    args["page"] = max(1, int(page_number))
    view_args = dict(request.view_args or {})
    return url_for(request.endpoint, **view_args, **args)


def init_production_hardening(app):
    """Request/response protections that must apply before route execution."""

    insecure_secret = app.config.get("SECRET_KEY") == "change-this-in-production"
    if insecure_secret and not app.config.get("TESTING"):
        app.logger.critical(
            "SECRET_KEY mantiene el valor predeterminado. "
            "Configura una clave segura antes de considerar el entorno endurecido."
        )

    @app.before_request
    def assign_request_id():
        incoming = (request.headers.get("X-Request-ID") or "").strip()
        if incoming and len(incoming) <= 80:
            g.request_id = incoming
        else:
            g.request_id = secrets.token_hex(12)

    @app.after_request
    def add_production_headers(response):
        response.headers["X-Request-ID"] = getattr(
            g,
            "request_id",
            secrets.token_hex(12),
        )
        response.headers.setdefault(
            "Content-Security-Policy",
            (
                "default-src 'self'; "
                "base-uri 'self'; "
                "form-action 'self'; "
                "frame-ancestors 'self'; "
                "img-src 'self' data: https:; "
                "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                "font-src 'self' https://cdn.jsdelivr.net data:; "
                "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                "connect-src 'self'"
            ),
        )
        response.headers.setdefault(
            "Cross-Origin-Opener-Policy",
            "same-origin",
        )
        response.headers.setdefault(
            "X-Permitted-Cross-Domain-Policies",
            "none",
        )

        if current_user.is_authenticated and response.mimetype == "text/html":
            response.headers["Cache-Control"] = (
                "no-store, no-cache, must-revalidate, max-age=0"
            )
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"

        return response

    @app.context_processor
    def inject_hardening_helpers():
        return {
            "pagination_url": _page_url,
        }

    @app.route("/healthz")
    def healthz():
        try:
            db.session.execute(text("SELECT 1"))
            return jsonify(status="ok"), 200
        except Exception:
            db.session.rollback()
            app.logger.exception("Healthcheck de base de datos falló.")
            return jsonify(status="degraded"), 503

    @app.errorhandler(413)
    def payload_too_large(_error):
        max_mb = int(app.config.get("MAX_CONTENT_LENGTH", 0) / 1024 / 1024)
        return (
            render_template(
                "errors/413.html",
                request_id=getattr(g, "request_id", None),
                max_mb=max_mb,
            ),
            413,
        )

    @app.errorhandler(500)
    def internal_server_error(error):
        db.session.rollback()
        request_id = getattr(g, "request_id", "sin-id")
        app.logger.exception(
            "Error interno. request_id=%s path=%s",
            request_id,
            request.path,
            exc_info=error,
        )
        return (
            render_template(
                "errors/500.html",
                request_id=request_id,
            ),
            500,
        )


def init_paginated_views(app):
    """Pagination layer registered after access/security guards.

    It returns a paginated response only for authenticated GET requests.
    POST flows and detail routes keep using their original blueprint views.
    """

    @app.before_request
    def paginate_large_lists():
        if request.method != "GET" or not current_user.is_authenticated:
            return None

        endpoint = request.endpoint or ""
        if endpoint not in PAGINATED_ENDPOINTS:
            return None

        if endpoint == "clients.index":
            return _clients_index()
        if endpoint == "crm.prospects":
            return _crm_prospects()
        if endpoint == "sales.index":
            return _sales_index()
        if endpoint == "operations.projects":
            return _projects_index()
        if endpoint == "operations.tasks":
            return _tasks_index()
        if endpoint == "printing.index":
            return _printing_index()
        if endpoint == "support.index":
            return _support_index()
        if endpoint.startswith("finance."):
            return _finance_index(endpoint)

        return None


def _clients_index():
    from app.blueprints.clients import _visible_client_query
    from app.models import AdvisorProject, Client, Collaborator

    query = _visible_client_query(Client.query).filter(
        Client.record_type == "cliente"
    )
    search = request.args.get("q", "").strip()
    status = request.args.get("status", "").strip()
    advisor_project_id = request.args.get(
        "advisor_project_id",
        type=int,
    )

    if search:
        like = f"%{search}%"
        query = query.filter(
            or_(
                Client.business_name.ilike(like),
                Client.contact_name.ilike(like),
                Client.phone.ilike(like),
                Client.email.ilike(like),
                Client.code.ilike(like),
            )
        )

    if status:
        query = query.filter_by(client_status=status)
    else:
        query = query.filter(Client.client_status != "archivado")

    if advisor_project_id:
        query = query.join(
            Collaborator,
            Client.owner_id == Collaborator.id,
        ).filter(
            Collaborator.advisor_project_id == advisor_project_id
        )

    query = query.options(
        joinedload(Client.owner).joinedload(Collaborator.user),
        joinedload(Client.owner).joinedload(
            Collaborator.advisor_project
        ),
    ).order_by(Client.updated_at.desc())

    pagination = _paginate(query)
    advisor_projects = (
        AdvisorProject.query
        .filter_by(active=True)
        .order_by(AdvisorProject.name)
        .all()
    )
    pending_followups = 0
    if current_user.has_permission("crm.view"):
        pending_followups = (
            _visible_client_query(Client.query)
            .filter_by(record_type="seguimiento")
            .count()
        )

    return render_template(
        "clients/index.html",
        clients=pagination.items,
        pagination=pagination,
        search=search,
        status=status,
        advisor_project_id=advisor_project_id,
        advisor_projects=advisor_projects,
        pending_followups=pending_followups,
    )


def _crm_prospects():
    from app.blueprints.crm import STAGES, visible_clients
    from app.models import AdvisorProject, Client, Collaborator

    query = visible_clients().filter(
        Client.record_type == "seguimiento"
    )
    search = request.args.get("q", "").strip()
    stage = request.args.get("stage", "")
    advisor_project_id = request.args.get(
        "advisor_project_id",
        type=int,
    )

    if search:
        like = f"%{search}%"
        query = query.filter(
            or_(
                Client.business_name.ilike(like),
                Client.contact_name.ilike(like),
                Client.phone.ilike(like),
                Client.email.ilike(like),
            )
        )
    if stage in STAGES:
        query = query.filter_by(pipeline_stage=stage)
    if advisor_project_id:
        query = query.join(
            Collaborator,
            Client.owner_id == Collaborator.id,
        ).filter(
            Collaborator.advisor_project_id == advisor_project_id
        )

    query = query.options(
        joinedload(Client.owner).joinedload(Collaborator.user),
        joinedload(Client.owner).joinedload(
            Collaborator.advisor_project
        ),
    ).order_by(Client.updated_at.desc())

    pagination = _paginate(query)
    advisor_projects = (
        AdvisorProject.query
        .filter_by(active=True)
        .order_by(AdvisorProject.name)
        .all()
    )

    return render_template(
        "crm/prospects.html",
        clients=pagination.items,
        pagination=pagination,
        stages=STAGES,
        search=search,
        stage=stage,
        advisor_project_id=advisor_project_id,
        advisor_projects=advisor_projects,
    )


def _sales_index():
    from app.blueprints.sales import _visible_sales_query
    from app.models import Collaborator, Sale

    query = (
        _visible_sales_query()
        .options(
            joinedload(Sale.client),
            joinedload(Sale.advisor).joinedload(Collaborator.user),
        )
        .order_by(Sale.sale_date.desc(), Sale.id.desc())
    )
    pagination = _paginate(query)

    return render_template(
        "sales/index.html",
        sales=pagination.items,
        pagination=pagination,
    )


def _projects_index():
    from app.blueprints.operations import _visible_projects_query
    from app.models import Collaborator, Project

    query = (
        _visible_projects_query()
        .options(
            joinedload(Project.client),
            joinedload(Project.coordinator).joinedload(
                Collaborator.user
            ),
        )
        .order_by(
            Project.due_on.is_(None),
            Project.due_on.asc(),
            Project.id.desc(),
        )
    )
    pagination = _paginate(query)
    return render_template(
        "operations/projects.html",
        projects=pagination.items,
        pagination=pagination,
    )


def _tasks_index():
    from app.blueprints.operations import (
        TASK_STATES,
        _visible_tasks_query,
    )
    from app.models import Collaborator, Task

    query = (
        _visible_tasks_query()
        .options(
            joinedload(Task.assignee).joinedload(Collaborator.user)
        )
        .order_by(
            Task.due_at.is_(None),
            Task.due_at.asc(),
            Task.id.desc(),
        )
    )
    pagination = _paginate(query)
    return render_template(
        "operations/tasks.html",
        tasks=pagination.items,
        states=TASK_STATES,
        pagination=pagination,
    )


def _printing_index():
    from app.blueprints.printing import _visible_orders_query
    from app.models import PrintOrder, Shipment

    query = _visible_orders_query()

    today = date.today()
    overdue_orders = (
        query
        .join(Shipment, Shipment.order_id == PrintOrder.id)
        .filter(
            PrintOrder.status.notin_(["recibido", "cancelado"]),
            Shipment.status.in_(["en_transito", "parcial"]),
            Shipment.estimated_delivery.isnot(None),
            Shipment.estimated_delivery < today,
        )
        .distinct()
        .all()
    )
    changed = False
    for order in overdue_orders:
        if order.status != "atrasado":
            order.status = "atrasado"
            changed = True
    if changed:
        db.session.commit()

    pagination = _paginate(
        query
        .options(joinedload(PrintOrder.client))
        .order_by(PrintOrder.created_at.desc())
    )
    return render_template(
        "printing/index.html",
        orders=pagination.items,
        pagination=pagination,
    )


def _support_index():
    from app.blueprints.support import (
        _active_collaborators,
        _client_scope,
    )
    from app.models import Client, Collaborator, SupportTicket

    clients = (
        _client_scope(Client.query)
        .filter(
            Client.record_type == "cliente",
            Client.client_status != "archivado",
        )
        .order_by(Client.business_name)
        .all()
    )
    collaborators = _active_collaborators()

    query = SupportTicket.query
    role = _role_name()
    collaborator = current_user.collaborator

    if role == "advisor" and collaborator:
        query = query.filter(
            SupportTicket.client.has(owner_id=collaborator.id)
        )
    elif role == "supervisor" and collaborator:
        allowed_ids = [collaborator.id] + [
            row.id for row in collaborator.subordinates
        ]
        query = query.filter(
            SupportTicket.client.has(
                Client.owner_id.in_(allowed_ids)
            )
        )

    query = query.options(
        joinedload(SupportTicket.client),
        joinedload(SupportTicket.responsible).joinedload(
            Collaborator.user
        ),
    ).order_by(SupportTicket.created_at.desc())

    pagination = _paginate(query)
    return render_template(
        "support/index.html",
        tickets=pagination.items,
        pagination=pagination,
        clients=clients,
        collaborators=collaborators,
        now=datetime.utcnow(),
    )


def _finance_index(endpoint):
    # Las vistas financieras originales usan roles_required. No se
    # interceptan usuarios fuera de ese conjunto para no omitir el
    # decorador de seguridad del blueprint.
    if _role_name() not in {
        "superadmin",
        "admin",
        "manager",
        "finance",
    }:
        return None

    from app.models import (
        AccountReceivable,
        Commission,
        Expense,
        Income,
        Payable,
    )

    if endpoint == "finance.expenses":
        pagination = _paginate(
            Expense.query.order_by(
                Expense.expense_date.desc(),
                Expense.id.desc(),
            )
        )
        return render_template(
            "finance/expenses.html",
            expenses=pagination.items,
            pagination=pagination,
        )

    if endpoint == "finance.receivables":
        pagination = _paginate(
            AccountReceivable.query.order_by(
                AccountReceivable.due_date.is_(None),
                AccountReceivable.due_date.asc(),
                AccountReceivable.id.desc(),
            )
        )
        return render_template(
            "finance/receivables.html",
            rows=pagination.items,
            pagination=pagination,
        )

    if endpoint == "finance.payables":
        pagination = _paginate(
            Payable.query.order_by(
                Payable.due_date.is_(None),
                Payable.due_date.asc(),
                Payable.id.desc(),
            )
        )
        return render_template(
            "finance/payables.html",
            rows=pagination.items,
            pagination=pagination,
        )

    if endpoint == "finance.commissions":
        pagination = _paginate(
            Commission.query.order_by(
                Commission.created_at.desc(),
                Commission.id.desc(),
            )
        )
        return render_template(
            "finance/commissions.html",
            rows=pagination.items,
            pagination=pagination,
        )

    if endpoint == "finance.incomes":
        pagination = _paginate(
            Income.query.order_by(
                Income.effective_date.desc(),
                Income.id.desc(),
            )
        )
        return render_template(
            "finance/incomes.html",
            rows=pagination.items,
            pagination=pagination,
        )

    return None
