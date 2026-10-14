"""Metadatos de modificaciones de comentarios, usando auditoría existente.

No requiere alterar tablas ni ejecutar migraciones de producción.
"""
from app.models import AuditLog


ACTIONS = {
    "ClientComment": "editar_comentario_cliente",
    "Interaction": "editar_nota_seguimiento",
}


def edit_history(entity, records):
    """Devuelve {id: {at, by, count}} y los logs de edición ordenados."""
    ids = [str(row.id) for row in records]
    if not ids or entity not in ACTIONS:
        return {}, []

    logs = (
        AuditLog.query.filter(
            AuditLog.entity == entity,
            AuditLog.action == ACTIONS[entity],
            AuditLog.entity_id.in_(ids),
        ).order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).all()
    )
    state = {}
    for log in logs:
        key = int(log.entity_id)
        if key not in state:
            state[key] = {
                "at": log.created_at,
                "by": log.user.name if log.user else "Usuario no disponible",
                "count": 0,
            }
        state[key]["count"] += 1
    return state, logs


def may_edit_note(user, author_id, permission):
    """El autor puede editar con permiso; administradores pueden corregir."""
    return (
        user.is_authenticated
        and user.has_permission(permission)
        and (
            user.id == author_id
            or user.is_superadmin
            or bool(user.role and user.role.name == "admin")
        )
    )
