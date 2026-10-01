from pathlib import Path

from flask import Flask, abort, redirect, render_template, request, url_for
from flask_login import current_user
from werkzeug.middleware.proxy_fix import ProxyFix

from config import Config
from app.access_control import init_access_control
from app.extensions import csrf, db, login_manager, migrate
from app.helpers import human_label, money


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
    from app.blueprints.sales import bp as sales_bp
    from app.blueprints.operations import bp as operations_bp
    from app.blueprints.printing import bp as printing_bp
    from app.blueprints.finance import bp as finance_bp
    from app.blueprints.hr import bp as hr_bp
    from app.blueprints.support import bp as support_bp
    from app.blueprints.settings import bp as settings_bp
    from app.blueprints.reports import bp as reports_bp

    for blueprint in [
        auth_bp,
        dashboard_bp,
        crm_bp,
        clients_bp,
        clients_v3_bp,
        sales_bp,
        operations_bp,
        printing_bp,
        finance_bp,
        hr_bp,
        support_bp,
        settings_bp,
        reports_bp,
    ]:
        app.register_blueprint(blueprint)

    init_access_control(app)

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
            if role_name not in {"superadmin", "admin", "manager", "supervisor"}:
                abort(403)
        return None

    @app.after_request
    def add_security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=()",
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
        if current_user.is_authenticated:
            unread = sum(1 for notification in current_user.notifications if not notification.read)
        return {
            "company_name": app.config.get("COMPANY_NAME"),
            "unread_notifications": unread,
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
