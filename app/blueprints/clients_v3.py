from datetime import date, datetime

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.decorators import permission_required
from app.extensions import db
from app.helpers import audit
from app.models import Client, ClientContract, Collaborator, Project, User
from app.access_control import _project_allowed, _task_allowed
from app.client_v2_models import ClientTeamAssignment
from app.client_v3_services import (
    ACTIVE_TEAM_STATUS,
    FINAL_TEAM_STATUS,
    SUSPENDED_TEAM_STATUS,
    build_client_alerts,
    build_client_timeline,
    client_coordination_allowed,
    my_clients_query,
    sync_legacy_summary_for_pair,
    sync_legacy_team_assignments,
)


bp = Blueprint("clients_v3", __name__, url_prefix="/clients")
TEAM_STATUSES = {ACTIVE_TEAM_STATUS, FINAL_TEAM_STATUS, SUSPENDED_TEAM_STATUS}


def _date_or_none(value):
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _role_name():
    return current_user.role.name if current_user.role else ""


def _get_client_or_403(client_id):
    client = db.get_or_404(Client, client_id)
    if client.record_type != "cliente":
        abort(404)
    if not client_coordination_allowed(client):
        abort(403)
    return client


def _assignable_collaborators():
    query = (
        Collaborator.query
        .join(User, Collaborator.user_id == User.id)
        .filter(Collaborator.status == "activo", User.active.is_(True))
    )
    role = _role_name()
    collaborator = current_user.collaborator

    if role == "supervisor" and collaborator:
        ids = [collaborator.id] + [row.id for row in collaborator.subordinates]
        query = query.filter(Collaborator.id.in_(ids))

    return query.order_by(User.name).all()


def _full_detail_ids(clients):
    role = _role_name()
    collaborator = current_user.collaborator
    if role == "production":
        return set()
    if role == "advisor" and collaborator:
        return {client.id for client in clients if client.owner_id == collaborator.id}
    if role == "supervisor" and collaborator:
        allowed = {collaborator.id, *(row.id for row in collaborator.subordinates)}
        return {client.id for client in clients if client.owner_id in allowed}
    return {client.id for client in clients}


@bp.route("/mine")
@login_required
@permission_required("clients.view")
def mine():
    sync_legacy_team_assignments()
    db.session.flush()

    clients = my_clients_query().order_by(Client.business_name).all()
    collaborator = current_user.collaborator
    role = _role_name()

    assignment_map = {}
    alert_count_map = {}
    for client in clients:
        query = ClientTeamAssignment.query.filter_by(
            client_id=client.id,
            status=ACTIVE_TEAM_STATUS,
        )
        if collaborator and role not in {"superadmin", "admin", "manager"}:
            if role == "supervisor":
                ids = [collaborator.id] + [row.id for row in collaborator.subordinates]
                query = query.filter(ClientTeamAssignment.collaborator_id.in_(ids))
            else:
                query = query.filter(ClientTeamAssignment.collaborator_id == collaborator.id)
        assignment_map[client.id] = query.order_by(ClientTeamAssignment.primary.desc()).all()

        visible_tasks = []
        if current_user.has_permission("tasks.view"):
            visible_tasks = [row for row in client.tasks if _task_allowed(row)]
        visible_tickets = client.tickets if current_user.has_permission("support.view") else []
        can_financial = current_user.has_permission("sales.view") or current_user.has_permission("finance.view")
        alert_count_map[client.id] = len(
            build_client_alerts(
                client,
                can_financial=can_financial,
                visible_tasks=visible_tasks,
                visible_tickets=visible_tickets,
            )
        )

    full_detail_ids = _full_detail_ids(clients)
    db.session.commit()
    return render_template(
        "clients/mine.html",
        clients=clients,
        assignment_map=assignment_map,
        alert_count_map=alert_count_map,
        full_detail_ids=full_detail_ids,
    )


