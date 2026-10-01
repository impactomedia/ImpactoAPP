from flask import abort, request
from flask_login import current_user


DEFAULT_VIEW_PERMISSIONS = {
    "crm": "crm.view",
    "clients": "clients.view",
    "sales": "sales.view",
    "printing": "printing.view",
    "finance": "finance.view",
    "hr": "hr.view",
    "support": "support.view",
    "reports": "reports.view",
    "settings": "settings.view",
}

SELF_SERVICE_ENDPOINTS = {
    "auth.login",
    "auth.logout",
    "auth.forgot",
    "auth.reset_password",
    "auth.profile",
    "dashboard.index",
    "dashboard.search",
    "dashboard.read_notification",
    "hr.my_day",
    "hr.mark",
    "hr.leave",
}

ENDPOINT_PERMISSIONS = {
    # CRM
    "crm.new_prospect": "crm.create",
    "crm.move_stage": "crm.edit",
    "crm.add_interaction": "crm.edit",
    "crm.transfer": "crm.transfer",
    "crm.new_quote": "crm.edit",

    # Clientes
    "clients.new_client": "clients.create",
    "clients.edit": "clients.edit",
    "clients.convert_to_client": "clients.edit",
    "clients.archive": "clients.edit",
    "clients.restore": "clients.edit",
    "clients.delete_client": "clients.edit",
    "clients.assign_collaborator": "clients.assign",
    "clients.remove_assignment": "clients.assign",
    "clients.add_contract": "clients.edit",
    "clients.add_comment": "clients.edit",
    "clients.export_csv": "clients.export",
    "clients.import_csv": "clients.create",
    "clients.upload_attachment": "clients.edit",
    "clients.update_operational_profile": "clients.edit",
    "clients.save_platform": "clients.edit",
    "clients.delete_platform": "clients.edit",
    "clients.update_contract": "clients.edit",

    # Ventas
    "sales.new_sale": "sales.create",
    "sales.from_quote": "sales.create",
    "sales.payment": "sales.payment",
    "sales.deduction": "sales.edit",
    "sales.reverse_payment": "sales.edit",
    "sales.renewal_update": "sales.edit",

    # Imprenta
    "printing.new_order": "printing.edit",
    "printing.upload_design": "printing.edit",
    "printing.design_decision": "printing.approve",
    "printing.shipping_approval": "printing.approve",
    "printing.create_shipment": "printing.edit",
    "printing.receive_shipment": "printing.edit",
    "printing.incident": "printing.edit",
    "printing.resolve_incident": "printing.edit",

    # Reportes
    "reports.export_csv": "reports.export",

    # Configuración / auditoría
    "settings.audit_log": "audit.view",
}

METHOD_PERMISSIONS = {
    # Soporte
    ("support.index", "POST"): "support.edit",
    ("support.update", "POST"): "support.edit",
    ("support.comment", "POST"): "support.edit",

    # Finanzas
    ("finance.expenses", "POST"): "finance.edit",
    ("finance.promise", "POST"): "finance.edit",
    ("finance.payables", "POST"): "finance.edit",
    ("finance.commission_rules", "POST"): "finance.commission",
    ("finance.commission_action", "POST"): "finance.commission",
    ("finance.payroll", "GET"): "finance.payroll",
    ("finance.payroll", "POST"): "finance.payroll",
    ("finance.payroll_detail", "GET"): "finance.payroll",
    ("finance.payroll_pay", "POST"): "finance.payroll",
    ("finance.incomes", "POST"): "finance.edit",
    ("finance.payable_payment", "POST"): "finance.edit",
    ("finance.payroll_adjust", "POST"): "finance.payroll",

    # RR. HH.
    ("hr.collaborators", "POST"): "hr.edit",
    ("hr.collaborator_detail", "POST"): "hr.edit",
    ("hr.schedules", "POST"): "hr.edit",
    ("hr.assign_schedule", "POST"): "hr.edit",
    ("hr.leave_admin", "GET"): "leave.approve",
    ("hr.leave_decision", "POST"): "leave.approve",
    ("hr.upload_document", "POST"): "hr.sensitive",
    ("hr.correct_mark", "POST"): "attendance.edit",
    ("hr.vacation_adjust", "POST"): "hr.edit",
    ("hr.trainings", "POST"): "hr.edit",

    # Configuración
    ("settings.roles", "POST"): "settings.edit",
    ("settings.update_role_permissions", "POST"): "settings.edit",
    ("settings.catalogs", "POST"): "settings.edit",
    ("settings.products", "POST"): "settings.edit",
    ("settings.values", "POST"): "settings.edit",
}

