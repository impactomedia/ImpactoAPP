from datetime import date, datetime

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import or_

from app.extensions import db
from app.helpers import audit, next_code
from app.models import ChangeRequest, Client, Collaborator, Project, Task, TaskComment

bp = Blueprint("operations", __name__, url_prefix="/operations")

PROJECT_STATES = [
    "pendiente_onboarding",
    "recopilando_informacion",
    "listo_iniciar",
    "en_produccion",
    "esperando_cliente",
    "revision_interna",
    "aprobacion_cliente",
    "correcciones",
    "entregado",
    "mantenimiento",
    "completado",
    "cancelado",
]
TASK_STATES = ["pendiente", "en_proceso", "bloqueada", "en_revision", "completada", "cancelada"]
TASK_PRIORITIES = {"baja", "media", "alta", "urgente"}


def _role_name():
    return current_user.role.name if current_user.role else ""


def _team_ids():
    collaborator = current_user.collaborator
    if not collaborator:
        return []
    return [collaborator.id] + [row.id for row in collaborator.subordinates]


def _visible_projects_query():
    query = Project.query
    role = _role_name()
    collaborator = current_user.collaborator

    if not collaborator:
        return query

    if role == "advisor":
        query = query.filter(Project.client.has(Client.owner_id == collaborator.id))
    elif role == "supervisor":
        query = query.filter(Project.client.has(Client.owner_id.in_(_team_ids())))
    elif role == "production":
        query = query.filter(
            or_(
                Project.coordinator_id == collaborator.id,
                Project.members.any(id=collaborator.id),
            )
        )
    return query


def _visible_tasks_query():
    query = Task.query
    role = _role_name()
    collaborator = current_user.collaborator

    if role in {"superadmin", "admin", "manager"}:
        return query
    if not collaborator:
        return query.filter(Task.id == -1)

    own = or_(
        Task.assignee_id == collaborator.id,
        Task.collaborators.any(id=collaborator.id),
    )

    if role == "supervisor":
        team_ids = _team_ids()
        return query.filter(
            or_(
                Task.assignee_id.in_(team_ids),
                Task.collaborators.any(Collaborator.id.in_(team_ids)),
                Task.project.has(Project.client.has(Client.owner_id.in_(team_ids))),
            )
        )

    if role == "advisor":
        return query.filter(
            or_(
                own,
                Task.project.has(Project.client.has(Client.owner_id == collaborator.id)),
            )
        )

    if role == "production":
        return query.filter(
            or_(
                own,
                Task.project.has(
                    or_(
                        Project.coordinator_id == collaborator.id,
                        Project.members.any(id=collaborator.id),
                    )
                ),
            )
        )

    return query.filter(own)


def _visible_clients_for_task():
    if not current_user.has_permission("clients.view"):
        return []

    query = Client.query.filter(Client.record_type == "cliente", Client.client_status != "archivado")
    role = _role_name()
    collaborator = current_user.collaborator
    if role == "advisor" and collaborator:
        query = query.filter(Client.owner_id == collaborator.id)
    elif role == "supervisor" and collaborator:
        query = query.filter(Client.owner_id.in_(_team_ids()))
    elif role == "production" and collaborator:
        visible_client_ids = [
            row[0]
            for row in _visible_projects_query().with_entities(Project.client_id).distinct().all()
            if row[0] is not None
        ]
        query = query.filter(Client.id.in_(visible_client_ids or [-1]))
    return query.order_by(Client.business_name).all()


def _assignable_collaborators():
    role = _role_name()
    collaborator = current_user.collaborator
    query = Collaborator.query.filter_by(status="activo")

    if role == "supervisor" and collaborator:
        query = query.filter(Collaborator.id.in_(_team_ids()))
    elif role in {"advisor", "production", "hr"} and collaborator:
        query = query.filter(Collaborator.id == collaborator.id)
    elif role not in {"superadmin", "admin", "manager"} and collaborator:
        query = query.filter(Collaborator.id == collaborator.id)

    return query.order_by(Collaborator.id).all()


def _can_reassign_tasks():
    return _role_name() in {"superadmin", "admin", "manager", "supervisor"}