@bp.route("/<int:client_id>/coordination")
@login_required
@permission_required("clients.view")
def coordination(client_id):
    sync_legacy_team_assignments()
    db.session.flush()

    client = _get_client_or_403(client_id)
    active_assignments = (
        ClientTeamAssignment.query
        .filter_by(client_id=client.id, status=ACTIVE_TEAM_STATUS)
        .order_by(ClientTeamAssignment.primary.desc(), ClientTeamAssignment.starts_on, ClientTeamAssignment.id)
        .all()
    )
    assignment_history = (
        ClientTeamAssignment.query
        .filter(
            ClientTeamAssignment.client_id == client.id,
            ClientTeamAssignment.status != ACTIVE_TEAM_STATUS,
        )
        .order_by(ClientTeamAssignment.starts_on.desc(), ClientTeamAssignment.id.desc())
        .all()
    )

    visible_tasks = []
    if current_user.has_permission("tasks.view"):
        visible_tasks = [row for row in client.tasks if _task_allowed(row)]

    visible_projects = []
    if current_user.has_permission("projects.view"):
        visible_projects = [row for row in client.projects if _project_allowed(row)]

    visible_tickets = client.tickets if current_user.has_permission("support.view") else []
    can_financial = current_user.has_permission("sales.view") or current_user.has_permission("finance.view")

    alerts = build_client_alerts(
        client,
        can_financial=can_financial,
        visible_tasks=visible_tasks,
        visible_tickets=visible_tickets,
    )
    timeline = build_client_timeline(client, can_financial=can_financial)

    collaborators = _assignable_collaborators() if current_user.has_permission("clients.assign") else []
    contracts = (
        ClientContract.query
        .filter_by(client_id=client.id)
        .order_by(ClientContract.starts_on.desc())
        .all()
    )
    projects = sorted(visible_projects, key=lambda row: row.created_at or datetime.min, reverse=True)

    role = _role_name()
    collaborator = current_user.collaborator
    if role == "production":
        can_open_full_detail = False
    elif role == "advisor" and collaborator:
        can_open_full_detail = client.owner_id == collaborator.id
    elif role == "supervisor" and collaborator:
        allowed_owner_ids = {collaborator.id, *(row.id for row in collaborator.subordinates)}
        can_open_full_detail = client.owner_id in allowed_owner_ids
    else:
        can_open_full_detail = True

    db.session.commit()
    return render_template(
        "clients/coordination.html",
        client=client,
        can_open_full_detail=can_open_full_detail,
        active_assignments=active_assignments,
        assignment_history=assignment_history,
        collaborators=collaborators,
        contracts=contracts,
        projects=projects,
        alerts=alerts,
        timeline=timeline[:120],
        can_financial=can_financial,
        can_assign=current_user.has_permission("clients.assign"),
    )


@bp.route("/<int:client_id>/team", methods=["POST"])
@login_required
@permission_required("clients.assign")
def add_team_assignment(client_id):
    client = _get_client_or_403(client_id)

    collaborator_id = request.form.get("collaborator_id", type=int)
    collaborator = db.session.get(Collaborator, collaborator_id) if collaborator_id else None
    allowed_ids = {row.id for row in _assignable_collaborators()}
    if not collaborator or collaborator.status != "activo" or collaborator.id not in allowed_ids:
        flash("El colaborador seleccionado no está disponible para tu alcance.", "danger")
        return redirect(url_for("clients_v3.coordination", client_id=client.id) + "#equipo")

    role_in_client = (request.form.get("role_in_client") or "").strip()
    if not role_in_client:
        flash("Indica el rol que tendrá el colaborador en este cliente.", "danger")
        return redirect(url_for("clients_v3.coordination", client_id=client.id) + "#equipo")

    contract_id = request.form.get("contract_id", type=int)
    contract = db.session.get(ClientContract, contract_id) if contract_id else None
    if contract and contract.client_id != client.id:
        flash("El servicio/plan seleccionado no pertenece a este cliente.", "danger")
        return redirect(url_for("clients_v3.coordination", client_id=client.id) + "#equipo")

    project_id = request.form.get("project_id", type=int)
    project = db.session.get(Project, project_id) if project_id else None
    if project and project.client_id != client.id:
        flash("El proyecto seleccionado no pertenece a este cliente.", "danger")
        return redirect(url_for("clients_v3.coordination", client_id=client.id) + "#equipo")

    starts_on = _date_or_none(request.form.get("starts_on")) or date.today()
    service_label = (request.form.get("service_label") or "").strip()
    if not service_label and contract and contract.product:
        service_label = contract.product.name

    duplicate_query = ClientTeamAssignment.query.filter_by(
        client_id=client.id,
        collaborator_id=collaborator.id,
        status=ACTIVE_TEAM_STATUS,
        role_in_client=role_in_client,
    )
    if contract_id:
        duplicate_query = duplicate_query.filter(ClientTeamAssignment.contract_id == contract_id)
    elif project_id:
        duplicate_query = duplicate_query.filter(ClientTeamAssignment.project_id == project_id)
    elif service_label:
        duplicate_query = duplicate_query.filter(ClientTeamAssignment.service_label == service_label)

    if duplicate_query.first():
        flash("Ya existe una asignación activa equivalente para este colaborador.", "warning")
        return redirect(url_for("clients_v3.coordination", client_id=client.id) + "#equipo")

    assignment = ClientTeamAssignment(
        client_id=client.id,
        collaborator_id=collaborator.id,
        contract_id=contract.id if contract else None,
        project_id=project.id if project else None,
        role_in_client=role_in_client[:120],
        service_label=service_label[:180] if service_label else None,
        starts_on=starts_on,
        status=ACTIVE_TEAM_STATUS,
        primary=bool(request.form.get("primary")),
        notes=(request.form.get("notes") or "").strip() or None,
        assigned_by_id=current_user.id,
    )
    db.session.add(assignment)
    db.session.flush()
    sync_legacy_summary_for_pair(client.id, collaborator.id)

    audit(
        "asignar_equipo_cliente_v3",
        "ClientTeamAssignment",
        assignment.id,
        after={
            "client_id": client.id,
            "collaborator_id": collaborator.id,
            "role": assignment.role_in_client,
            "service": assignment.service_label,
        },
    )
    db.session.commit()
    flash("Colaborador asignado al equipo operativo.", "success")
    return redirect(url_for("clients_v3.coordination", client_id=client.id) + "#equipo")


