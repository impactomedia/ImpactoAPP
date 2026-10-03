import time
import unicodedata
from collections import OrderedDict

from flask import has_app_context
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import CatalogItem, SystemSetting


CATALOG_DEFINITIONS = OrderedDict([
    ("department", {
        "label": "Áreas / departamentos",
        "storage": "label",
        "defaults": [
            ("administracion", "Administración"),
            ("ventas", "Ventas"),
            ("desarrollo", "Desarrollo"),
            ("diseno", "Diseño"),
            ("seo", "SEO"),
            ("social_media", "Social Media"),
            ("imprenta", "Imprenta"),
            ("logistica", "Logística"),
            ("recursos_humanos", "Recursos Humanos"),
            ("finanzas", "Finanzas"),
        ],
    }),
    ("job_title", {
        "label": "Cargos",
        "storage": "label",
        "defaults": [
            ("manager", "Manager"),
            ("supervisor", "Supervisor"),
            ("asesor_comercial", "Asesor Comercial"),
            ("coordinador", "Coordinador"),
            ("disenador", "Diseñador"),
            ("desarrollador", "Desarrollador"),
            ("produccion", "Producción"),
            ("rrhh", "Recursos Humanos"),
            ("finanzas", "Finanzas"),
        ],
    }),
    ("pipeline_stage", {
        "label": "Estados de prospecto / pipeline",
        "storage": "code",
        "protected": {"nuevo", "venta_cerrada", "perdido"},
        "defaults": [
            ("nuevo", "Nuevo"),
            ("intento_contacto", "Intento de contacto"),
            ("contactado", "Contactado"),
            ("calificado", "Calificado"),
            ("interesado", "Interesado"),
            ("seguimiento", "Seguimiento"),
            ("cotizacion_enviada", "Cotización enviada"),
            ("negociacion", "Negociación"),
            ("pendiente_pago", "Pendiente de pago"),
            ("venta_cerrada", "Venta cerrada"),
            ("perdido", "Perdido"),
        ],
    }),
    ("prospect_source", {
        "label": "Fuentes de prospectos",
        "storage": "label",
        "defaults": [
            ("llamada_en_frio", "Llamada en frío"),
            ("facebook", "Facebook"),
            ("instagram", "Instagram"),
            ("google", "Google"),
            ("website", "Website"),
            ("whatsapp", "WhatsApp"),
            ("referido", "Referido"),
            ("evento", "Evento"),
            ("importado", "Importado"),
            ("otro", "Otro"),
        ],
    }),
    ("lost_reason", {
        "label": "Motivos de pérdida",
        "storage": "label",
        "defaults": [
            ("precio", "Precio"),
            ("sin_respuesta", "Sin respuesta"),
            ("no_interesado", "No interesado"),
            ("competencia", "Eligió competencia"),
            ("pospuesto", "Pospuesto"),
            ("fuera_de_perfil", "Fuera de perfil"),
            ("otro", "Otro"),
        ],
    }),
    ("priority", {
        "label": "Prioridades comerciales / tareas",
        "storage": "code",
        "protected": {"media"},
        "defaults": [
            ("baja", "Baja"),
            ("media", "Media"),
            ("alta", "Alta"),
            ("urgente", "Urgente"),
        ],
    }),
    ("interaction_type", {
        "label": "Tipos de interacción",
        "storage": "code",
        "protected": {"nota"},
        "defaults": [
            ("llamada", "Llamada"),
            ("whatsapp", "WhatsApp"),
            ("email", "Correo electrónico"),
            ("reunion", "Reunión"),
            ("nota", "Nota"),
        ],
    }),
    ("payment_method", {
        "label": "Métodos de pago",
        "storage": "label",
        "protected": {"transferencia"},
        "defaults": [
            ("efectivo", "efectivo"),
            ("transferencia", "transferencia"),
            ("ach", "ACH"),
            ("wise", "Wise"),
            ("zelle", "Zelle"),
            ("tarjeta", "tarjeta"),
            ("otro", "otro"),
        ],
    }),
    ("currency", {
        "label": "Monedas",
        "storage": "upper_code",
        "protected": {"usd"},
        "allowed_codes": {"usd", "nio"},
        "defaults": [
            ("usd", "USD"),
        ],
    }),
    ("expense_category", {
        "label": "Categorías de egresos",
        "storage": "label",
        "defaults": [
            ("nomina", "Nómina"),
            ("comision", "Comisión"),
            ("publicidad", "Publicidad"),
            ("hosting", "Hosting"),
            ("dominios", "Dominios"),
            ("software", "Software"),
            ("imprenta", "Imprenta"),
            ("envios", "Envíos"),
            ("equipo", "Equipo"),
            ("reembolso", "Reembolso"),
            ("servicios", "Servicios"),
            ("otros", "Otros"),
        ],
    }),
    ("income_category", {
        "label": "Categorías de ingresos",
        "storage": "label",
        "defaults": [
            ("ventas", "Ventas"),
            ("renovaciones", "Renovaciones"),
            ("mantenimiento", "Mantenimiento"),
            ("imprenta", "Imprenta"),
            ("otros", "Otros"),
        ],
    }),
    ("provider", {
        "label": "Proveedores",
        "storage": "label",
        "defaults": [],
    }),
    ("task_type", {
        "label": "Tipos de tarea",
        "storage": "code",
        "protected": {"cliente"},
        "defaults": [
            ("interna", "Interna"),
            ("cliente", "Cliente"),
            ("venta", "Venta"),
            ("diseno", "Diseño"),
            ("website", "Website"),
            ("seo", "SEO"),
            ("impresion", "Impresión"),
            ("administracion", "Administración"),
        ],
    }),
    ("project_status", {
        "label": "Estados de proyecto",
        "storage": "code",
        "protected": {"pendiente_onboarding", "completado", "cancelado"},
        "defaults": [
            ("pendiente_onboarding", "Pendiente onboarding"),
            ("recopilando_informacion", "Recopilando información"),
            ("listo_iniciar", "Listo para iniciar"),
            ("en_produccion", "En producción"),
            ("esperando_cliente", "Esperando cliente"),
            ("revision_interna", "Revisión interna"),
            ("aprobacion_cliente", "Aprobación del cliente"),
            ("correcciones", "Correcciones"),
            ("entregado", "Entregado"),
            ("mantenimiento", "Mantenimiento"),
            ("completado", "Completado"),
            ("cancelado", "Cancelado"),
        ],
    }),
    ("task_status", {
        "label": "Estados de tarea",
        "storage": "code",
        "protected": {"pendiente", "completada", "cancelada"},
        "defaults": [
            ("pendiente", "Pendiente"),
            ("en_proceso", "En proceso"),
            ("bloqueada", "Bloqueada"),
            ("en_revision", "En revisión"),
            ("completada", "Completada"),
            ("cancelada", "Cancelada"),
        ],
    }),
    ("ticket_type", {
        "label": "Tipos de ticket",
        "storage": "code",
        "protected": {"soporte", "otro"},
        "defaults": [
            ("soporte", "Soporte"),
            ("cambio_informacion", "Cambio de información"),
            ("website", "Website"),
            ("redes", "Redes sociales"),
            ("pago", "Pago"),
            ("acceso", "Acceso"),
            ("diseno", "Diseño"),
            ("impresion", "Impresión"),
            ("otro", "Otro"),
        ],
    }),
    ("ticket_status", {
        "label": "Estados de ticket",
        "storage": "code",
        "protected": {"nuevo", "resuelto", "cerrado"},
        "defaults": [
            ("nuevo", "Nuevo"),
            ("asignado", "Asignado"),
            ("en_proceso", "En proceso"),
            ("esperando_cliente", "Esperando cliente"),
            ("resuelto", "Resuelto"),
            ("cerrado", "Cerrado"),
        ],
    }),
    ("support_priority", {
        "label": "Prioridades de soporte",
        "storage": "code",
        "protected": {"normal"},
        "allowed_codes": {"baja", "normal", "alta", "urgente"},
        "defaults": [
            ("baja", "Baja"),
            ("normal", "Normal"),
            ("alta", "Alta"),
            ("urgente", "Urgente"),
        ],
    }),
])

