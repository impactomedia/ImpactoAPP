"""Entregables verificables de cada plan contratado, sin crear obligaciones ficticias.

Se utiliza la copia de beneficios del contrato cuando existe, no la descripción
actual del catálogo. Las condiciones comerciales no son entregables.
"""
from datetime import datetime

from app.extensions import db
from app.models import ClientContract, Project, Task
from app.client_v2_models import ClientContractDetail

IGNORED_SECTIONS = {"CONDICIONES COMERCIALES", "CORTESÍAS", "CORTESIAS"}
TASK_TYPE = "beneficio_plan"


def contracted_benefits(text):
    """Devuelve (sección, beneficio) a partir del catálogo/snapshot del contrato."""
    current_section = "Beneficios contratados"
    benefits = []
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith(("- ", "• ")):
            if current_section and current_section.upper() not in IGNORED_SECTIONS:
                feature = line[2:].strip()
                if feature and not feature.lower().startswith("no incluye"):
                    benefits.append((current_section, feature))
        else:
            current_section = line.strip(": ")
    return benefits


def _contract_for_project(project):
    if not project.product_id:
        return None
    query = ClientContract.query.filter_by(client_id=project.client_id, product_id=project.product_id)
    if project.sale_id:
        return query.filter_by(sale_id=project.sale_id).order_by(ClientContract.id.desc()).first()
    # Contratos manuales: solo hacer atribución automática cuando no hay ambigüedad.
    matches = query.limit(2).all()
    return matches[0] if len(matches) == 1 else None


def sync_project_benefits(project, contract=None):
    """Una tarea por beneficio; idempotente e independiente del estado de ejecución.

    Los ítems existentes nunca se reabren ni se sobreescriben. No se incluyen
    regalos opcionales no seleccionados ni condiciones comerciales.
    """
    if not project or not project.product_id:
        return 0
    contract = contract or _contract_for_project(project)
    if contract is None:
        return 0

    detail = ClientContractDetail.query.filter_by(contract_id=contract.id).first()
    snapshot = detail.benefits_snapshot if detail and detail.benefits_snapshot else (
        project.product.components if project.product else None
    )
    benefits = contracted_benefits(snapshot)
    if detail and detail.courtesies_snapshot:
        for courtesy in detail.courtesies_snapshot.splitlines():
            courtesy = courtesy.strip(" -•\t")
            if courtesy:
                benefits.append(("Cortesía contratada", courtesy))

    existing = {
        (row.title, row.task_type)
        for row in Task.query.filter_by(project_id=project.id).all()
    }
    inserted = 0
    for section, benefit in benefits:
        title = benefit[:180]
        key = (title, TASK_TYPE)
        if key in existing:
            continue
        db.session.add(Task(
            title=title,
            description=f"Beneficio contratado · {section}. Plan: {project.product.name}.",
            task_type=TASK_TYPE,
            client_id=project.client_id,
            project_id=project.id,
            priority="media",
            status="pendiente",
            starts_at=datetime.utcnow(),
        ))
        existing.add(key)
        inserted += 1
    db.session.flush()
    return inserted


def sync_existing_plan_benefits():
    """Actualiza contratos históricos de manera segura; no duplica tareas."""
    total = 0
    projects = Project.query.filter(
        Project.product_id.isnot(None),
    ).order_by(Project.id).all()
    for project in projects:
        total += sync_project_benefits(project)
    return total