@bp.route("/<int:client_id>/team/<int:assignment_id>/<action>", methods=["POST"])
@login_required
@permission_required("clients.assign")
def team_assignment_action(client_id, assignment_id, action):
    client = _get_client_or_403(client_id)
    assignment = db.get_or_404(ClientTeamAssignment, assignment_id)
    if assignment.client_id != client.id:
        abort(404)

    if action not in {"suspend", "resume", "end"}:
        abort(404)

    before = {
        "status": assignment.status,
        "ends_on": str(assignment.ends_on) if assignment.ends_on else None,
    }

    if action == "suspend":
        if assignment.status != ACTIVE_TEAM_STATUS:
            flash("Solo una asignación activa puede suspenderse.", "warning")
            return redirect(url_for("clients_v3.coordination", client_id=client.id) + "#equipo")
        assignment.status = SUSPENDED_TEAM_STATUS

    elif action == "resume":
        if assignment.status != SUSPENDED_TEAM_STATUS:
            flash("Solo una asignación suspendida puede reactivarse.", "warning")
            return redirect(url_for("clients_v3.coordination", client_id=client.id) + "#equipo")
        assignment.status = ACTIVE_TEAM_STATUS
        assignment.ends_on = None
        assignment.ended_by_id = None
        assignment.end_reason = None

    else:
        if assignment.status == FINAL_TEAM_STATUS:
            flash("Esta asignación ya está finalizada.", "info")
            return redirect(url_for("clients_v3.coordination", client_id=client.id) + "#equipo")
        end_date = _date_or_none(request.form.get("ends_on")) or date.today()
        if end_date < assignment.starts_on:
            flash("La fecha final no puede ser anterior al inicio de la asignación.", "danger")
            return redirect(url_for("clients_v3.coordination", client_id=client.id) + "#equipo")
        assignment.status = FINAL_TEAM_STATUS
        assignment.ends_on = end_date
        assignment.ended_by_id = current_user.id
        assignment.end_reason = (request.form.get("end_reason") or "").strip() or "Asignación finalizada."

    sync_legacy_summary_for_pair(client.id, assignment.collaborator_id)

    audit(
        f"equipo_cliente_v3_{action}",
        "ClientTeamAssignment",
        assignment.id,
        before=before,
        after={
            "status": assignment.status,
            "ends_on": str(assignment.ends_on) if assignment.ends_on else None,
            "reason": assignment.end_reason,
        },
    )
    db.session.commit()
    flash("Estado de la asignación actualizado.", "success")
    return redirect(url_for("clients_v3.coordination", client_id=client.id) + "#equipo")