_CACHE = {
    "expires": 0.0,
    "options": {},
    "checked_defaults": False,
}
CACHE_SECONDS = 15


def _definition(category):
    return CATALOG_DEFINITIONS.get(category)


def _normalized_label(value):
    text = unicodedata.normalize("NFKD", str(value or "").strip().lower())
    return "".join(char for char in text if not unicodedata.combining(char))


def ensure_catalog_defaults():
    if not has_app_context():
        return False

    changed = False
    for category, definition in CATALOG_DEFINITIONS.items():
        existing_rows = CatalogItem.query.filter_by(category=category).all()
        existing = {row.code: row for row in existing_rows}
        existing_labels = {_normalized_label(row.label) for row in existing_rows}

        for index, (code, label) in enumerate(definition.get("defaults", [])):
            if code in existing or _normalized_label(label) in existing_labels:
                continue

            db.session.add(
                CatalogItem(
                    category=category,
                    code=code,
                    label=label,
                    active=True,
                    sort_order=index,
                )
            )
            existing_labels.add(_normalized_label(label))
            changed = True

    if changed:
        try:
            db.session.commit()
        except IntegrityError:
            # Varios workers de Gunicorn pueden intentar inicializar a la vez.
            # El índice único category+code evita duplicados; si otro worker ganó
            # la carrera, se revierte este intento y se continúa con sus datos.
            db.session.rollback()

    _CACHE["checked_defaults"] = True
    return changed


