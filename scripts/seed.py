import os
from app.extensions import db
from app.models import Role, Permission, User, ProductService, CatalogItem, SystemSetting, AdvisorProject

PERMISSIONS = {
    "crm": ["crm.view", "crm.create", "crm.edit", "crm.transfer", "crm.export"],
    "clients": [
        "clients.view",
        "clients.create",
        "clients.edit",
        "clients.assign",
        "clients.comment",
        "clients.files",
        "clients.export",
    ],
    "sales": ["sales.view", "sales.create", "sales.edit", "sales.payment"],
    "finance": ["finance.view", "finance.edit", "finance.commission", "finance.payroll"],
    "operations": [
        "projects.view",
        "projects.edit",
        "projects.progress",
        "tasks.view",
        "tasks.edit",
        "tasks.update",
        "development.manage",
    ],
    "printing": ["printing.view", "printing.edit", "printing.approve"],
    "hr": ["hr.view", "hr.edit", "attendance.edit", "leave.approve", "hr.sensitive"],
    "support": ["support.view", "support.edit"],
    "reports": ["reports.view", "reports.export"],
    "settings": ["settings.view", "settings.edit", "audit.view"],
}

PERMISSION_LABELS = {
    "crm.view": "Ver CRM y prospectos",
    "crm.create": "Crear prospectos",
    "crm.edit": "Editar seguimientos y cotizaciones",
    "crm.transfer": "Transferir responsables comerciales",
    "crm.export": "Exportar CRM",
    "clients.view": "Ver clientes",
    "clients.create": "Crear e importar clientes",
    "clients.edit": "Editar clientes",
    "clients.assign": "Asignar colaboradores a clientes",
    "clients.comment": "Agregar notas operativas de clientes",
    "clients.files": "Cargar y consultar evidencias operativas de clientes",
    "clients.export": "Exportar clientes",
    "sales.view": "Ver ventas y renovaciones",
    "sales.create": "Crear ventas",
    "sales.edit": "Editar ventas, deducciones y renovaciones",
    "sales.payment": "Registrar pagos",
    "finance.view": "Ver finanzas",
    "finance.edit": "Registrar y editar movimientos financieros",
    "finance.commission": "Gestionar comisiones",
    "finance.payroll": "Gestionar nómina",
    "projects.view": "Ver proyectos",
    "projects.edit": "Crear y administrar proyectos",
    "projects.progress": "Actualizar estado y progreso de proyectos asignados",
    "tasks.view": "Ver tareas",
    "tasks.edit": "Crear y administrar tareas",
    "tasks.update": "Actualizar estado y comentarios de tareas asignadas",
    "development.manage": "Coordinar y supervisar el área de Desarrollo",
    "printing.view": "Ver órdenes de imprenta",
    "printing.edit": "Gestionar órdenes y envíos de imprenta",
    "printing.approve": "Aprobar diseños y envíos",
    "hr.view": "Ver Recursos Humanos",
    "hr.edit": "Editar expedientes y configuración de RR. HH.",
    "attendance.edit": "Corregir marcaciones",
    "leave.approve": "Aprobar vacaciones y permisos",
    "hr.sensitive": "Ver y gestionar información sensible de RR. HH.",
    "support.view": "Ver tickets de soporte",
    "support.edit": "Crear y gestionar tickets de soporte",
    "reports.view": "Ver reportes",
    "reports.export": "Exportar reportes",
    "settings.view": "Ver configuración",
    "settings.edit": "Editar configuración",
    "audit.view": "Ver bitácora de auditoría",
}

ROLE_MAP = {
    "superadmin": "Superadministrador",
    "admin": "Administración",
    "manager": "Gerencia",
    "hr": "Recursos Humanos",
    "supervisor": "Supervisor / Coordinador",
    "advisor": "Asesor Comercial",
    "development_coordinator": "Coordinador de Desarrollo",
    "production": "Operaciones / Producción",
    "finance": "Contabilidad / Finanzas",
    "audit": "Consulta / Auditoría",
}

ADVISOR_PROJECTS = [
    {"code": "IMPACTO", "name": "IMPACTO", "description": "Proyecto comercial IMPACTO"},
    {"code": "NOVAX", "name": "NOVAX", "description": "Proyecto comercial NOVAX"},
]

