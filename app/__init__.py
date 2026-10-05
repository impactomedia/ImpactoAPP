from pathlib import Path

from flask import Flask, abort, redirect, render_template, request, url_for
from flask_login import current_user
from werkzeug.middleware.proxy_fix import ProxyFix

from config import Config
from app.access_control import init_access_control
from app.backup_scheduler import init_backup_scheduler
from app.critical_flows import init_critical_flow_guards
from app.security_controls import init_security_controls
from app.catalog_runtime import init_catalog_runtime
from app.extensions import csrf, db, login_manager, migrate
from app.helpers import human_label, money
from app.production_hardening import (
    init_paginated_views,
    init_production_hardening,
)


def create_app(config_object=Config):
    app = Flask(__name__)
    app.config.from_object(config_object)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    Path(app.config["UPLOAD_FOLDER"]).mkdir(parents=True, exist_ok=True)

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)

    login_manager.login_view = "auth.login"
    login_manager.login_message = "Debes iniciar sesión para continuar."
    login_manager.login_message_category = "warning"

    from app.models import User
    from app.nexora_models import SaleOperationMeta  # noqa: F401

    @login_manager.user_loader
    def load_user(user_id):
        try:
            return db.session.get(User, int(user_id))
        except (TypeError, ValueError):
            return None

    from app.blueprints.auth import bp as auth_bp
    from app.blueprints.dashboard import bp as dashboard_bp
    from app.blueprints.crm import bp as crm_bp
    from app.blueprints.clients import bp as clients_bp
    from app.blueprints.clients_v3 import bp as clients_v3_bp
    from app.blueprints.clients_import import bp as clients_import_bp
    from app.blueprints.sales import bp as sales_bp
    from app.blueprints.renewals import bp as renewals_bp
    from app.blueprints.notifications import bp as notifications_bp
    from app.blueprints.operations import bp as operations_bp
    from app.blueprints.printing import bp as printing_bp
    from app.blueprints.finance import bp as finance_bp
    from app.blueprints.hr import bp as hr_bp
    from app.blueprints.support import bp as support_bp
    from app.blueprints.settings import bp as settings_bp
    from app.blueprints.security_admin import bp as security_admin_bp
    from app.blueprints.settings_master import bp as settings_master_bp
    from app.blueprints.reports import bp as reports_bp
    from app.blueprints.data_hub import bp as data_hub_bp
    from app.blueprints.backup_admin import bp as backup_admin_bp
    from app.blueprints.planning import bp as planning_bp
    from app.blueprints.quality import bp as quality_bp

    for blueprint in [
        auth_bp,
        dashboard_bp,
        crm_bp,
        clients_bp,
        clients_v3_bp,
        clients_import_bp,
        sales_bp,
        renewals_bp,
        notifications_bp,
        operations_bp,
        printing_bp,
        finance_bp,
        hr_bp,
        support_bp,
        settings_bp,
        security_admin_bp,
        settings_master_bp,
        reports_bp,
        data_hub_bp,
        backup_admin_bp,
        planning_bp,
        quality_bp,
    ]:
        app.register_blueprint(blueprint)

    init_production_hardening(app)
    init_catalog_runtime(app)
    init_security_controls(app)
    init_access_control(app)
    init_backup_scheduler(app)
    init_critical_flow_guards(app)

    @app.before_request
    def protect_uploaded_static_files():
        if request.endpoint == "static" and request.path.startswith("/static/uploads/client_"):
            abort(404)
        if (
            request.endpoint == "static"
            and request.path.startswith("/static/uploads/")
            and not current_user.is_authenticated
        ):
            abort(404)
        return None

    @app.before_request
    def restrict_kanban_to_coordination_roles():
        if request.endpoint == "operations.task_kanban" and current_user.is_authenticated:
            role_name = current_user.role.name if current_user.role else ""
            if role_name not in {
                "superadmin",
                "admin",
                "manager",
                "supervisor",
                "development_coordinator",
            }:
                abort(403)
        return None

    init_paginated_views(app)

    @app.after_request
    def add_security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=()",
        )
        if request.is_secure:
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )
        return response

    @app.template_filter("money")
    def money_filter(value):
        return money(value)

    @app.template_filter("label")
    def label_filter(value):
        return human_label(value)

    @app.context_processor
    def inject_globals():
        unread = 0
        catalog_payload = {}
        if current_user.is_authenticated:
            from app.notification_center import unread_count_for_user
            from app.catalog_runtime import catalog_options, system_setting

            unread = unread_count_for_user(current_user)
            catalog_payload = {
                category: catalog_options(category)
                for category in (
                    "department",
                    "job_title",
                    "pipeline_stage",
                    "prospect_source",
                    "lost_reason",
                    "priority",
                    "interaction_type",
                    "payment_method",
                    "currency",
                    "expense_category",
                    "income_category",
                    "provider",
                    "task_type",
                    "project_status",
                    "task_status",
                    "ticket_type",
                    "ticket_status",
                    "support_priority",
                )
            }

        from app.catalog_runtime import catalog_options, system_setting

        operational_defaults = {
            "tolerance_minutes": system_setting("attendance_default_tolerance_minutes", "10"),
            "break_minutes": system_setting("attendance_default_break_minutes", "30"),
            "lunch_minutes": system_setting("attendance_default_lunch_minutes", "60"),
            "vacation_rate": system_setting("vacation_default_rate", "0"),
            "currency": system_setting("currency_default", "USD"),
        } if current_user.is_authenticated else {}

        return {
            "company_name": app.config.get("COMPANY_NAME"),
            "unread_notifications": unread,
            "catalog_options": catalog_options,
            "catalog_payload": catalog_payload,
            "operational_defaults": operational_defaults,
        }

    @app.errorhandler(403)
    def forbidden(_error):
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def not_found(_error):
        return render_template("errors/404.html"), 404

    @app.route("/")
    def index():
        if current_user.is_authenticated:
            return redirect(url_for("dashboard.index"))
        return redirect(url_for("auth.login"))

    return app
