"""Aislamiento empresarial de cuentas con rol admin.

El proyecto comercial asignado al COLABORADOR es el límite obligatorio.
La falta de proyecto no concede acceso global. Superadmin no usa este filtro.
"""
from flask_login import current_user
from app.models import Client, Collaborator


def is_project_admin():
    return bool(current_user.is_authenticated and current_user.role and
                current_user.role.name == "admin")


def project_id_for_admin():
    if not is_project_admin():
        return None
    collaborator = current_user.collaborator
    return collaborator.advisor_project_id if collaborator else None


def client_scope_condition():
    project_id = project_id_for_admin()
    return Client.owner.has(Collaborator.advisor_project_id == (project_id or -1))


def client_allowed(client):
    return bool(client and client.owner and project_id_for_admin() and
                client.owner.advisor_project_id == project_id_for_admin())


def collaborator_allowed(collaborator):
    return bool(collaborator and project_id_for_admin() and
                collaborator.advisor_project_id == project_id_for_admin())
