import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import login_required

from app.catalog_runtime import (
    CATALOG_DEFINITIONS,
    ensure_catalog_defaults,
    is_protected_catalog_code,
    refresh_runtime_catalogs,
    save_system_setting,
    system_setting,
)
from app.decorators import roles_required
from app.extensions import db
from app.helpers import audit
from app.models import (
    CatalogItem,
    CommissionRule,
    Holiday,
    ProductService,
    Role,
    TaskTemplate,
    TaskTemplateItem,
    WorkSchedule,
)


bp = Blueprint("settings_master", __name__, url_prefix="/settings/master-data")
CODE_RE = re.compile(r"^[a-z0-9_áéíóúñü-]{1,80}$", re.IGNORECASE)


def _redirect(category=None, anchor=None):
    target = url_for("settings_master.index", category=category) if category else url_for("settings_master.index")
    if anchor:
        target += f"#{anchor}"
    return redirect(target)


def _parse_date(raw):
    try:
        return date.fromisoformat((raw or "").strip())
    except (TypeError, ValueError):
        return None


def _parse_int(raw, default, minimum=0, maximum=None):
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = int(default)
    value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _normalize_code(raw):
    value = (raw or "").strip().lower()
    replacements = {
        " ": "_",
        "/": "_",
        ".": "_",
    }
    for old, new in replacements.items():
        value = value.replace(old, new)
    while "__" in value:
        value = value.replace("__", "_")
    return value.strip("_")


@bp.route("/")
@login_required
@roles_required("superadmin", "admin", "manager")
def index():
    ensure_catalog_defaults()
    selected = (request.args.get("category") or "department").strip()
    if selected not in CATALOG_DEFINITIONS:
        selected = "department"

    catalog_items = (
        CatalogItem.query
        .filter_by(category=selected)
        .order_by(CatalogItem.sort_order, CatalogItem.id)
        .all()
    )

    policies = {
        "attendance_default_tolerance_minutes": system_setting(
            "attendance_default_tolerance_minutes", "10"
        ),
        "attendance_default_break_minutes": system_setting(
            "attendance_default_break_minutes", "30"
        ),
        "attendance_default_lunch_minutes": system_setting(
            "attendance_default_lunch_minutes", "60"
        ),
        "vacation_default_rate": system_setting("vacation_default_rate", "0"),
        "vacation_policy_note": system_setting(
            "vacation_policy_note",
            "Definir y validar la política interna de vacaciones.",
        ),
        "attendance_policy_note": system_setting(
            "attendance_policy_note",
            "Configurar tolerancias, pausas y horas extra según política interna.",
        ),
        "renewal_alert_days": system_setting("renewal_alert_days", "60,30,15,7,0"),
        "currency_default": system_setting("currency_default", "USD"),
    }

    return render_template(
        "settings/master_data.html",
        definitions=CATALOG_DEFINITIONS,
        selected_category=selected,
        selected_definition=CATALOG_DEFINITIONS[selected],
        catalog_items=catalog_items,
        policies=policies,
        holidays=Holiday.query.order_by(Holiday.holiday_date.desc()).all(),
        templates=TaskTemplate.query.order_by(TaskTemplate.active.desc(), TaskTemplate.name).all(),
        products=ProductService.query.filter_by(active=True).order_by(ProductService.name).all(),
        roles_count=Role.query.count(),
        schedules_count=WorkSchedule.query.filter_by(active=True).count(),
        products_count=ProductService.query.filter_by(active=True).count(),
        commission_rules_count=CommissionRule.query.filter_by(active=True).count(),
    )


