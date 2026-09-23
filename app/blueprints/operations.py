from datetime import datetime, date
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user

from app.extensions import db
from app.helpers import audit, next_code
from app.models import Project, Task, TaskComment, Collaborator, Client, ChangeRequest

bp = Blueprint("operations", __name__, url_prefix="/operations")

PROJECT_STATES = ["pendiente_onboarding", "recopilando_informacion", "listo_iniciar", "en_produccion", "esperando_cliente", "revision_interna", "aprobacion_cliente", "correcciones", "entregado", "mantenimiento", "completado", "cancelado"]
TASK_STATES = ["pendiente", "en_proceso", "bloqueada", "en_revision", "completada", "cancelada"]


@bp.route("/projects")
@login_required
def projects():
    q = Project.query
    if current_user.role and current_user.role.name == "production" and current_user.collaborator:
        q = q.filter((Project.coordinator_id == current_user.collaborator.id) | Project.members.any(id=current_user.collaborator.id))
    projects = q.order_by(Project.due_on.is_(None), Project.due_on.asc(), Project.id.desc()).all()
    return render_template("operations/projects.html", projects=projects)


@bp.route("/projects/new", methods=["GET", "POST"])
@login_required
def new_project():
    clients = Client.query.filter_by(record_type="cliente").order_by(Client.business_name).all()
    collaborators = Collaborator.query.filter_by(status="activo").order_by(Collaborator.id).all()
    if request.method == "POST":
        p = Project(
            project_no=next_code("PRJ", Project),
            client_id=request.form.get("client_id", type=int),
            name=request.form.get("name", "").strip(),
            coordinator_id=request.form.get("coordinator_id", type=int),
            department=request.form.get("department"),
            status=request.form.get("status", "pendiente_onboarding"),
            progress=request.form.get("progress", type=int) or 0,
            starts_on=date.fromisoformat(request.form.get("starts_on")) if request.form.get("starts_on") else date.today(),
            due_on=date.fromisoformat(request.form.get("due_on")) if request.form.get("due_on") else None,
            notes=request.form.get("notes"),
        )
        db.session.add(p)
        db.session.flush()
        audit("crear_proyecto", "Project", p.id, after={"project_no": p.project_no})
        db.session.commit()
        flash("Proyecto creado.", "success")
        return redirect(url_for("operations.project_detail", project_id=p.id))
    return render_template("operations/project_form.html", clients=clients, collaborators=collaborators, states=PROJECT_STATES)


@bp.route("/projects/<int:project_id>")
@login_required
def project_detail(project_id):
    project = db.get_or_404(Project, project_id)
    collaborators = Collaborator.query.filter_by(status="activo").all()
    return render_template("operations/project_detail.html", project=project, collaborators=collaborators, project_states=PROJECT_STATES, task_states=TASK_STATES)


@bp.route("/projects/<int:project_id>/update", methods=["POST"])
@login_required
def update_project(project_id):
    p = db.get_or_404(Project, project_id)
    before = {"status": p.status, "progress": p.progress}
    p.status = request.form.get("status", p.status)
    p.progress = max(0, min(100, request.form.get("progress", type=int) or p.progress))
    if p.status == "completado" and not p.completed_on:
        p.completed_on = date.today()
    audit("actualizar_proyecto", "Project", p.id, before=before, after={"status": p.status, "progress": p.progress})
    db.session.commit()
    flash("Proyecto actualizado.", "success")
    return redirect(url_for("operations.project_detail", project_id=p.id))


@bp.route("/projects/<int:project_id>/members", methods=["POST"])
@login_required
def add_project_member(project_id):
    p = db.get_or_404(Project, project_id)
    c = db.get_or_404(Collaborator, request.form.get("collaborator_id", type=int))
    if c not in p.members:
        p.members.append(c)
        audit("asignar_miembro", "Project", p.id, after={"collaborator_id": c.id})
        db.session.commit()
    return redirect(url_for("operations.project_detail", project_id=p.id))


@bp.route("/tasks")
@login_required
def tasks():
    q = Task.query
    if current_user.role and current_user.role.name not in {"superadmin", "admin", "manager", "supervisor"} and current_user.collaborator:
        q = q.filter((Task.assignee_id == current_user.collaborator.id) | Task.collaborators.any(id=current_user.collaborator.id))
    tasks = q.order_by(Task.due_at.is_(None), Task.due_at.asc(), Task.id.desc()).all()
    return render_template("operations/tasks.html", tasks=tasks, states=TASK_STATES)


