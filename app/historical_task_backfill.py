"""Preparación supervisada de proyectos y tareas de clientes históricos.

No crea datos al previsualizar. La ejecución la activa Superadministración.
Nunca modifica el estado, fechas, pagos ni responsables de una tarea existente.
"""
from collections import Counter
from datetime import date

from app.extensions import db
from app.models import Client, ClientContract, Project, Task
from app.plan_benefit_tasks import (
    FINISHED_PROJECT_STATES,
    contract_benefit_entries,
    sync_project_benefits,
)
from app.helpers import next_code


def _unique_project(contract, candidates, active_pair_count, exact_contract_count):
    """Selecciona proyecto solo cuando su correspondencia es inequívoca.

    Devuelve (proyecto, debe_crearse, motivo_de_omisión).
    """
    if exact_contract_count > 1:
        return None, False, "Contratos activos duplicados para la misma venta y plan."

    if contract.sale_id is not None:
        exact = [p for p in candidates if p.sale_id == contract.sale_id]
        if len(exact) == 1:
            return exact[0], False, None
        if len(exact) > 1:
            return None, False, "Hay varios proyectos para la misma venta."
        legacy = [p for p in candidates if p.sale_id is None]
        if legacy:
            if len(legacy) == 1 and active_pair_count == 1:
                return legacy[0], False, None
            return None, False, "Proyecto anterior sin venta identificada; revisar correspondencia."
        return None, True, None

    if active_pair_count > 1:
        return None, False, "Hay varios contratos activos sin venta distinguible."
    manual = [p for p in candidates if p.sale_id is None]
    if len(manual) == 1:
        return manual[0], False, None
    if len(manual) > 1:
        return None, False, "Hay varios proyectos manuales para el plan."
    if len(candidates) == 1:
        return None, False, (
            "El contrato no tiene venta vinculada, pero el proyecto sí; "
            "confirmar que corresponden antes de relacionarlos."
        )
    if len(candidates) > 1:
        return None, False, "Hay varios proyectos de ventas anteriores para el plan."
    return None, True, None


