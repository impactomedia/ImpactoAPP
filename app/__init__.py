from pathlib import Path
from flask import Flask, redirect, url_for
from flask_login import current_user

from config import Config
from app.extensions import db, migrate, login_manager, csrf
from app.helpers import money


def create_app(config_object=Config):
    app = Flask(__name__)
    app.config.from_object(config_object)
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
        return db.session.get(User, int(user_id))

    from app.blueprints.auth import bp as auth_bp
    from app.blueprints.dashboard import bp as dashboard_bp
    from app.blueprints.crm import bp as crm_bp
    from app.blueprints.clients import bp as clients_bp
    from app.blueprints.sales import bp as sales_bp
    from app.blueprints.operations import bp as operations_bp
    from app.blueprints.printing import bp as printing_bp
    from app.blueprints.finance import bp as finance_bp
    from app.blueprints.hr import bp as hr_bp
    from app.blueprints.support import bp as support_bp
    from app.blueprints.settings import bp as settings_bp
    from app.blueprints.reports import bp as reports_bp

    for blueprint in [auth_bp, dashboard_bp, crm_bp, clients_bp, sales_bp, operations_bp, printing_bp, finance_bp, hr_bp, support_bp, settings_bp, reports_bp]:
        app.register_blueprint(blueprint)

    @app.template_filter("money")
    def money_filter(value):
        return money(value)

    @app.context_processor
    def inject_globals():
        unread = 0
        if current_user.is_authenticated:
            unread = sum(1 for n in current_user.notifications if not n.read)
        return {"company_name": app.config.get("COMPANY_NAME"), "unread_notifications": unread}

    @app.route("/")
    def index():
        if current_user.is_authenticated:
            return redirect(url_for("dashboard.index"))
        return redirect(url_for("auth.login"))

    return app
