from flask import Blueprint, render_template
from flask_login import login_required

from app.critical_audit import build_critical_audit
from app.decorators import roles_required


bp = Blueprint("quality", __name__, url_prefix="/quality")


@bp.route("/")
@login_required
@roles_required("superadmin", "admin", "manager")
def index():
    return render_template(
        "quality/index.html",
        audit=build_critical_audit(),
    )