OPERATIONS_ENDPOINT_PERMISSIONS = {
    "operations.projects": "projects.view",
    "operations.project_detail": "projects.view",
    "operations.new_project": "projects.edit",
    "operations.update_project": "projects.edit",
    "operations.add_project_member": "projects.edit",
    "operations.change_request": "projects.edit",
    "operations.tasks": "tasks.view",
    "operations.task_kanban": "tasks.view",
    "operations.task_detail": "tasks.view",
    "operations.new_task": "tasks.edit",
    "operations.update_task": "tasks.edit",
    "operations.task_comment": "tasks.edit",
}

HR_VIEW_ENDPOINTS = {
    "hr.dashboard",
    "hr.collaborators",
    "hr.collaborator_detail",
    "hr.schedules",
    "hr.attendance",
    "hr.evaluations",
    "hr.trainings",
}


def _required_permission(endpoint, method):
    if endpoint in SELF_SERVICE_ENDPOINTS:
        return None

    explicit = METHOD_PERMISSIONS.get((endpoint, method))
    if explicit:
        return explicit

    explicit = ENDPOINT_PERMISSIONS.get(endpoint)
    if explicit:
        return explicit

    if endpoint in OPERATIONS_ENDPOINT_PERMISSIONS:
        return OPERATIONS_ENDPOINT_PERMISSIONS[endpoint]

    if endpoint in HR_VIEW_ENDPOINTS:
        return "hr.view"

    return DEFAULT_VIEW_PERMISSIONS.get(request.blueprint)


def _team_collaborator_ids():
    collaborator = current_user.collaborator
    if not collaborator:
        return set()
    return {collaborator.id, *(row.id for row in collaborator.subordinates)}


def _commercial_client_allowed(client):
    """Limita cartera comercial para asesores y supervisores."""
    role = current_user.role.name if current_user.role else ""
    collaborator = current_user.collaborator

    if role not in {"advisor", "supervisor"}:
        return True
    if not collaborator:
        return False

    if role == "advisor":
        return client.owner_id == collaborator.id

    return client.owner_id in _team_collaborator_ids()


def _sale_allowed(sale):
    role = current_user.role.name if current_user.role else ""
    collaborator = current_user.collaborator

    if role not in {"advisor", "supervisor"}:
        return True
    if not collaborator:
        return False

    if role == "advisor":
        return sale.advisor_id == collaborator.id or _commercial_client_allowed(sale.client)

    team_ids = _team_collaborator_ids()
    return sale.advisor_id in team_ids or _commercial_client_allowed(sale.client)


def _project_allowed(project):
    role = current_user.role.name if current_user.role else ""
    collaborator = current_user.collaborator

    if role in {"superadmin", "admin", "manager"}:
        return True
    if not collaborator:
        return role not in {"advisor", "supervisor", "production"}

    if role in {"advisor", "supervisor"}:
        return _commercial_client_allowed(project.client)

    if role == "production":
        return project.coordinator_id == collaborator.id or collaborator in project.members

    return True


def _task_allowed(task):
    role = current_user.role.name if current_user.role else ""
    collaborator = current_user.collaborator

    if role in {"superadmin", "admin", "manager"}:
        return True
    if not collaborator:
        return False

    if task.assignee_id == collaborator.id or collaborator in task.collaborators:
        return True

    if role == "supervisor":
        team_ids = _team_collaborator_ids()
        if task.assignee_id in team_ids:
            return True
        if any(member.id in team_ids for member in task.collaborators):
            return True
        return bool(task.project and _commercial_client_allowed(task.project.client))

    if role == "advisor":
        return bool(task.project and _commercial_client_allowed(task.project.client))

    if role == "production":
        return bool(task.project and _project_allowed(task.project))

    return False