def _parse_date(value):
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _parse_datetime(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


@bp.route("/projects")
@login_required
def projects():
    rows = (
        _visible_projects_query()
        .order_by(Project.due_on.is_(None), Project.due_on.asc(), Project.id.desc())
        .all()
    )
    return render_template("operations/projects.html", projects=rows)


@bp.route("/projects/new", methods=["GET", "POST"])
@login_required
def new_project():
    client_query = Client.query.filter(Client.record_type == "cliente", Client.client_status != "archivado")
    if _role_name() == "supervisor" and current_user.collaborator:
        client_query = client_query.filter(Client.owner_id.in_(_team_ids()))
    clients = client_query.order_by(Client.business_name).all()

    collaborators = _assignable_collaborators()

    visible_client_ids = {client.id for client in clients}
    collaborator_ids = {row.id for row in collaborators}

    if request.method == "POST":
        client_id = request.form.get("client_id", type=int)
        name = (request.form.get("name") or "").strip()
        status = request.form.get("status", "pendiente_onboarding")
        coordinator_id = request.form.get("coordinator_id", type=int)
        if _role_name() == "production" and current_user.collaborator:
            # Producción solo puede crear proyectos quedando asignado como coordinador,
            # para evitar crear un proyecto que luego quede fuera de su propio alcance.
            coordinator_id = current_user.collaborator.id

        if not client_id or client_id not in visible_client_ids:
            abort(403)
        if not name:
            flash("El nombre del proyecto es obligatorio.", "danger")
            return render_template("operations/project_form.html", clients=clients, collaborators=collaborators, states=PROJECT_STATES)
        if status not in PROJECT_STATES:
            status = "pendiente_onboarding"
        if coordinator_id and coordinator_id not in collaborator_ids:
            flash("Selecciona un coordinador válido.", "danger")
            return render_template("operations/project_form.html", clients=clients, collaborators=collaborators, states=PROJECT_STATES)

        starts_on = _parse_date(request.form.get("starts_on")) or date.today()
        due_on = _parse_date(request.form.get("due_on"))
        if due_on and due_on < starts_on:
            flash("La fecha de entrega no puede ser anterior al inicio.", "danger")
            return render_template("operations/project_form.html", clients=clients, collaborators=collaborators, states=PROJECT_STATES)

        progress = request.form.get("progress", type=int)
        progress = max(0, min(100, progress if progress is not None else 0))

        project = Project(
            project_no=next_code("PRJ", Project),
            client_id=client_id,
            name=name,
            coordinator_id=coordinator_id,
            department=request.form.get("department"),
            status=status,
            progress=progress,
            starts_on=starts_on,
            due_on=due_on,
            notes=request.form.get("notes"),
        )
        db.session.add(project)
        db.session.flush()
        audit("crear_proyecto", "Project", project.id, after={"project_no": project.project_no})
        db.session.commit()
        flash("Proyecto creado.", "success")
        return redirect(url_for("operations.project_detail", project_id=project.id))

    return render_template("operations/project_form.html", clients=clients, collaborators=collaborators, states=PROJECT_STATES)


@bp.route("/projects/<int:project_id>")
@login_required
def project_detail(project_id):
    project = db.get_or_404(Project, project_id)
    can_manage_members = _role_name() in {"superadmin", "admin", "manager", "supervisor"}
    collaborators = _assignable_collaborators() if can_manage_members else []
    return render_template(
        "operations/project_detail.html",
        project=project,
        collaborators=collaborators,
        project_states=PROJECT_STATES,
        task_states=TASK_STATES,
        can_manage_members=can_manage_members,
    )


@bp.route("/projects/<int:project_id>/update", methods=["POST"])
@login_required
def update_project(project_id):
    project = db.get_or_404(Project, project_id)
    new_status = request.form.get("status", project.status)
    if new_status not in PROJECT_STATES:
        flash("Estado de proyecto no válido.", "danger")
        return redirect(url_for("operations.project_detail", project_id=project.id))

    progress = request.form.get("progress", type=int)
    if progress is None:
        progress = project.progress
    progress = max(0, min(100, progress))

    before = {"status": project.status, "progress": project.progress}
    project.status = new_status
    project.progress = progress
    if project.status == "completado":
        project.progress = 100
        if not project.completed_on:
            project.completed_on = date.today()
    elif project.completed_on:
        project.completed_on = None

    audit(
        "actualizar_proyecto",
        "Project",
        project.id,
        before=before,
        after={"status": project.status, "progress": project.progress},
    )
    db.session.commit()
    flash("Proyecto actualizado.", "success")
    return redirect(url_for("operations.project_detail", project_id=project.id))


@bp.route("/projects/<int:project_id>/members", methods=["POST"])
@login_required
def add_project_member(project_id):
    if _role_name() not in {"superadmin", "admin", "manager", "supervisor"}:
        abort(403)

    project = db.get_or_404(Project, project_id)
    collaborator_id = request.form.get("collaborator_id", type=int)
    if not collaborator_id:
        flash("Selecciona un colaborador.", "warning")
        return redirect(url_for("operations.project_detail", project_id=project.id))

    collaborator = db.get_or_404(Collaborator, collaborator_id)
    if collaborator.status != "activo":
        flash("Solo puedes asignar colaboradores activos.", "danger")
        return redirect(url_for("operations.project_detail", project_id=project.id))

    if collaborator not in project.members:
        project.members.append(collaborator)
        audit("asignar_miembro", "Project", project.id, after={"collaborator_id": collaborator.id})
        db.session.commit()
        flash("Miembro asignado al proyecto.", "success")
    else:
        flash("El colaborador ya pertenece al proyecto.", "info")
    return redirect(url_for("operations.project_detail", project_id=project.id))


@bp.route("/tasks")
@login_required
def tasks():
    rows = (
        _visible_tasks_query()
        .order_by(Task.due_at.is_(None), Task.due_at.asc(), Task.id.desc())
        .all()
    )
    return render_template("operations/tasks.html", tasks=rows, states=TASK_STATES)


@bp.route("/tasks/kanban")
@login_required
def task_kanban():
    rows = _visible_tasks_query().order_by(Task.updated_at.desc()).all()
    columns = {state: [] for state in TASK_STATES}
    for task in rows:
        columns.setdefault(task.status, []).append(task)
    return render_template("operations/task_kanban.html", columns=columns, states=TASK_STATES)


@bp.route("/tasks/new", methods=["GET", "POST"])
@login_required
def new_task():
    projects = _visible_projects_query().order_by(Project.id.desc()).all() if current_user.has_permission("projects.view") else []
    clients = _visible_clients_for_task()
    collaborators = _assignable_collaborators()

    project_ids = {row.id for row in projects}
    client_ids = {row.id for row in clients}
    collaborator_ids = {row.id for row in collaborators}

    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        if not title:
            flash("El título de la tarea es obligatorio.", "danger")
            return render_template("operations/task_form.html", projects=projects, clients=clients, collaborators=collaborators)

        project_id = request.form.get("project_id", type=int)
        client_id = request.form.get("client_id", type=int)
        assignee_id = request.form.get("assignee_id", type=int)

        if project_id and project_id not in project_ids:
            abort(403)
        if client_id and client_id not in client_ids:
            abort(403)
        if assignee_id and assignee_id not in collaborator_ids:
            abort(403)

        if project_id:
            project = db.session.get(Project, project_id)
            if project and client_id and project.client_id != client_id:
                flash("El proyecto seleccionado no pertenece al cliente indicado.", "danger")
                return render_template("operations/task_form.html", projects=projects, clients=clients, collaborators=collaborators)
            if project and not client_id:
                client_id = project.client_id

        priority = request.form.get("priority", "media")
        if priority not in TASK_PRIORITIES:
            priority = "media"

        starts_at = _parse_datetime(request.form.get("starts_at"))
        due_at = _parse_datetime(request.form.get("due_at"))
        if starts_at and due_at and due_at < starts_at:
            flash("El vencimiento no puede ser anterior al inicio.", "danger")
            return render_template("operations/task_form.html", projects=projects, clients=clients, collaborators=collaborators)

        task = Task(
            title=title,
            description=request.form.get("description"),
            task_type=(request.form.get("task_type") or "interna").strip(),
            client_id=client_id,
            project_id=project_id,
            assignee_id=assignee_id,
            priority=priority,
            status="pendiente",
            starts_at=starts_at,
            due_at=due_at,
            checklist=request.form.get("checklist"),
            estimated_minutes=request.form.get("estimated_minutes", type=int),
            recurring=bool(request.form.get("recurring")),
        )
        db.session.add(task)
        db.session.flush()
        audit("crear_tarea", "Task", task.id, after={"title": task.title})
        db.session.commit()
        flash("Tarea creada.", "success")
        return redirect(url_for("operations.task_detail", task_id=task.id))

    return render_template("operations/task_form.html", projects=projects, clients=clients, collaborators=collaborators)


@bp.route("/tasks/<int:task_id>")
@login_required
def task_detail(task_id):
    task = db.get_or_404(Task, task_id)
    can_reassign = _can_reassign_tasks()
    collaborators = _assignable_collaborators() if can_reassign else []
    return render_template(
        "operations/task_detail.html",
        task=task,
        collaborators=collaborators,
        states=TASK_STATES,
        can_reassign=can_reassign,
    )


@bp.route("/tasks/<int:task_id>/update", methods=["POST"])
@login_required
def update_task(task_id):
    task = db.get_or_404(Task, task_id)
    new_status = request.form.get("status", task.status)
    if new_status not in TASK_STATES:
        flash("Estado de tarea no válido.", "danger")
        return redirect(url_for("operations.task_detail", task_id=task.id))

    if new_status == "completada" and task.checklist:
        required = []
        for line in task.checklist.splitlines():
            clean = line.strip()
            if clean and not clean.lower().startswith("[x]"):
                required.append(clean)
        if required:
            flash("Completa los ítems obligatorios del checklist antes de cerrar la tarea.", "warning")
            return redirect(url_for("operations.task_detail", task_id=task.id))

    before = {"status": task.status, "assignee_id": task.assignee_id}
    task.status = new_status

    if _can_reassign_tasks():
        requested_assignee = request.form.get("assignee_id", type=int)
        allowed_ids = {row.id for row in _assignable_collaborators()}
        if requested_assignee and requested_assignee not in allowed_ids:
            abort(403)
        task.assignee_id = requested_assignee or task.assignee_id

    if task.status == "completada" and not task.completed_at:
        task.completed_at = datetime.utcnow()
    elif task.status != "completada":
        task.completed_at = None

    audit(
        "actualizar_tarea",
        "Task",
        task.id,
        before=before,
        after={"status": task.status, "assignee_id": task.assignee_id},
    )
    db.session.commit()
    flash("Tarea actualizada.", "success")
    return redirect(url_for("operations.task_detail", task_id=task.id))


@bp.route("/tasks/<int:task_id>/comment", methods=["POST"])
@login_required
def task_comment(task_id):
    task = db.get_or_404(Task, task_id)
    body = (request.form.get("body") or "").strip()
    if not body:
        flash("Escribe un comentario antes de guardar.", "warning")
        return redirect(url_for("operations.task_detail", task_id=task.id))

    db.session.add(TaskComment(task_id=task.id, user_id=current_user.id, body=body))
    audit("comentario_tarea", "Task", task.id)
    db.session.commit()
    flash("Comentario agregado.", "success")
    return redirect(url_for("operations.task_detail", task_id=task.id))


@bp.route("/projects/<int:project_id>/change-request", methods=["POST"])
@login_required
def change_request(project_id):
    project = db.get_or_404(Project, project_id)
    title = (request.form.get("title") or "Cambio").strip()
    priority = request.form.get("priority", "media")
    scope_class = request.form.get("scope_class", "incluida")

    if priority not in TASK_PRIORITIES:
        priority = "media"
    if scope_class not in {"incluida", "cortesia", "adicional"}:
        scope_class = "incluida"

    request_row = ChangeRequest(
        project_id=project.id,
        title=title or "Cambio",
        description=request.form.get("description"),
        priority=priority,
        responsible_id=request.form.get("responsible_id", type=int),
        scope_class=scope_class,
        status="recibida",
    )
    db.session.add(request_row)
    audit("solicitud_cambio", "Project", project.id, after={"title": request_row.title})
    db.session.commit()
    flash("Solicitud de cambio registrada.", "success")
    return redirect(url_for("operations.project_detail", project_id=project.id))
