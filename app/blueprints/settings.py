from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required

from app.extensions import db
from app.decorators import roles_required
from app.helpers import audit
from app.models import Role, Permission, CatalogItem, SystemSetting, AuditLog, ProductService

bp = Blueprint("settings", __name__, url_prefix="/settings")


@bp.route("/")
@login_required
@roles_required("superadmin", "admin", "manager")
def index():
    return render_template("settings/index.html", roles=Role.query.order_by(Role.label).all(), settings=SystemSetting.query.order_by(SystemSetting.key).all())


@bp.route("/roles", methods=["GET", "POST"])
@login_required
@roles_required("superadmin", "admin")
def roles():
    permissions = Permission.query.order_by(Permission.module, Permission.label).all()
    if request.method == "POST":
        name = request.form.get("name", "").strip().lower().replace(" ", "_")
        if Role.query.filter_by(name=name).first():
            flash("Ya existe ese rol.", "danger")
            return redirect(url_for("settings.roles"))
        role = Role(name=name, label=request.form.get("label", name.title()), description=request.form.get("description"), active=True)
        perm_ids = [int(x) for x in request.form.getlist("permission_ids") if x.isdigit()]
        role.permissions = Permission.query.filter(Permission.id.in_(perm_ids)).all() if perm_ids else []
        db.session.add(role)
        audit("crear_rol", "Role", after={"name": name})
        db.session.commit()
        flash("Rol creado.", "success")
        return redirect(url_for("settings.roles"))
    return render_template("settings/roles.html", roles=Role.query.order_by(Role.label).all(), permissions=permissions)


@bp.route("/roles/<int:role_id>/permissions", methods=["POST"])
@login_required
@roles_required("superadmin", "admin")
def update_role_permissions(role_id):
    role = db.get_or_404(Role, role_id)
    perm_ids = [int(x) for x in request.form.getlist("permission_ids") if x.isdigit()]
    role.permissions = Permission.query.filter(Permission.id.in_(perm_ids)).all() if perm_ids else []
    audit("editar_permisos", "Role", role.id)
    db.session.commit()
    flash("Permisos actualizados.", "success")
    return redirect(url_for("settings.roles"))


@bp.route("/catalogs", methods=["GET", "POST"])
@login_required
@roles_required("superadmin", "admin", "manager")
def catalogs():
    if request.method == "POST":
        item = CatalogItem(category=request.form.get("category"), code=request.form.get("code"), label=request.form.get("label"), active=True, sort_order=request.form.get("sort_order", type=int) or 0)
        db.session.add(item)
        db.session.commit()
        flash("Valor de catálogo agregado.", "success")
        return redirect(url_for("settings.catalogs"))
    return render_template("settings/catalogs.html", items=CatalogItem.query.order_by(CatalogItem.category, CatalogItem.sort_order).all())


@bp.route("/products", methods=["GET", "POST"])
@login_required
@roles_required("superadmin", "admin", "manager")
def products():
    if request.method == "POST":
        p = ProductService(
            name=request.form.get("name", "").strip(),
            category=request.form.get("category", "servicio"),
            description=request.form.get("description"),
            base_price=request.form.get("base_price") or None,
            currency=request.form.get("currency", "USD"),
            duration_months=request.form.get("duration_months", type=int),
            modality=request.form.get("modality"),
            maintenance=request.form.get("maintenance", "no_aplica"),
            responsible_area=request.form.get("responsible_area"),
            renewal_required=bool(request.form.get("renewal_required")),
            is_physical=bool(request.form.get("is_physical")),
            active=True,
            components=request.form.get("components"),
        )
        db.session.add(p)
        audit("crear_producto_servicio", "ProductService", after={"name": p.name})
        db.session.commit()
        flash("Producto/servicio creado.", "success")
        return redirect(url_for("settings.products"))
    return render_template("settings/products.html", products=ProductService.query.order_by(ProductService.name).all())


@bp.route("/values", methods=["POST"])
@login_required
@roles_required("superadmin", "admin", "manager")
def values():
    key = request.form.get("key", "").strip()
    row = SystemSetting.query.filter_by(key=key).first()
    if not row:
        row = SystemSetting(key=key)
        db.session.add(row)
    row.value = request.form.get("value")
    row.description = request.form.get("description")
    db.session.commit()
    flash("Configuración guardada.", "success")
    return redirect(url_for("settings.index"))


@bp.route("/audit")
@login_required
@roles_required("superadmin", "admin", "manager", "audit")
def audit_log():
    rows = AuditLog.query.order_by(AuditLog.created_at.desc()).limit(500).all()
    return render_template("settings/audit.html", rows=rows)