def _enforce_record_scope(endpoint):
    """Evita saltarse filtros de propiedad escribiendo IDs directamente en la URL."""
    if not current_user.is_authenticated:
        return

    role = current_user.role.name if current_user.role else ""
    args = request.view_args or {}

    # CRM: asesor = su cartera; supervisor = su equipo.
    if request.blueprint == "crm":
        from app.models import Client, Quote

        client = None
        if "client_id" in args:
            client = Client.query.get(args["client_id"])
        elif "quote_id" in args:
            quote = Quote.query.get(args["quote_id"])
            client = quote.client if quote else None

        if client is not None and not _commercial_client_allowed(client):
            abort(403)

    # Clientes: las rutas por ID respetan la misma cartera comercial.
    if request.blueprint == "clients" and "client_id" in args:
        from app.models import Client

        client = Client.query.get(args["client_id"])
        if client is not None and not _commercial_client_allowed(client):
            abort(403)

    # Ventas: asesor y supervisor solo acceden a su cartera/equipo.
    if request.blueprint == "sales" and role in {"advisor", "supervisor"}:
        from app.models import Payment, Quote, Renewal, Sale

        sale = None
        if "sale_id" in args:
            sale = Sale.query.get(args["sale_id"])
        elif "payment_id" in args:
            payment = Payment.query.get(args["payment_id"])
            sale = payment.sale if payment else None
        elif "quote_id" in args:
            quote = Quote.query.get(args["quote_id"])
            if quote and not _commercial_client_allowed(quote.client):
                abort(403)
        elif "renewal_id" in args:
            renewal = Renewal.query.get(args["renewal_id"])
            if renewal and not _commercial_client_allowed(renewal.client):
                abort(403)

        if sale is not None and not _sale_allowed(sale):
            abort(403)

    # Operaciones: alcance de proyectos y tareas por función real.
    if request.blueprint == "operations":
        from app.models import Project, Task

        if "project_id" in args:
            project = Project.query.get(args["project_id"])
            if project is not None and not _project_allowed(project):
                abort(403)

        if "task_id" in args:
            task = Task.query.get(args["task_id"])
            if task is not None and not _task_allowed(task):
                abort(403)

    # Imprenta: asesor/supervisor solo acceden a órdenes de su cartera/equipo.
    if request.blueprint == "printing" and role in {"advisor", "supervisor"}:
        from app.models import PrintIncident, PrintItem, PrintOrder, Shipment

        order = None
        if "order_id" in args:
            order = PrintOrder.query.get(args["order_id"])
        elif "item_id" in args:
            item = PrintItem.query.get(args["item_id"])
            order = item.order if item else None
        elif "shipment_id" in args:
            shipment = Shipment.query.get(args["shipment_id"])
            order = shipment.order if shipment else None
        elif "incident_id" in args:
            incident = PrintIncident.query.get(args["incident_id"])
            order = incident.order if incident else None

        if order is not None and not _commercial_client_allowed(order.client):
            abort(403)

    # Soporte: asesor/supervisor solo acceden a tickets de su cartera/equipo.
    if request.blueprint == "support" and role in {"advisor", "supervisor"} and "ticket_id" in args:
        from app.models import SupportTicket

        ticket = SupportTicket.query.get(args["ticket_id"])
        if ticket is not None and not _commercial_client_allowed(ticket.client):
            abort(403)

    # RR. HH.: un supervisor solo puede abrir registros de su propio equipo.
    if request.blueprint == "hr" and role == "supervisor":
        from app.models import AttendanceMark, Collaborator, LeaveRequest

        team_ids = _team_collaborator_ids()
        target_id = None
        if "collaborator_id" in args:
            target_id = args["collaborator_id"]
        elif "leave_id" in args:
            row = LeaveRequest.query.get(args["leave_id"])
            target_id = row.collaborator_id if row else None
        elif "mark_id" in args:
            row = AttendanceMark.query.get(args["mark_id"])
            target_id = row.collaborator_id if row else None

        if target_id is not None and target_id not in team_ids:
            abort(403)


def init_access_control(app):
    @app.before_request
    def enforce_access_control():
        endpoint = request.endpoint
        if not endpoint or endpoint.startswith("static"):
            return None

        if not current_user.is_authenticated:
            return None

        permission = _required_permission(endpoint, request.method)
        if permission and not current_user.has_permission(permission):
            abort(403)

        _enforce_record_scope(endpoint)
        return None
