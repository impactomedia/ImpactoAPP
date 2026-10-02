from sqlalchemy import or_

from app.models import Collaborator, Project, ProductService, Role, User


DEVELOPMENT_COORDINATOR_ROLE = "development_coordinator"


def development_project_condition():
    """Condición SQL reutilizable para identificar proyectos del área de Desarrollo."""
    return or_(
        Project.department.ilike("%desarrollo%"),
        Project.department.ilike("%development%"),
        Project.product.has(
            or_(
                ProductService.responsible_area.ilike("%desarrollo%"),
                ProductService.responsible_area.ilike("%development%"),
            )
        ),
    )


def development_projects_query(query=None):
    query = query if query is not None else Project.query
    return query.filter(development_project_condition())


def is_development_project(project):
    """Evalúa un proyecto cargado sin depender de una consulta adicional."""
    department = (getattr(project, "department", None) or "").strip().lower()
    if "desarrollo" in department or "development" in department:
        return True

    product = getattr(project, "product", None)
    area = (getattr(product, "responsible_area", None) or "").strip().lower()
    return "desarrollo" in area or "development" in area


def development_team_query(active_only=True):
    """Colaboradores que pertenecen al equipo de Desarrollo.

    Se reconoce el área por departamento/cargo y siempre se incluye el rol
    específico de Coordinador de Desarrollo.
    """
    query = Collaborator.query.join(User, Collaborator.user_id == User.id)

    area_condition = or_(
        Collaborator.department.ilike("%desarrollo%"),
        Collaborator.department.ilike("%development%"),
        Collaborator.job_title.ilike("%desarroll%"),
        Collaborator.job_title.ilike("%developer%"),
        User.role.has(Role.name == DEVELOPMENT_COORDINATOR_ROLE),
    )
    query = query.filter(area_condition)

    if active_only:
        query = query.filter(
            Collaborator.status == "activo",
            User.active.is_(True),
        )

    return query


def is_development_collaborator(collaborator):
    if not collaborator:
        return False

    department = (collaborator.department or "").strip().lower()
    job_title = (collaborator.job_title or "").strip().lower()
    role_name = (
        collaborator.user.role.name
        if collaborator.user and collaborator.user.role
        else ""
    )
    return (
        "desarrollo" in department
        or "development" in department
        or "desarroll" in job_title
        or "developer" in job_title
        or role_name == DEVELOPMENT_COORDINATOR_ROLE
    )
