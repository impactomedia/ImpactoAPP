"""Presentación de tareas por proyecto sin alterar el modelo ni los permisos."""

from datetime import datetime


CLOSED_TASK_STATES = {"completada", "cancelada"}
PRIORITY_RANK = {"urgente": 0, "alta": 1, "media": 2, "baja": 3}


def organize_tasks(tasks, *, now=None):
    """Agrupa únicamente las tareas que ya fueron autorizadas por la consulta de acceso.

    Las tareas sin project_id conservan visibilidad mediante el cliente y,
    en último término, un grupo de tareas internas sin proyecto.
    """
    now = now or datetime.utcnow()
    groups = {}

    for task in tasks:
        project = task.project
        client = task.client or (project.client if project else None)

        if project:
            key = f"project:{project.id}"
            kind = "Proyecto"
            title = project.name
            client_name = client.business_name if client else ""
            rank = 0
        elif client:
            key = f"client:{client.id}"
            kind = "Cliente · sin proyecto"
            title = client.business_name
            client_name = ""
            rank = 1
        else:
            key = "general"
            kind = "Tareas internas"
            title = "Sin proyecto ni cliente"
            client_name = ""
            rank = 2

        if key not in groups:
            groups[key] = {
                "key": key,
                "kind": kind,
                "title": title,
                "client_name": client_name,
                "rank": rank,
                "all_tasks": [],
                "total": 0,
                "open_count": 0,
                "completed_count": 0,
                "overdue_count": 0,
            }

        group = groups[key]
        group["all_tasks"].append(task)
        group["total"] += 1
        if task.status in CLOSED_TASK_STATES:
            group["completed_count"] += 1
        else:
            group["open_count"] += 1
            if task.due_at and task.due_at < now:
                group["overdue_count"] += 1

    ordered = sorted(
        groups.values(),
        key=lambda group: (group["rank"], group["title"].casefold(), group["key"]),
    )
    for group in ordered:
        group["all_tasks"].sort(
            key=lambda task: (
                task.status in CLOSED_TASK_STATES,
                task.due_at is None,
                task.due_at or datetime.max,
                PRIORITY_RANK.get(task.priority, 9),
                task.id,
            )
        )
    return ordered


def filter_task_groups(groups, *, status="open", group_key="all", search=""):
    """Filtra solo dentro de los grupos ya visibles, sin consultar otros proyectos."""
    valid_statuses = {
        "open", "all", "pendiente", "en_proceso", "bloqueada",
        "en_revision", "completada", "cancelada",
    }
    if status not in valid_statuses:
        status = "open"
    search = (search or "").strip().casefold()
    selected = []

    for group in groups:
        if group_key != "all" and group_key != group["key"]:
            continue
        matching = []
        for task in group["all_tasks"]:
            if status == "open" and task.status in CLOSED_TASK_STATES:
                continue
            if status not in {"open", "all"} and task.status != status:
                continue

            assigned_user = getattr(getattr(task, "assignee", None), "user", None)
            searchable = " ".join((
                task.title or "",
                task.task_type or "",
                group["title"],
                group["client_name"],
                assigned_user.name if assigned_user else "",
            )).casefold()
            if search and search not in searchable:
                continue
            matching.append(task)

        if matching:
            selected.append({**group, "tasks": matching, "visible_count": len(matching)})
    return selected
