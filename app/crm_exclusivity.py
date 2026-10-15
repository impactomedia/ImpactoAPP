"""Protección temporal de seguimientos comerciales por 30 días.

Nunca crea un segundo cliente cuando ya existe un contacto coincidente.
Solo pueden recuperarse seguimientos sin actividad reciente dentro del
mismo proyecto empresarial. Un cliente convertido no se libera.
"""
from datetime import datetime, timedelta
import re

from sqlalchemy import func, or_

from app.extensions import db
from app.models import Client, Interaction, OwnershipHistory

PROTECTION_DAYS = 30


def normalize_phone(phone):
    digits = re.sub(r"\D", "", str(phone or ""))
    return digits[1:] if len(digits) == 11 and digits.startswith("1") else digits


def matching_contacts(business_name, phone=None, email=None):
    """Busca nombre, correo o teléfono incluso con distinto formato USA."""
    terms = [func.lower(func.trim(Client.business_name)) == business_name.strip().lower()]
    if email:
        terms.append(func.lower(func.trim(Client.email)) == email.strip().lower())
    digits = normalize_phone(phone)
    if len(digits) >= 7:
        digits_column = Client.phone
        for mark in (" ", "(", ")", "-", ".", "+"):
            digits_column = func.replace(digits_column, mark, "")
        terms.extend((digits_column == digits, digits_column == "1" + digits))
    return Client.query.filter(or_(*terms)).order_by(Client.id.asc()).with_for_update().all()


def last_activity_at(client):
    """Los nuevos registros y las reasignaciones también inician una protección."""
    dates = [client.created_at] if client.created_at else []
    interaction = Interaction.query.filter_by(client_id=client.id).order_by(
        Interaction.occurred_at.desc(), Interaction.id.desc()
    ).first()
    if interaction and interaction.occurred_at:
        dates.append(interaction.occurred_at)
    change = OwnershipHistory.query.filter_by(client_id=client.id).order_by(
        OwnershipHistory.changed_at.desc(), OwnershipHistory.id.desc()
    ).first()
    if change and change.changed_at:
        dates.append(change.changed_at)
    return max(dates) if dates else datetime.utcnow()


def protected_until(client):
    return last_activity_at(client) + timedelta(days=PROTECTION_DAYS)


def is_protected(client, *, now=None):
    return bool(client.record_type != "seguimiento" or
                (now or datetime.utcnow()) < protected_until(client))


def same_business_project(client, new_owner):
    """Impide transferir seguimientos entre IMPACTO y NOVAX."""
    if not new_owner or not client.owner:
        return False
    return client.owner.advisor_project_id == new_owner.advisor_project_id


def classify_match(client, new_owner, *, now=None):
    """Devuelve un estado sin revelar datos de otra cartera al usuario."""
    if client.record_type != "seguimiento":
        return "existing_client"
    if not new_owner:
        return "unassigned"
    if client.owner_id == new_owner.id:
        return "own"
    if not same_business_project(client, new_owner):
        return "other_project"
    if is_protected(client, now=now):
        return "reserved"
    return "recoverable"


def recover_followup(client, new_owner, actor_id):
    """Transfiere sin duplicar, mantiene el historial y reinicia los 30 días."""
    if classify_match(client, new_owner) != "recoverable":
        raise ValueError("El seguimiento no está disponible para recuperarse.")
    before_id = client.owner_id
    db.session.add(OwnershipHistory(
        client_id=client.id,
        previous_owner_id=before_id,
        new_owner_id=new_owner.id,
        reason="Recuperado después de 30 días sin actividad registrada",
        changed_by_id=actor_id,
    ))
    client.owner_id = new_owner.id
    # No se modifica el generador original del lead ni la atribución de ventas.
    db.session.flush()
    return before_id