def prepare_registered_clients(*, apply=False):
    """Previsualiza o importa proyectos y beneficios faltantes, de forma idempotente.

    Los clientes sin un contrato verificable no reciben tareas inventadas.
    Las tareas sin proyecto se vinculan únicamente cuando existe un solo
    proyecto y como máximo un contrato para ese cliente.
    La transacción la confirma o revierte la ruta invocadora.
    """
    contracts = (
        ClientContract.query.join(Client, Client.id == ClientContract.client_id)
        .filter(
            Client.record_type == "cliente",
            Client.client_status != "archivado",
            ClientContract.status == "activo",
        )
        .order_by(ClientContract.client_id, ClientContract.id)
        .all()
    )
    projects = Project.query.order_by(Project.id).all()
    client_rows = Client.query.filter(
        Client.record_type == "cliente", Client.client_status != "archivado"
    ).all()
    all_contract_counts = Counter(
        row[0] for row in db.session.query(ClientContract.client_id).all()
    )
    pair_counts = Counter((c.client_id, c.product_id) for c in contracts)
    exact_counts = Counter((c.client_id, c.product_id, c.sale_id) for c in contracts)
    by_client = {}
    for p in projects:
        by_client.setdefault(p.client_id, []).append(p)

    results = []
    orphan_ids_planned = set()
    summary = {
        "clients": len(client_rows),
        "contracts": len(contracts),
        "projects_created": 0,
        "projects_existing": 0,
        "tasks_added": 0,
        "tasks_linked": 0,
        "skipped_no_benefits": 0,
        "skipped_closed": 0,
        "skipped_ambiguous": 0,
    }

    for contract in contracts:
        product = contract.product
        if not product or product.is_physical:
            continue

        entries = contract_benefit_entries(contract)
        if not entries:
            summary["skipped_no_benefits"] += 1
            results.append({
                "client": contract.client.business_name,
                "plan": product.name,
                "status": "Revisar beneficios del contrato",
                "tasks": 0, "linked": 0,
            })
            continue

        candidates = [
            p for p in by_client.get(contract.client_id, [])
            if p.product_id == contract.product_id
        ]
        project, create_project, reason = _unique_project(
            contract, candidates, pair_counts[(contract.client_id, contract.product_id)],
            exact_counts[(contract.client_id, contract.product_id, contract.sale_id)],
        )
        if reason:
            summary["skipped_ambiguous"] += 1
            results.append({
                "client": contract.client.business_name,
                "plan": product.name,
                "status": "Revisión manual: " + reason,
                "tasks": 0, "linked": 0,
            })
            continue

        if project and project.status in FINISHED_PROJECT_STATES:
            summary["skipped_closed"] += 1
            results.append({
                "client": contract.client.business_name,
                "plan": product.name,
                "status": "Proyecto entregado o cerrado, sin reabrir",
                "tasks": 0, "linked": 0,
            })
            continue

        # Solo relacionar tareas huérfanas si NO pueden pertenecer a otro
        # contrato, otro proyecto o una compra adicional ambigua.
        client_projects = by_client.get(contract.client_id, [])
        should_link = (
            all_contract_counts[contract.client_id] == 1
            and ((len(client_projects) == 0 and create_project)
                 or (len(client_projects) == 1 and project is client_projects[0]))
        )
        orphan = (
            Task.query.filter_by(client_id=contract.client_id, project_id=None).all()
            if should_link else []
        )
        existing_tasks = (
            Task.query.filter_by(project_id=project.id).all() if project else []
        )
        seen = {t.title.strip().casefold() for t in existing_tasks + orphan}
        missing = []
        for _, benefit in entries:
            title = benefit[:180]
            key = title.strip().casefold()
            if key and key not in seen:
                seen.add(key)
                missing.append(title)

        if create_project:
            summary["projects_created"] += 1
        else:
            summary["projects_existing"] += 1
        summary["tasks_added"] += len(missing)
        summary["tasks_linked"] += len(orphan)
        results.append({
            "client": contract.client.business_name,
            "plan": product.name,
            "status": "Crear proyecto" if create_project else "Completar proyecto existente",
            "tasks": len(missing), "linked": len(orphan),
        })

        orphan_ids_planned.update(task.id for task in orphan)

        if not apply:
            continue

        if create_project:
            project = Project(
                project_no=next_code("PRJ", Project),
                client_id=contract.client_id,
                product_id=product.id,
                sale_id=contract.sale_id,
                name=f"{product.name} - {contract.client.business_name}"[:180],
                department=product.responsible_area or "Desarrollo",
                status="revision_interna",
                progress=0,
                starts_on=contract.starts_on or date.today(),
                notes=(
                    "Proyecto recuperado desde un contrato histórico. "
                    "Comprobar entregables y estados con las evidencias reales."
                ),
            )
            db.session.add(project)
            db.session.flush()
            by_client.setdefault(contract.client_id, []).append(project)

        for task in orphan:
            task.project_id = project.id
        db.session.flush()
        inserted = sync_project_benefits(project, contract)
        if inserted != len(missing):
            raise RuntimeError(
                "La sincronización produjo una cantidad inesperada de tareas. "
                "No se guardará una importación parcial."
            )

    # Agrupa tareas antiguas que ya tienen un único proyecto inequívoco,
    # incluso cuando ese proyecto no proviene de un contrato con beneficios.
    for client in client_rows:
        client_projects = by_client.get(client.id, [])
        if all_contract_counts[client.id] > 1 or len(client_projects) != 1:
            continue
        project = client_projects[0]
        if project.status in FINISHED_PROJECT_STATES:
            continue
        orphan = [
            task for task in Task.query.filter_by(
                client_id=client.id, project_id=None
            ).all()
            if task.id not in orphan_ids_planned
        ]
        if not orphan:
            continue

        summary["tasks_linked"] += len(orphan)
        results.append({
            "client": client.business_name,
            "plan": project.product.name if project.product else "Proyecto existente",
            "status": "Vincular tareas previas a proyecto único",
            "tasks": 0, "linked": len(orphan),
        })
        if apply:
            for task in orphan:
                task.project_id = project.id
            db.session.flush()

    return {"summary": summary, "rows": results}