@bp.route("/catalog/<category>/add", methods=["POST"])
@login_required
@roles_required("superadmin", "admin", "manager")
def catalog_add(category):
    if category not in CATALOG_DEFINITIONS:
        abort(404)

    code = _normalize_code(request.form.get("code"))
    label = (request.form.get("label") or "").strip()
    sort_order = request.form.get("sort_order", type=int) or 0

    if not code or not label or not CODE_RE.fullmatch(code):
        flash("Código y nombre visible son obligatorios. Usa un código corto sin espacios.", "danger")
        return _redirect(category, "catalogs")

    allowed_codes = set(CATALOG_DEFINITIONS[category].get("allowed_codes", set()))
    if allowed_codes and code not in allowed_codes:
        flash(
            "Ese catálogo usa códigos operativos predefinidos. Puedes renombrar, ordenar o activar/desactivar los existentes.",
            "danger",
        )
        return _redirect(category, "catalogs")

    existing = CatalogItem.query.filter_by(category=category, code=code).first()
    if existing:
        flash("Ya existe ese código dentro del catálogo.", "warning")
        return _redirect(category, "catalogs")

    row = CatalogItem(
        category=category,
        code=code,
        label=label,
        sort_order=sort_order,
        active=True,
    )
    db.session.add(row)
    db.session.flush()
    audit(
        "crear_valor_catalogo",
        "CatalogItem",
        row.id,
        after={
            "category": category,
            "code": code,
            "label": label,
            "sort_order": sort_order,
        },
    )
    db.session.commit()
    refresh_runtime_catalogs(force=True)
    flash("Valor agregado al catálogo.", "success")
    return _redirect(category, "catalogs")


@bp.route("/catalog/<int:item_id>/update", methods=["POST"])
@login_required
@roles_required("superadmin", "admin", "manager")
def catalog_update(item_id):
    row = db.get_or_404(CatalogItem, item_id)
    if row.category not in CATALOG_DEFINITIONS:
        abort(404)

    label = (request.form.get("label") or "").strip()
    if not label:
        flash("El nombre visible no puede quedar vacío.", "danger")
        return _redirect(row.category, "catalogs")

    before = {
        "label": row.label,
        "sort_order": row.sort_order,
        "active": row.active,
    }
    row.label = label
    row.sort_order = request.form.get("sort_order", type=int) or 0

    audit(
        "editar_valor_catalogo",
        "CatalogItem",
        row.id,
        before=before,
        after={
            "label": row.label,
            "sort_order": row.sort_order,
            "active": row.active,
        },
    )
    db.session.commit()
    refresh_runtime_catalogs(force=True)
    flash("Catálogo actualizado.", "success")
    return _redirect(row.category, "catalogs")


@bp.route("/catalog/<int:item_id>/toggle", methods=["POST"])
@login_required
@roles_required("superadmin", "admin", "manager")
def catalog_toggle(item_id):
    row = db.get_or_404(CatalogItem, item_id)
    if row.category not in CATALOG_DEFINITIONS:
        abort(404)

    if row.active and is_protected_catalog_code(row.category, row.code):
        flash(
            "Ese valor es esencial para la lógica del sistema. Puedes cambiar su nombre visible u orden, pero no desactivarlo.",
            "warning",
        )
        return _redirect(row.category, "catalogs")

    before = {"active": row.active}
    row.active = not row.active
    audit(
        "activar_valor_catalogo" if row.active else "desactivar_valor_catalogo",
        "CatalogItem",
        row.id,
        before=before,
        after={"active": row.active},
    )
    db.session.commit()
    refresh_runtime_catalogs(force=True)
    flash("Estado del catálogo actualizado.", "success")
    return _redirect(row.category, "catalogs")


