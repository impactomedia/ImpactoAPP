"""Parche seguro de roles Nexora: se ejecuta en la raíz del repositorio.

No conecta a la base ni cambia registros. Edita fuentes existentes tras validar
TODOS los patrones, con backup fuera de la carpeta app/, y actualiza una plantilla.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from datetime import datetime, timezone
import shutil
import sys

ROLE_PROFILES = '''\n\n# Perfil profesional predeterminado: cargo y área se derivan del rol del usuario.\nROLE_PROFILES = {\n    "superadmin": ("Superadministrador", "Administración"),\n    "admin": ("Administración", "Administración"),\n    "manager": ("Gerencia", "Gerencia"),\n    "hr": ("Recursos Humanos", "Recursos Humanos"),\n    "supervisor": ("Supervisor / Coordinador", "Coordinación Comercial"),\n    "advisor": ("Asesor(a) de Ventas", "Ventas"),\n    "developer": ("Desarrollador", "Desarrollo"),\n    "development_coordinator": ("Coordinador de Desarrollo", "Desarrollo"),\n    "production": ("Operaciones / Producción", "Operaciones"),\n    "finance": ("Contabilidad / Finanzas", "Finanzas"),\n    "audit": ("Consulta / Auditoría", "Auditoría"),\n}\n'''


def exactly(content: str, old: str, new: str, context: str) -> str:
    found = content.count(old)
    if found != 1:
        raise ValueError(f"{context}: se esperaba 1 coincidencia del fragmento, encontradas {found}. No se tocará ningún archivo.")
    return content.replace(old, new, 1)


def patch_seed(s):
    s = exactly(s, '    "advisor": "Asesor Comercial",', '    "advisor": "Asesor(a) de Ventas",\n    "developer": "Desarrollador",', "seed: ROLE_MAP")
    s = exactly(s,
        '    "development_coordinator": {\n        "clients.view", "clients.assign", "clients.comment", "clients.files",',
        '    # Perfil mínimo de Desarrollo: consulta de clientes asignados y actualización de tareas propias.\n    "developer": {\n        "clients.view", "tasks.view", "tasks.update",\n    },\n    "development_coordinator": {\n        "clients.view", "clients.assign", "clients.comment", "clients.files",',
        "seed: permisos developer")
    return s


def patch_hr(s):
    s = exactly(s, 'LEAVE_TYPES = {"vacaciones", "permiso", "ausencia"}',
       'LEAVE_TYPES = {"vacaciones", "permiso", "ausencia"}' + ROLE_PROFILES,
       "hr: mapa de perfiles")
    s = exactly(s,
       '        if not role or not role.active:\n            flash("Selecciona un rol válido y activo.", "danger")\n            return redirect(url_for("hr.collaborators"))',
       '        if not role or not role.active:\n            flash("Selecciona un rol válido y activo.", "danger")\n            return redirect(url_for("hr.collaborators"))\n        # Nadie puede ascenderse a roles críticos mediante un POST manual.\n        if role.name in {"superadmin", "admin", "manager"} and not current_user.is_superadmin:\n            abort(403)',
       "hr: elevación privilegios")
    s = exactly(s,
       '        advisor_project_id = request.form.get("advisor_project_id", type=int)\n        if role.name == "advisor" and not advisor_project_id:',
       '        advisor_project_id = request.form.get("advisor_project_id", type=int) if role.name == "advisor" else None\n        if role.name == "advisor" and not advisor_project_id:',
       "hr: limitar equipo comercial")
    s = exactly(s,
       '        user = User(name=name, email=email, role=role, active=True)',
       '        # Cargo y departamento nunca se confían al navegador.\n        job_title, department = ROLE_PROFILES.get(role.name, (role.label or "Colaborador", "General"))\n        user = User(name=name, email=email, role=role, active=True)',
       "hr: servidor fija cargo/área")
    s = exactly(s,
       '            job_title=(request.form.get("job_title") or "Colaborador").strip(),\n            department=(request.form.get("department") or "").strip() or None,',
       '            job_title=job_title,\n            department=department,',
       "hr: guardar perfil")
    return s


def patch_access(s):
    s = exactly(s,
       '    if request.blueprint == "clients" and role == DEVELOPMENT_COORDINATOR_ROLE:\n        abort(403)',
       '    if request.blueprint == "clients" and role in {DEVELOPMENT_COORDINATOR_ROLE, "developer"}:\n        abort(403)',
       "access: prohibir ficha comercial para Desarrollo")
    s = exactly(s,
       '    if role == "production":\n        return False\n\n    return False',
       '    if role in {"production", "developer"}:\n        return False\n\n    return False',
       "access: tareas solo asignadas")
    s = exactly(s,
       '    if role in {"production", "hr"}:\n        return task.assignee_id == collaborator.id or collaborator in task.collaborators',
       '    if role in {"production", "developer", "hr"}:\n        return task.assignee_id == collaborator.id or collaborator in task.collaborators',
       "access: escrituras solo en tarea propia")
    return s


def patch_client_service(s):
    needle='''    if role == "advisor":
        return (
            client.owner_id == collaborator.id
            or ClientTeamAssignment.query.filter_by(
                client_id=client.id,
                collaborator_id=collaborator.id,
                status=ACTIVE_TEAM_STATUS,
            ).first()
            is not None
        )'''
    s=exactly(s,needle,'    if role == "advisor":\n        return client.owner_id == collaborator.id',"clientes: escritura comercial propia")
    needle='''    assigned = _assignment_client_ids([collaborator.id])
    conditions = []

    if role == "advisor":
        conditions.append(Client.owner_id == collaborator.id)

    if assigned:
        conditions.append(Client.id.in_(assigned))'''
    repl='''    # Un asesor tiene estrictamente su cartera como referencia.
    if role == "advisor":
        return query.filter(Client.owner_id == collaborator.id)

    # El desarrollador solo puede consultar clientes con asignación directa
    # o con tareas donde es responsable/participante. Nunca toda la empresa.
    if role == "developer":
        assigned_ids = set(_assignment_client_ids([collaborator.id]))
        own_task = or_(
            Task.assignee_id == collaborator.id,
            Task.collaborators.any(id=collaborator.id),
        )
        for direct_id, project_client_id in (
            db.session.query(Task.client_id, Project.client_id)
            .select_from(Task)
            .outerjoin(Project, Project.id == Task.project_id)
            .filter(own_task)
            .all()
        ):
            if direct_id is not None:
                assigned_ids.add(direct_id)
            if project_client_id is not None:
                assigned_ids.add(project_client_id)
        return query.filter(Client.id.in_(assigned_ids or {-1}))

    assigned = _assignment_client_ids([collaborator.id])
    conditions = []
    if assigned:
        conditions.append(Client.id.in_(assigned))'''
    s=exactly(s,needle,repl,"clientes: filtrar consultas según perfil")
    s=exactly(s,
       '    if role == "production":\n        assigned = ClientTeamAssignment.query.filter_by(',
       '    if role == "developer":\n        return my_clients_query().filter(Client.id == client.id).first() is not None\n\n    if role == "production":\n        assigned = ClientTeamAssignment.query.filter_by(',
       "clientes: validar acceso por ID")
    s=exactly(s,
       '    operational_only = role in {"production", DEVELOPMENT_COORDINATOR_ROLE}',
       '    operational_only = role in {"production", "developer", DEVELOPMENT_COORDINATOR_ROLE}',
       "clientes: timeline operativo")
    s=exactly(s,
       '        if role == "production" and (\n            not current_user.collaborator',
       '        if role in {"production", "developer"} and (\n            not current_user.collaborator',
       "clientes: timeline de asignaciones propias")
    return s


def patch_clients_v3(s):
    s=exactly(s,'    if role in {"production", DEVELOPMENT_COORDINATOR_ROLE}:\n        return set()',
       '    if role in {"production", "developer", DEVELOPMENT_COORDINATOR_ROLE}:\n        return set()',
       "clientes v3: sin ficha comercial")
    s=exactly(s,'    if role == "production":\n        if not collaborator:',
       '    if role in {"production", "developer"}:\n        if not collaborator:',
       "clientes v3: equipos visibles")
    s=exactly(s,'    if role in {"production", DEVELOPMENT_COORDINATOR_ROLE}:\n        can_open_full_detail = False',
       '    if role in {"production", "developer", DEVELOPMENT_COORDINATOR_ROLE}:\n        can_open_full_detail = False',
       "clientes v3: bloquear navegación comercial")
    return s


def patch_dashboard(s):
    s=exactly(s,'    if role in {"production", DEVELOPMENT_COORDINATOR_ROLE}:',
       '    if role in {"production", "developer", DEVELOPMENT_COORDINATOR_ROLE}:',
       "dashboard: clientes propios")
    s=exactly(s,'    if role not in {"superadmin", "admin", "manager"} and collaborator:',
       '    if role == "developer" and not collaborator:\n        return query.filter(Task.id == -1)\n\n    if role not in {"superadmin", "admin", "manager"} and collaborator:',
       "dashboard: tareas sin colaborador")
    return s


def patch_development_scope(s):
    s=exactly(s,'        User.role.has(Role.name == DEVELOPMENT_COORDINATOR_ROLE),',
       '        User.role.has(Role.name.in_([DEVELOPMENT_COORDINATOR_ROLE, "developer"])),',
       "desarrollo: equipo por rol")
    s=exactly(s,'        or role_name == DEVELOPMENT_COORDINATOR_ROLE',
       '        or role_name in {DEVELOPMENT_COORDINATOR_ROLE, "developer"}',
       "desarrollo: reconocer rol")
    return s


def patch_security(s):
    s=exactly(s,'    "development_coordinator",\n    "production",',
       '    "development_coordinator",\n    "developer",\n    "production",',
       "seguridad: rol base protegido")
    s=exactly(s,'    if role_name in {"production", "development_coordinator"}:',
       '    if role_name in {"production", "developer", "development_coordinator"}:',
       "seguridad: alcance asignado")
    return s


def patch_shell(s):
    s=exactly(s,'role_name in [\'production\',\'development_coordinator\']',
       'role_name in [\'production\',\'developer\',\'development_coordinator\']',
       "menú: clientes asignados de Desarrollo")
    return s


def patch_mine(s):
    s=exactly(s,'{% block title %}Mis clientes | Impacto Manager{% endblock %}',
       '{% block title %}Clientes asignados | Impacto Nexora{% endblock %}',
       "clientes: marca")
    s=exactly(s,'<span class="eyebrow">Clientes V2 · Fase 3</span>',
       '<span class="eyebrow">Cartera y asignaciones</span>',
       "clientes: quitar nombre temporal")
    s=exactly(s,"{% if role_name not in ['production','development_coordinator'] %}",
       "{% if role_name not in ['production','developer','development_coordinator'] %}",
       "clientes: ocultar navegación comercial")
    s=exactly(s,'{{ \'Clientes de Desarrollo\' if role_name == \'development_coordinator\' else \'Mis clientes\' }}',
       "{{ 'Clientes de Desarrollo' if role_name == 'development_coordinator' else ('Clientes asignados (consulta)' if role_name == 'developer' else 'Mis clientes') }}",
       "clientes: encabezado por rol")
    s=exactly(s,'{{ \'Clientes vinculados a proyectos y asignaciones del equipo de Desarrollo.\' if role_name == \'development_coordinator\' else \'Clientes vinculados a tu cartera comercial o a tu equipo operativo activo.\' }}',
       "{{ 'Clientes vinculados a proyectos y asignaciones del equipo de Desarrollo.' if role_name == 'development_coordinator' else ('Acceso de solo lectura a los datos de clientes vinculados a tus tareas y asignaciones.' if role_name == 'developer' else 'Clientes vinculados a tu cartera comercial o a tu equipo operativo activo.') }}",
       "clientes: descripción específica")
    s=exactly(s,'>Coordinar</a>',
       ">{{ 'Consultar' if role_name == 'developer' else 'Coordinar' }}</a>",
       "clientes: CTA solo lectura")
    return s

PATCHES={
    'scripts/seed.py':patch_seed,
    'app/blueprints/hr.py':patch_hr,
    'app/access_control.py':patch_access,
    'app/client_v3_services.py':patch_client_service,
    'app/blueprints/clients_v3.py':patch_clients_v3,
    'app/blueprints/dashboard.py':patch_dashboard,
    'app/development_scope.py':patch_development_scope,
    'app/security_controls.py':patch_security,
    'app/templates/app_base.html':patch_shell,
    'app/templates/clients/mine.html':patch_mine,
}


def main():
    parser=argparse.ArgumentParser(description="Aplicar perfiles simplificados y rol Desarrollador en Nexora")
    parser.add_argument('--apply',action='store_true',help='Modificar los archivos (sin esta opción solo valida).')
    parser.add_argument('--repo',default='.',help='Raíz de ImpactoAPP (por defecto el directorio actual).')
    args=parser.parse_args()
    repo=Path(args.repo).resolve()
    if not (repo/'scripts/seed.py').is_file() or not (repo/'app/blueprints/hr.py').is_file():
        parser.error('Este comando debe ejecutarse en la raíz del repositorio ImpactoAPP.')
    generated={}
    try:
        for rel,handler in PATCHES.items():
            original=(repo/rel).read_text(encoding='utf-8')
            generated[rel]=handler(original)
        # plantilla nueva suministrada en el mismo paquete de actualización
        template=Path(__file__).resolve().parents[1]/'roles_payload/hr_collaborators.html'
        generated['app/templates/hr/collaborators.html'] = template.read_text(encoding='utf-8')
        test_payload=Path(__file__).resolve().parents[1]/'tests/test_developer_role_v125.py'
        if test_payload.is_file():
            generated['tests/test_developer_role_v125.py'] = test_payload.read_text(encoding='utf-8')
    except (OSError,ValueError) as exc:
        print('ERROR: ninguna modificación aplicada:',exc,file=sys.stderr)
        return 2
    print('Validación correcta:',len(generated),'archivos preparados.')
    if not args.apply:
        print('Vista previa sin cambios. Ejecuta con --apply para guardar.')
        return 0
    backup=repo.parent / ('.nexora_roles_backup_' + repo.name) / datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    for rel in generated:
        old_path=repo/rel
        if old_path.is_file():
            target=backup/rel
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(old_path,target)
    for rel,code in generated.items():
        dst=repo/rel
        dst.parent.mkdir(parents=True,exist_ok=True)
        dst.write_text(code,encoding='utf-8')
    print('Archivos actualizados:',len(generated))
    print('Backup anterior:',backup)
    print('Ahora ejecuta: python -m scripts.init_db (en el entorno correspondiente) para dar de alta el rol en DB.')
    print('Revisa git diff y ejecuta pytest antes de publicar.')
    return 0

if __name__=='__main__':
    sys.exit(main())