@bp.route("/tasks/kanban")
@login_required
def task_kanban():
    tasks = Task.query.order_by(Task.updated_at.desc()).all()
    columns = {s: [] for s in TASK_STATES}
    for t in tasks:
        columns.setdefault(t.status, []).append(t)
    return render_template("operations/task_kanban.html", columns=columns, states=TASK_STATES)


@bp.route("/tasks/new", methods=["GET", "POST"])
@login_required
def new_task():
    projects = Project.query.order_by(Project.id.desc()).all()
    clients = Client.query.order_by(Client.business_name).all()
    collaborators = Collaborator.query.filter_by(status="activo").all()
    if request.method == "POST":
        t = Task(
            title=request.form.get("title", "").strip(),
            description=request.form.get("description"),
            task_type=request.form.get("task_type", "interna"),
            client_id=request.form.get("client_id", type=int),
            project_id=request.form.get("project_id", type=int),
            assignee_id=request.form.get("assignee_id", type=int),
            priority=request.form.get("priority", "media"),
            status="pendiente",
            starts_at=datetime.fromisoformat(request.form.get("starts_at")) if request.form.get("starts_at") else None,
            due_at=datetime.fromisoformat(request.form.get("due_at")) if request.form.get("due_at") else None,
            checklist=request.form.get("checklist"),
            estimated_minutes=request.form.get("estimated_minutes", type=int),
            recurring=bool(request.form.get("recurring")),
        )
        db.session.add(t)
        db.session.flush()
        audit("crear_tarea", "Task", t.id, after={"title": t.title})
        db.session.commit()
        flash("Tarea creada.", "success")
        return redirect(url_for("operations.task_detail", task_id=t.id))
    return render_template("operations/task_form.html", projects=projects, clients=clients, collaborators=collaborators)


@bp.route("/tasks/<int:task_id>")
@login_required
def task_detail(task_id):
    task = db.get_or_404(Task, task_id)
    collaborators = Collaborator.query.filter_by(status="activo").all()
    return render_template("operations/task_detail.html", task=task, collaborators=collaborators, states=TASK_STATES)


@bp.route("/tasks/<int:task_id>/update", methods=["POST"])
@login_required
def update_task(task_id):
    t = db.get_or_404(Task, task_id)
    before = {"status": t.status, "assignee_id": t.assignee_id}
    new_status = request.form.get("status", t.status)
    if new_status == "completada" and t.checklist:
        required = [x.strip() for x in t.checklist.splitlines() if x.strip() and not x.strip().startswith("[x]")]
        if required:
            flash("Completa los ítems obligatorios del checklist antes de cerrar la tarea.", "warning")
            return redirect(url_for("operations.task_detail", task_id=t.id))
    t.status = new_status
    t.assignee_id = request.form.get("assignee_id", type=int) or t.assignee_id
    if t.status == "completada" and not t.completed_at:
        t.completed_at = datetime.utcnow()
    audit("actualizar_tarea", "Task", t.id, before=before, after={"status": t.status, "assignee_id": t.assignee_id})
    db.session.commit()
    flash("Tarea actualizada.", "success")
    return redirect(url_for("operations.task_detail", task_id=t.id))


@bp.route("/tasks/<int:task_id>/comment", methods=["POST"])
@login_required
def task_comment(task_id):
    t = db.get_or_404(Task, task_id)
    body = request.form.get("body", "").strip()
    if body:
        db.session.add(TaskComment(task_id=t.id, user_id=current_user.id, body=body))
        db.session.commit()
    return redirect(url_for("operations.task_detail", task_id=t.id))


@bp.route("/projects/<int:project_id>/change-request", methods=["POST"])
@login_required
def change_request(project_id):
    p = db.get_or_404(Project, project_id)
    cr = ChangeRequest(project_id=p.id, title=request.form.get("title", "Cambio"), description=request.form.get("description"), priority=request.form.get("priority", "media"), responsible_id=request.form.get("responsible_id", type=int), scope_class=request.form.get("scope_class", "incluida"), status="recibida")
    db.session.add(cr)
    audit("solicitud_cambio", "Project", p.id, after={"title": cr.title})
    db.session.commit()
    flash("Solicitud de cambio registrada.", "success")
    return redirect(url_for("operations.project_detail", project_id=p.id))