@bp.route("/policies", methods=["POST"])
@login_required
@roles_required("superadmin", "admin", "manager")
def policies_update():
    tolerance = _parse_int(request.form.get("attendance_default_tolerance_minutes"), 10, 0, 240)
    break_minutes = _parse_int(request.form.get("attendance_default_break_minutes"), 30, 0, 240)
    lunch_minutes = _parse_int(request.form.get("attendance_default_lunch_minutes"), 60, 0, 240)

    raw_rate = (request.form.get("vacation_default_rate") or "0").strip()
    try:
        vacation_rate = Decimal(raw_rate)
        if vacation_rate < 0:
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        flash("La tasa de vacaciones no es válida.", "danger")
        return _redirect(anchor="policies")

    renewal_days = []
    for raw in (request.form.get("renewal_alert_days") or "").split(","):
        raw = raw.strip()
        if not raw:
            continue
        try:
            value = int(raw)
        except ValueError:
            flash("Los días de recordatorio deben ser enteros separados por coma.", "danger")
            return _redirect(anchor="policies")
        if value < 0 or value > 365:
            flash("Los recordatorios deben estar entre 0 y 365 días.", "danger")
            return _redirect(anchor="policies")
        renewal_days.append(value)

    if not renewal_days:
        renewal_days = [60, 30, 15, 7, 0]
    renewal_days = sorted(set(renewal_days), reverse=True)

    currency_default = (request.form.get("currency_default") or "USD").strip()
    active_currencies = {
        item.code.upper()
        for item in CatalogItem.query.filter_by(category="currency", active=True).all()
    }
    if currency_default not in active_currencies:
        flash("Selecciona una moneda predeterminada activa.", "danger")
        return _redirect(anchor="policies")

    values = {
        "attendance_default_tolerance_minutes": (
            tolerance,
            "Tolerancia global sugerida al crear horarios.",
        ),
        "attendance_default_break_minutes": (
            break_minutes,
            "Break global sugerido al crear horarios.",
        ),
        "attendance_default_lunch_minutes": (
            lunch_minutes,
            "Almuerzo global sugerido al crear horarios.",
        ),
        "vacation_default_rate": (
            vacation_rate,
            "Tasa predeterminada de acumulación de vacaciones.",
        ),
        "vacation_policy_note": (
            (request.form.get("vacation_policy_note") or "").strip(),
            "Política general de vacaciones.",
        ),
        "attendance_policy_note": (
            (request.form.get("attendance_policy_note") or "").strip(),
            "Política general de asistencia.",
        ),
        "renewal_alert_days": (
            ",".join(str(value) for value in renewal_days),
            "Días previos para alertas de renovación.",
        ),
        "currency_default": (
            currency_default,
            "Moneda predeterminada del sistema.",
        ),
    }

    before = {key: system_setting(key) for key in values}
    for key, (value, description) in values.items():
        save_system_setting(key, value, description)

    audit(
        "actualizar_politicas_operativas",
        "SystemSetting",
        before=before,
        after={key: str(value[0]) for key, value in values.items()},
    )
    db.session.commit()
    refresh_runtime_catalogs(force=True)
    flash("Políticas operativas actualizadas.", "success")
    return _redirect(anchor="policies")


@bp.route("/holidays/add", methods=["POST"])
@login_required
@roles_required("superadmin", "admin", "manager")
def holiday_add():
    holiday_date = _parse_date(request.form.get("holiday_date"))
    name = (request.form.get("name") or "").strip()

    if not holiday_date or not name:
        flash("Fecha y nombre del feriado son obligatorios.", "danger")
        return _redirect(anchor="holidays")

    if Holiday.query.filter_by(holiday_date=holiday_date).first():
        flash("Ya existe un feriado para esa fecha.", "warning")
        return _redirect(anchor="holidays")

    row = Holiday(
        holiday_date=holiday_date,
        name=name,
        non_working=bool(request.form.get("non_working")),
    )
    db.session.add(row)
    db.session.flush()
    audit(
        "crear_feriado",
        "Holiday",
        row.id,
        after={
            "date": str(row.holiday_date),
            "name": row.name,
            "non_working": row.non_working,
        },
    )
    db.session.commit()
    flash("Feriado agregado.", "success")
    return _redirect(anchor="holidays")


@bp.route("/holidays/<int:holiday_id>/update", methods=["POST"])
@login_required
@roles_required("superadmin", "admin", "manager")
def holiday_update(holiday_id):
    row = db.get_or_404(Holiday, holiday_id)
    holiday_date = _parse_date(request.form.get("holiday_date"))
    name = (request.form.get("name") or "").strip()

    if not holiday_date or not name:
        flash("Fecha y nombre son obligatorios.", "danger")
        return _redirect(anchor="holidays")

    duplicate = Holiday.query.filter(
        Holiday.holiday_date == holiday_date,
        Holiday.id != row.id,
    ).first()
    if duplicate:
        flash("Ya existe otro feriado para esa fecha.", "warning")
        return _redirect(anchor="holidays")

    before = {
        "date": str(row.holiday_date),
        "name": row.name,
        "non_working": row.non_working,
    }
    row.holiday_date = holiday_date
    row.name = name
    row.non_working = bool(request.form.get("non_working"))

    audit(
        "editar_feriado",
        "Holiday",
        row.id,
        before=before,
        after={
            "date": str(row.holiday_date),
            "name": row.name,
            "non_working": row.non_working,
        },
    )
    db.session.commit()
    flash("Feriado actualizado.", "success")
    return _redirect(anchor="holidays")