def _load_options():
    if not _CACHE["checked_defaults"]:
        ensure_catalog_defaults()

    result = {}
    rows = (
        CatalogItem.query
        .filter(CatalogItem.category.in_(list(CATALOG_DEFINITIONS)))
        .order_by(CatalogItem.category, CatalogItem.sort_order, CatalogItem.id)
        .all()
    )
    for category in CATALOG_DEFINITIONS:
        result[category] = []

    for row in rows:
        if not row.active or row.category not in CATALOG_DEFINITIONS:
            continue
        definition = CATALOG_DEFINITIONS[row.category]
        storage = definition.get("storage", "code")
        if storage == "label":
            value = row.label
        elif storage == "upper_code":
            value = row.code.upper()
        else:
            value = row.code
        result[row.category].append({
            "code": row.code,
            "label": row.label,
            "value": value,
            "sort_order": row.sort_order,
        })

    return result


def refresh_runtime_catalogs(force=False):
    now = time.monotonic()
    if force or now >= _CACHE["expires"]:
        _CACHE["options"] = _load_options()
        _CACHE["expires"] = now + CACHE_SECONDS

        # Los blueprints ya fueron importados antes de ejecutarse el before_request.
        from app.blueprints import crm, operations, sales, support

        crm.STAGES = [row["value"] for row in _CACHE["options"]["pipeline_stage"]]
        crm.PRIORITIES = {row["value"] for row in _CACHE["options"]["priority"]}
        crm.INTERACTION_TYPES = {row["value"] for row in _CACHE["options"]["interaction_type"]}
        crm.CURRENCIES = {row["value"] for row in _CACHE["options"]["currency"]} or {"USD"}

        sales.PAYMENT_METHODS = {row["value"] for row in _CACHE["options"]["payment_method"]}
        sales.CURRENCIES = {row["value"] for row in _CACHE["options"]["currency"]} or {"USD"}

        operations.PROJECT_STATES = [row["value"] for row in _CACHE["options"]["project_status"]]
        operations.TASK_STATES = [row["value"] for row in _CACHE["options"]["task_status"]]
        operations.TASK_PRIORITY_ORDER = [row["value"] for row in _CACHE["options"]["priority"]]
        operations.TASK_PRIORITIES = set(operations.TASK_PRIORITY_ORDER)

        # El SLA actual de soporte tiene semántica fija para estas cuatro claves.
        safe_support = [
            row["value"]
            for row in _CACHE["options"]["support_priority"]
            if row["value"] in {"baja", "normal", "alta", "urgente"}
        ]
        support.PRIORITIES = set(safe_support or ["baja", "normal", "alta", "urgente"])
        support.TICKET_TYPES = {row["value"] for row in _CACHE["options"]["ticket_type"]}
        support.TICKET_STATUSES = {row["value"] for row in _CACHE["options"]["ticket_status"]}

    return _CACHE["options"]


def catalog_options(category):
    options = refresh_runtime_catalogs()
    return list(options.get(category, []))


def catalog_values(category):
    return [row["value"] for row in catalog_options(category)]


def catalog_labels(category):
    return [row["label"] for row in catalog_options(category)]


def is_protected_catalog_code(category, code):
    definition = _definition(category) or {}
    return code in set(definition.get("protected", set()))


def system_setting(key, default=None):
    row = SystemSetting.query.filter_by(key=key).first()
    if not row or row.value in (None, ""):
        return default
    return row.value


def save_system_setting(key, value, description=None):
    row = SystemSetting.query.filter_by(key=key).first()
    if not row:
        row = SystemSetting(key=key)
        db.session.add(row)
    row.value = "" if value is None else str(value)
    if description is not None:
        row.description = description
    return row


def init_catalog_runtime(app):
    # create_app() puede ejecutarse varias veces durante pytest con bases distintas.
    # Nunca reutilizar cache de una instancia Flask anterior.
    _CACHE["expires"] = 0.0
    _CACHE["options"] = {}
    _CACHE["checked_defaults"] = False

    @app.before_request
    def refresh_configurable_catalogs():
        refresh_runtime_catalogs()
        return None
