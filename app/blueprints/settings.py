import re
from decimal import Decimal, InvalidOperation

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError

from app.decorators import roles_required
from app.extensions import db
from app.helpers import audit
from app.models import AuditLog, CatalogItem, Permission, ProductService, Role, SystemSetting

bp = Blueprint("settings", __name__, url_prefix="/settings")

ROLE_CODE_RE = re.compile(r"^[a-z0-9_]{2,80}$")


@bp.route("/")
@login_required
@roles_required("superadmin", "admin", "manager")
def index():
    return render_template(
        "settings/index.html",
        roles=Role.query.order_by(Role.label).all(),
        settings=SystemSetting.query.order_by(SystemSetting.key).all(),
    )


@bp.route("/roles", methods=["GET", "POST"])
@login_required
@roles_required("superadmin", "admin")
def roles():
    permissions = Permission.query.order_by(Permission.module, Permission.label).all()

    if request.method == "POST":
        name = (request.form.get("name") or "").strip().lower().replace(" ", "_")
        label = (request.form.get("label") or "").strip()

        if not ROLE_CODE_RE.fullmatch(name):
            flash("El código del rol debe usar únicamente letras minúsculas, números y guiones bajos.", "danger")
            return redirect(url_for("settings.roles"))
        if not label:
            flash("El nombre visible del rol es obligatorio.", "danger")
            return redirect(url_for("settings.roles"))
        if Role.query.filter_by(name=name).first():
            flash("Ya existe ese rol.", "danger")
            return redirect(url_for("settings.roles"))

        perm_ids = [int(value) for value in request.form.getlist("permission_ids") if value.isdigit()]
        role = Role(
            name=name,
            label=label,
            description=(request.form.get("description") or "").strip() or None,
            active=True,
        )
        role.permissions = Permission.query.filter(Permission.id.in_(perm_ids)).all() if perm_ids else []
        db.session.add(role)
        audit("crear_rol", "Role", after={"name": name})
        db.session.commit()
        flash("Rol creado.", "success")
        return redirect(url_for("settings.roles"))

    return render_template(
        "settings/roles.html",
        roles=Role.query.order_by(Role.label).all(),
        permissions=permissions,
    )


@bp.route("/roles/<int:role_id>/permissions", methods=["POST"])
@login_required
@roles_required("superadmin", "admin")
def update_role_permissions(role_id):
    role = db.get_or_404(Role, role_id)

    # Superadministrador tiene autorización inherente en User.has_permission().
    # No se ofrece una edición engañosa que no tendría efecto real.
    if role.name == "superadmin":
        flash("Los permisos de Superadministrador son inherentes y no se modifican desde esta pantalla.", "info")
        return redirect(url_for("settings.roles"))

    perm_ids = [int(value) for value in request.form.getlist("permission_ids") if value.isdigit()]
    role.permissions = Permission.query.filter(Permission.id.in_(perm_ids)).all() if perm_ids else []
    audit("editar_permisos", "Role", role.id, after={"permission_ids": perm_ids})
    db.session.commit()
    flash("Permisos actualizados.", "success")
    return redirect(url_for("settings.roles"))


@bp.route("/catalogs", methods=["GET", "POST"])
@login_required
@roles_required("superadmin", "admin", "manager")
def catalogs():
    if request.method == "POST":
        category = (request.form.get("category") or "").strip()
        code = (request.form.get("code") or "").strip().lower().replace(" ", "_")
        label = (request.form.get("label") or "").strip()
        sort_order = request.form.get("sort_order", type=int) or 0

        if not category or not code or not label:
            flash("Categoría, código y nombre visible son obligatorios.", "danger")
            return redirect(url_for("settings.catalogs"))
        if CatalogItem.query.filter_by(category=category, code=code).first():
            flash("Ya existe un valor con ese código dentro de la categoría.", "warning")
            return redirect(url_for("settings.catalogs"))

        item = CatalogItem(
            category=category,
            code=code,
            label=label,
            active=True,
            sort_order=sort_order,
        )
        db.session.add(item)
        audit("crear_valor_catalogo", "CatalogItem", after={"category": category, "code": code})
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("No se pudo guardar el valor porque ya existe uno equivalente.", "danger")
            return redirect(url_for("settings.catalogs"))

        flash("Valor de catálogo agregado.", "success")
        return redirect(url_for("settings.catalogs"))

    return render_template(
        "settings/catalogs.html",
        items=CatalogItem.query.order_by(CatalogItem.category, CatalogItem.sort_order).all(),
    )


@bp.route("/products", methods=["GET", "POST"])
@login_required
@roles_required("superadmin", "admin", "manager")
def products():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        if not name:
            flash("El nombre del producto o servicio es obligatorio.", "danger")
            return redirect(url_for("settings.products"))

        base_price_raw = request.form.get("base_price")
        base_price = None
        if base_price_raw not in (None, ""):
            try:
                base_price = Decimal(str(base_price_raw))
            except (InvalidOperation, TypeError, ValueError):
                flash("El precio base no es válido.", "danger")
                return redirect(url_for("settings.products"))
            if base_price < 0:
                flash("El precio base no puede ser negativo.", "danger")
                return redirect(url_for("settings.products"))

        duration_months = request.form.get("duration_months", type=int)
        if duration_months is not None and duration_months < 0:
            flash("La duración no puede ser negativa.", "danger")
            return redirect(url_for("settings.products"))

        currency = request.form.get("currency", "USD")
        if currency not in {"USD", "NIO"}:
            currency = "USD"

        product = ProductService(
            name=name,
            category=(request.form.get("category") or "servicio").strip(),
            description=request.form.get("description"),
            base_price=base_price,
            currency=currency,
            duration_months=duration_months,
            modality=(request.form.get("modality") or "").strip() or None,
            maintenance=(request.form.get("maintenance") or "no_aplica").strip(),
            responsible_area=(request.form.get("responsible_area") or "").strip() or None,
            renewal_required=bool(request.form.get("renewal_required")),
            is_physical=bool(request.form.get("is_physical")),
            active=True,
            components=request.form.get("components"),
        )
        db.session.add(product)
        audit("crear_producto_servicio", "ProductService", after={"name": product.name})
        db.session.commit()
        flash("Producto/servicio creado.", "success")
        return redirect(url_for("settings.products"))

    return render_template(
        "settings/products.html",
        products=ProductService.query.order_by(ProductService.name).all(),
    )


@bp.route("/values", methods=["POST"])
@login_required
@roles_required("superadmin", "admin", "manager")
def values():
    key = (request.form.get("key") or "").strip()
    if not key:
        flash("La clave de configuración es obligatoria.", "danger")
        return redirect(url_for("settings.index"))

    row = SystemSetting.query.filter_by(key=key).first()
    if not row:
        row = SystemSetting(key=key)
        db.session.add(row)
    row.value = request.form.get("value")
    row.description = (request.form.get("description") or "").strip() or None
    audit("guardar_configuracion", "SystemSetting", row.id, after={"key": key})
    db.session.commit()
    flash("Configuración guardada.", "success")
    return redirect(url_for("settings.index"))


@bp.route("/audit")
@login_required
@roles_required("superadmin", "admin", "manager", "audit")
def audit_log():
    rows = AuditLog.query.order_by(AuditLog.created_at.desc()).limit(500).all()
    return render_template("settings/audit.html", rows=rows)