@bp.route("/task-templates/add", methods=["POST"])
@login_required
@roles_required("superadmin", "admin", "manager")
def task_template_add():
    name = (request.form.get("name") or "").strip()
    product_id = request.form.get("product_id", type=int)

    if not name:
        flash("El nombre de la plantilla es obligatorio.", "danger")
        return _redirect(anchor="templates")

    if product_id and not db.session.get(ProductService, product_id):
        abort(400)

    row = TaskTemplate(
        name=name,
        product_id=product_id,
        active=True,
    )
    db.session.add(row)
    db.session.flush()
    audit(
        "crear_plantilla_tareas",
        "TaskTemplate",
        row.id,
        after={"name": row.name, "product_id": row.product_id},
    )
    db.session.commit()
    flash("Plantilla creada.", "success")
    return _redirect(anchor="templates")


@bp.route("/task-templates/<int:template_id>/toggle", methods=["POST"])
@login_required
@roles_required("superadmin", "admin", "manager")
def task_template_toggle(template_id):
    row = db.get_or_404(TaskTemplate, template_id)
    before = {"active": row.active}
    row.active = not row.active
    audit(
        "activar_plantilla_tareas" if row.active else "desactivar_plantilla_tareas",
        "TaskTemplate",
        row.id,
        before=before,
        after={"active": row.active},
    )
    db.session.commit()
    flash("Estado de plantilla actualizado.", "success")
    return _redirect(anchor="templates")


@bp.route("/task-templates/<int:template_id>/items/add", methods=["POST"])
@login_required
@roles_required("superadmin", "admin", "manager")
def task_template_item_add(template_id):
    template = db.get_or_404(TaskTemplate, template_id)
    title = (request.form.get("title") or "").strip()
    if not title:
        flash("El título de la tarea es obligatorio.", "danger")
        return _redirect(anchor="templates")

    task_type = (request.form.get("task_type") or "cliente").strip()
    priority = (request.form.get("priority") or "media").strip()
    active_task_types = {
        row.code
        for row in CatalogItem.query.filter_by(category="task_type", active=True).all()
    }
    active_priorities = {
        row.code
        for row in CatalogItem.query.filter_by(category="priority", active=True).all()
    }
    if task_type not in active_task_types:
        task_type = "cliente"
    if priority not in active_priorities:
        priority = "media"

    row = TaskTemplateItem(
        template_id=template.id,
        title=title,
        description=(request.form.get("description") or "").strip() or None,
        task_type=task_type,
        priority=priority,
        due_days=_parse_int(request.form.get("due_days"), 3, 0, 365),
        required=bool(request.form.get("required")),
    )
    db.session.add(row)
    db.session.flush()
    audit(
        "agregar_tarea_plantilla",
        "TaskTemplateItem",
        row.id,
        after={
            "template_id": template.id,
            "title": row.title,
            "task_type": row.task_type,
            "priority": row.priority,
        },
    )
    db.session.commit()
    flash("Tarea agregada a la plantilla.", "success")
    return _redirect(anchor="templates")


@bp.route("/task-template-items/<int:item_id>/delete", methods=["POST"])
@login_required
@roles_required("superadmin", "admin", "manager")
def task_template_item_delete(item_id):
    row = db.get_or_404(TaskTemplateItem, item_id)
    before = {
        "template_id": row.template_id,
        "title": row.title,
        "task_type": row.task_type,
        "priority": row.priority,
    }
    audit(
        "eliminar_tarea_plantilla",
        "TaskTemplateItem",
        row.id,
        before=before,
        reason="Eliminación de elemento de configuración",
    )
    db.session.delete(row)
    db.session.commit()
    flash("Elemento eliminado de la plantilla.", "warning")
    return _redirect(anchor="templates")
