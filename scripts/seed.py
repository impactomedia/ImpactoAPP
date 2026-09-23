import os
from datetime import date
from app.extensions import db
from app.models import Role, Permission, User, ProductService, CatalogItem, SystemSetting, AdvisorProject

PERMISSIONS = {
    "crm": ["crm.view", "crm.create", "crm.edit", "crm.transfer", "crm.export"],
    "clients": ["clients.view", "clients.create", "clients.edit", "clients.assign", "clients.export"],
    "sales": ["sales.view", "sales.create", "sales.edit", "sales.payment"],
    "finance": ["finance.view", "finance.edit", "finance.commission", "finance.payroll"],
    "operations": ["projects.view", "projects.edit", "tasks.view", "tasks.edit"],
    "printing": ["printing.view", "printing.edit", "printing.approve"],
    "hr": ["hr.view", "hr.edit", "attendance.edit", "leave.approve", "hr.sensitive"],
    "support": ["support.view", "support.edit"],
    "reports": ["reports.view", "reports.export"],
    "settings": ["settings.view", "settings.edit", "audit.view"],
}

ROLE_MAP = {
    "superadmin": "Superadministrador",
    "admin": "Administración",
    "manager": "Gerencia",
    "hr": "Recursos Humanos",
    "supervisor": "Supervisor / Coordinador",
    "advisor": "Asesor Comercial",
    "production": "Operaciones / Producción",
    "finance": "Contabilidad / Finanzas",
    "audit": "Consulta / Auditoría",
}

ADVISOR_PROJECTS = [
    {"code": "IMPACTO", "name": "IMPACTO", "description": "Proyecto comercial IMPACTO"},
    {"code": "NOVAX", "name": "NOVAX", "description": "Proyecto comercial NOVAX"},
]


ROLE_MODULES = {
    "admin": set(PERMISSIONS),
    "manager": set(PERMISSIONS),
    "hr": {"hr", "operations", "reports"},
    "supervisor": {"crm", "clients", "sales", "operations", "printing", "support", "reports", "hr"},
    "advisor": {"crm", "clients", "sales", "operations", "printing", "support", "reports"},
    "production": {"clients", "operations", "printing", "support", "reports"},
    "finance": {"sales", "finance", "reports", "clients"},
    "audit": {"reports", "settings"},
}

PRODUCTS = [
    dict(name="3 Meses", category="paquete", duration_months=3, modality="inquilino", maintenance="no_aplica", renewal_required=True),
    dict(name="6 Meses", category="paquete", duration_months=6, modality="inquilino", maintenance="no_aplica", renewal_required=True),
    dict(name="8 Meses", category="paquete", duration_months=8, modality="inquilino", maintenance="no_aplica", renewal_required=True),
    dict(name="Golden", category="paquete", modality="dueno", maintenance="no_incluido", renewal_required=False),
    dict(name="Premium", category="paquete", modality="dueno", maintenance="incluido", renewal_required=True),
    dict(name="VIP", category="paquete", modality="dueno", maintenance="incluido", renewal_required=True),
    dict(name="Accesorios", category="accesorios", modality=None, maintenance="no_aplica", is_physical=True),
    dict(name="Website", category="servicio", responsible_area="Desarrollo", renewal_required=False),
    dict(name="Google Business Profile", category="servicio", responsible_area="SEO", renewal_required=False),
    dict(name="Redes Sociales", category="servicio", responsible_area="Social Media", renewal_required=True),
    dict(name="SEO Local", category="servicio", responsible_area="SEO", renewal_required=True),
    dict(name="Branding / Logo", category="servicio", responsible_area="Diseño", renewal_required=False),
    dict(name="Material Impreso", category="imprenta", responsible_area="Imprenta", is_physical=True),
]

CATALOGS = {
    "payment_method": ["efectivo", "transferencia", "ACH", "Wise", "Zelle", "tarjeta", "otro"],
    "prospect_source": ["Facebook", "Instagram", "Google", "Website", "WhatsApp", "Referido", "Llamada", "Evento", "Otro"],
    "expense_category": ["Nómina", "Comisión", "Publicidad", "Hosting", "Dominios", "Software", "Imprenta", "Envíos", "Equipo", "Reembolso", "Servicios", "Otros"],
    "task_type": ["Interna", "Cliente", "Venta", "Diseño", "Website", "SEO", "Impresión", "Administración"],
    "ticket_type": ["Soporte", "Cambio de información", "Website", "Redes", "Pago", "Acceso", "Diseño", "Impresión", "Otro"],
}


def seed_all():
    permissions_by_module = {}
    for module, codes in PERMISSIONS.items():
        permissions_by_module[module] = []
        for code in codes:
            p = Permission.query.filter_by(code=code).first()
            if not p:
                p = Permission(code=code, label=code.replace(".", " ").title(), module=module)
                db.session.add(p)
                db.session.flush()
            permissions_by_module[module].append(p)

    roles = {}
    for name, label in ROLE_MAP.items():
        role = Role.query.filter_by(name=name).first()
        if not role:
            role = Role(name=name, label=label, active=True)
            db.session.add(role)
            db.session.flush()
        roles[name] = role

    all_permissions = Permission.query.all()
    roles["superadmin"].permissions = all_permissions
    for role_name, modules in ROLE_MODULES.items():
        role = roles[role_name]
        role.permissions = [p for p in all_permissions if p.module in modules]

    for item in ADVISOR_PROJECTS:
        row = AdvisorProject.query.filter_by(code=item["code"]).first()
        if not row:
            db.session.add(AdvisorProject(**item, active=True))

    for source in PRODUCTS:
        data = dict(source)
        name = data.pop("name")
        row = ProductService.query.filter_by(name=name).first()
        if not row:
            row = ProductService(name=name, active=True, **data)
            db.session.add(row)

    for category, labels in CATALOGS.items():
        for idx, label in enumerate(labels):
            code = label.lower().replace(" ", "_").replace("/", "_")
            if not CatalogItem.query.filter_by(category=category, code=code).first():
                db.session.add(CatalogItem(category=category, code=code, label=label, sort_order=idx, active=True))

    defaults = {
        "currency_default": "USD",
        "renewal_alert_days": "60,30,15,7,0",
        "company_timezone": "America/Managua",
        "attendance_policy_note": "Configurar tolerancias, breaks y horas extra según política interna.",
        "commission_policy_note": "Configurar reglas exactas antes de liquidar comisiones.",
    }
    for key, value in defaults.items():
        if not SystemSetting.query.filter_by(key=key).first():
            db.session.add(SystemSetting(key=key, value=value))

    admin_email = os.getenv("ADMIN_EMAIL", "admin@impactomedia.local").strip().lower()
    admin = User.query.filter_by(email=admin_email).first()
    if not admin:
        admin = User(name=os.getenv("ADMIN_NAME", "Administrador General"), email=admin_email, role=roles["superadmin"], active=True)
        admin.set_password(os.getenv("ADMIN_PASSWORD", "ChangeMe123!"))
        db.session.add(admin)
    db.session.commit()