# Permisos por función real. Superadmin, administración y gerencia conservan acceso total.
ROLE_PERMISSION_CODES = {
    "admin": "ALL",
    "manager": "ALL",
    "hr": {
        "hr.view", "hr.edit", "attendance.edit", "leave.approve", "hr.sensitive",
        "finance.payroll",
        "tasks.view", "tasks.edit", "tasks.update",
        "reports.view", "reports.export",
    },
    "supervisor": {
        "crm.view", "crm.create", "crm.edit", "crm.transfer", "crm.export",
        "clients.view", "clients.create", "clients.edit", "clients.assign",
        "clients.comment", "clients.files", "clients.export",
        "sales.view", "sales.create", "sales.edit", "sales.payment",
        "projects.view", "projects.edit", "projects.progress",
        "tasks.view", "tasks.edit", "tasks.update",
        "printing.view", "printing.edit", "printing.approve",
        "support.view", "support.edit",
        "reports.view", "reports.export",
        "hr.view", "attendance.edit", "leave.approve",
    },
    "advisor": {
        "crm.view", "crm.create", "crm.edit", "crm.export",
        "clients.view", "clients.create", "clients.edit",
        "clients.comment", "clients.files", "clients.export",
        "sales.view", "sales.create", "sales.edit", "sales.payment",
        "projects.view", "tasks.view",
        "printing.view", "printing.edit", "printing.approve",
        "support.view", "support.edit",
        "reports.view",
    },
    "development_coordinator": {
        "clients.view", "clients.assign", "clients.comment", "clients.files",
        "projects.view", "projects.edit", "projects.progress",
        "tasks.view", "tasks.edit", "tasks.update",
        "development.manage",
    },
    "production": {
        "clients.view", "clients.comment", "clients.files",
        "projects.view", "projects.progress",
        "tasks.view", "tasks.update",
        "printing.view", "printing.edit", "printing.approve",
        "support.view", "support.edit",
        "reports.view",
    },
    "finance": {
        "clients.view", "clients.export",
        "sales.view", "sales.edit", "sales.payment",
        "finance.view", "finance.edit", "finance.commission", "finance.payroll",
        "reports.view", "reports.export",
    },
    "audit": {
        "reports.view", "reports.export",
        "settings.view", "audit.view",
    },
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
    for module, codes in PERMISSIONS.items():
        for code in codes:
            permission = Permission.query.filter_by(code=code).first()
            if not permission:
                permission = Permission(code=code, label=PERMISSION_LABELS.get(code, code), module=module)
                db.session.add(permission)
                db.session.flush()
            else:
                permission.label = PERMISSION_LABELS.get(code, permission.label)
                permission.module = module

    roles = {}
    for name, label in ROLE_MAP.items():
        role = Role.query.filter_by(name=name).first()
        if not role:
            role = Role(name=name, label=label, active=True)
            db.session.add(role)
            db.session.flush()
        else:
            role.label = label
        roles[name] = role

    all_permissions = Permission.query.all()
    permissions_by_code = {permission.code: permission for permission in all_permissions}

    roles["superadmin"].permissions = all_permissions
    for role_name, configured in ROLE_PERMISSION_CODES.items():
        role = roles[role_name]
        if configured == "ALL":
            role.permissions = all_permissions
        else:
            role.permissions = [permissions_by_code[code] for code in configured if code in permissions_by_code]

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
        "attendance_policy_note": "Configurar tolerancias, pausas y horas extra según política interna.",
        "commission_policy_note": "Configurar reglas exactas antes de liquidar comisiones.",
    }
    for key, value in defaults.items():
        if not SystemSetting.query.filter_by(key=key).first():
            db.session.add(SystemSetting(key=key, value=value))

    admin_email = os.getenv("ADMIN_EMAIL", "admin@impactomedia.local").strip().lower()
    admin = User.query.filter_by(email=admin_email).first()
    if not admin:
        admin = User(
            name=os.getenv("ADMIN_NAME", "Administrador General"),
            email=admin_email,
            role=roles["superadmin"],
            active=True,
        )
        admin.set_password(os.getenv("ADMIN_PASSWORD", "ChangeMe123!"))
        db.session.add(admin)

    db.session.commit()
