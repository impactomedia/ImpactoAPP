from __future__ import annotations

from datetime import date, datetime
from difflib import SequenceMatcher
import re

from app.extensions import db
from app.helpers import audit, next_code
from app.models import (
    Client,
    ClientContract,
    Collaborator,
    ProductService,
    Renewal,
    User,
)
from app.client_v2_models import (
    ClientContractDetail,
    ClientImportBatch,
    ClientOperationalProfile,
    ClientPlatform,
)
from app.client_excel_parser import normalize_text


_OWNER_ALIAS_GROUPS = [
    {"jessenia", "jessy"},
    {"roxana", "roxy"},
]


def _digits(value):
    return re.sub(r"\D", "", str(value or ""))


def _business_key(value):
    text = normalize_text(value)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _phone_key(value):
    digits = _digits(value)
    return digits[-10:] if len(digits) >= 10 else digits


def _date_from_iso(value):
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _resolve_owner(advisor_name):
    target = normalize_text(advisor_name)
    if not target:
        return None, "Sin asesor en el Excel"

    candidates = (
        Collaborator.query
        .join(User, Collaborator.user_id == User.id)
        .filter(
            Collaborator.status == "activo",
            User.active.is_(True),
        )
        .all()
    )

    aliases = {target}
    for group in _OWNER_ALIAS_GROUPS:
        if target in group:
            aliases |= group

    exact = []
    starts = []
    for collaborator in candidates:
        if not collaborator.user:
            continue
        full = normalize_text(collaborator.user.name)
        first = full.split()[0] if full else ""
        code = normalize_text(collaborator.code)

        if full in aliases or first in aliases or code in aliases:
            exact.append(collaborator)
        elif any(
            full.startswith(alias + " ") or first.startswith(alias)
            for alias in aliases
        ):
            starts.append(collaborator)

    if len(exact) == 1:
        return exact[0], "Coincidencia exacta"
    if len(exact) > 1:
        return None, "Asesor ambiguo"
    if len(starts) == 1:
        return starts[0], "Coincidencia por nombre"
    return None, "Asesor no reconocido"


def _client_match(payload):
    business = _business_key(payload.get("business_name"))
    email = normalize_text(payload.get("email"))
    phone = _phone_key(payload.get("phone"))

    scored = []
    for client in Client.query.all():
        score = 0
        reasons = []
        client_email = normalize_text(client.email)
        client_phone = _phone_key(client.phone)
        client_business = _business_key(client.business_name)

        if email and client_email and email == client_email:
            score = max(score, 100)
            reasons.append("correo")
        if phone and client_phone and phone == client_phone:
            score = max(score, 97)
            reasons.append("teléfono")
        if business and client_business:
            if business == client_business:
                score = max(score, 94)
                reasons.append("nombre")
            else:
                ratio = SequenceMatcher(
                    None,
                    business,
                    client_business,
                ).ratio()
                if ratio >= 0.93:
                    score = max(score, 88)
                    reasons.append("nombre similar")

        if score:
            scored.append((score, client, ", ".join(reasons)))

    if not scored:
        return None, "new", "No existe coincidencia"

    scored.sort(key=lambda item: (-item[0], item[1].id))
    best = scored[0]
    if len(scored) > 1 and scored[1][0] >= best[0] - 2:
        return None, "ambiguous", "Hay más de un cliente con coincidencia similar"

    return best[1], "existing", f"Coincidencia por {best[2]}"


def _product_mapping(plan_label):
    norm = normalize_text(plan_label)
    if not norm:
        return None, "none", "Sin plan"

    target = None
    confidence = "none"
    reason = "Plan no mapeado"

    if "golden" in norm:
        target, confidence, reason = "Golden", "high", "Golden detectado"
    elif re.search(r"\bvip\b", norm):
        target, confidence, reason = "VIP", "high", "VIP detectado"
    elif "accesor" in norm:
        target, confidence, reason = (
            "Accesorios",
            "high",
            "Accesorios detectado",
        )
    elif "3 meses" in norm:
        target, confidence, reason = (
            "3 Meses",
            "high",
            "Paquete 3 Meses detectado",
        )
    elif "8 meses" in norm:
        target, confidence, reason = (
            "8 Meses",
            "high",
            "Paquete 8 Meses detectado",
        )
    elif norm.strip() == "6 meses":
        target, confidence, reason = (
            "6 Meses",
            "high",
            "Paquete 6 Meses detectado",
        )
    elif "community" in norm:
        target, confidence, reason = (
            "Redes Sociales",
            "medium",
            "Community Manager se relaciona con Redes Sociales",
        )
    elif "esencial plus" in norm:
        target, confidence, reason = (
            None,
            "manual",
            "Esencial Plus requiere revisión manual",
        )
    elif "esencial" in norm:
        target, confidence, reason = (
            "6 Meses",
            "medium",
            "Esencial se relaciona de forma operativa con 6 Meses",
        )

    if not target:
        return None, confidence, reason

    product = ProductService.query.filter_by(name=target).first()
    if not product:
        return None, "manual", f"No existe el producto {target} en catálogo"

    return product, confidence, reason


def analyze_client_payload(payload):
    client, match_state, match_reason = _client_match(payload)
    owner, owner_reason = _resolve_owner(payload.get("advisor"))
    product, product_confidence, product_reason = _product_mapping(
        payload.get("current_plan_label")
    )

    start_date = _date_from_iso(payload.get("plan_start"))
    safe_contract = bool(
        product
        and product_confidence == "high"
        and start_date
        and payload.get("plan_start_precision") == "exact"
        and not payload.get("multiple_investments")
    )

    contract_reason = (
        "Contrato apto para importación segura"
        if safe_contract
        else product_reason
    )
    if payload.get("multiple_investments"):
        contract_reason = (
            "Tiene inversiones adicionales; contrato automático desactivado"
        )
    elif payload.get("plan_start_precision") == "approximate":
        contract_reason = (
            "La fecha de activación es aproximada; requiere revisión"
        )
    elif not start_date and product:
        contract_reason = "No hay fecha exacta de activación"

    return {
        "payload": payload,
        "matched_client": client,
        "match_state": match_state,
        "match_reason": match_reason,
        "owner": owner,
        "owner_reason": owner_reason,
        "product": product,
        "product_confidence": product_confidence,
        "product_reason": product_reason,
        "safe_contract": safe_contract,
        "contract_reason": contract_reason,
    }


def analyze_batch_payload(payload_json):
    return [
        analyze_client_payload(row)
        for row in payload_json.get("clients", [])
    ]


def _set_value(obj, field, value, overwrite=False):
    if value in (None, ""):
        return False

    current = getattr(obj, field, None)
    if not overwrite and current not in (None, ""):
        return False
    if current == value:
        return False

    setattr(obj, field, value)
    return True


def _append_operational_note(profile, text):
    text = (text or "").strip()
    if not text:
        return False

    current = (profile.operational_notes or "").strip()
    if text in current:
        return False

    profile.operational_notes = (
        f"{current}\n\n{text}".strip()
        if current
        else text
    )
    return True


def _upsert_profile(
    client,
    payload,
    *,
    overwrite=False,
    batch_id=None,
):
    source = payload.get("operational") or {}
    profile = ClientOperationalProfile.query.filter_by(
        client_id=client.id
    ).first()

    created = False
    if not profile:
        profile = ClientOperationalProfile(client_id=client.id)
        db.session.add(profile)
        created = True

    changed = False
    fields = [
        "attention_days",
        "business_hours",
        "experience_text",
        "coverage_text",
        "payment_methods",
        "estimate_policy",
        "operational_email",
        "corporate_email",
        "postal_code",
        "domain_name",
        "services_to_promote",
        "logo_status",
        "brand_colors",
        "domain_activated_on",
        "hosting_activated_on",
        "domain_notes",
    ]

    for field in fields:
        value = source.get(field)
        if field.endswith("_on"):
            value = _date_from_iso(value)
        changed |= _set_value(
            profile,
            field,
            value,
            overwrite=overwrite,
        )

    plan_label = (payload.get("current_plan_label") or "").strip()
    if plan_label:
        changed |= _append_operational_note(
            profile,
            (
                f"Importación Excel lote #{batch_id}: "
                f"plan reportado en la ficha: {plan_label}. "
                "Las inversiones/pagos históricos no se importaron "
                "automáticamente."
            ),
        )

    return profile, created, changed


def _upsert_platforms(client, payload, *, overwrite=False):
    created = 0
    updated = 0

    for source in payload.get("platforms") or []:
        key = source.get("key")
        if not key:
            continue

        row = ClientPlatform.query.filter_by(
            client_id=client.id,
            platform_key=key,
        ).first()
        was_new = row is None

        if was_new:
            row = ClientPlatform(
                client_id=client.id,
                platform_key=key,
                label=(source.get("label") or key)[:120],
                status=source.get("status") or "pendiente",
            )
            db.session.add(row)
            created += 1

        changed = False
        changed |= _set_value(
            row,
            "label",
            (source.get("label") or key)[:120],
            overwrite=overwrite,
        )
        changed |= _set_value(
            row,
            "url",
            source.get("url"),
            overwrite=overwrite,
        )
        changed |= _set_value(
            row,
            "notes",
            source.get("notes"),
            overwrite=overwrite,
        )

        status = source.get("status")
        if status and (was_new or overwrite or not row.status):
            if row.status != status:
                row.status = status
                changed = True

        if changed and not was_new:
            updated += 1

    return created, updated


def _upsert_safe_contract(
    client,
    payload,
    analysis,
    *,
    overwrite=False,
):
    if not analysis["safe_contract"]:
        return None, False, "manual_review"

    product = analysis["product"]
    starts_on = _date_from_iso(payload.get("plan_start"))
    ends_on = _date_from_iso(payload.get("plan_end"))

    contract = ClientContract.query.filter_by(
        client_id=client.id,
        product_id=product.id,
        starts_on=starts_on,
    ).first()

    created = False
    if not contract:
        contract = ClientContract(
            client_id=client.id,
            product_id=product.id,
            starts_on=starts_on,
            ends_on=ends_on,
            status=(
                "caducado"
                if ends_on and ends_on < date.today()
                else "activo"
            ),
            principal=not any(row.principal for row in client.contracts),
            notes=(
                "Importado desde Excel operativo. "
                f"Etiqueta original: {payload.get('current_plan_label')}."
            ),
        )
        db.session.add(contract)
        db.session.flush()
        created = True
    elif overwrite and ends_on and contract.ends_on != ends_on:
        contract.ends_on = ends_on
        contract.status = (
            "caducado"
            if ends_on < date.today()
            else "activo"
        )

    detail = ClientContractDetail.query.filter_by(
        contract_id=contract.id
    ).first()
    if not detail:
        detail = ClientContractDetail(
            contract_id=contract.id,
            modality_snapshot=product.modality,
            maintenance_snapshot=product.maintenance,
        )
        db.session.add(detail)

    if overwrite or not detail.benefits_snapshot:
        if payload.get("benefits"):
            detail.benefits_snapshot = payload["benefits"]

    if overwrite or not detail.courtesies_snapshot:
        if payload.get("courtesies"):
            detail.courtesies_snapshot = payload["courtesies"]

    if ends_on and product.renewal_required:
        renewal = Renewal.query.filter_by(
            client_id=client.id,
            contract_id=contract.id,
            due_date=ends_on,
        ).first()
        if not renewal:
            db.session.add(
                Renewal(
                    client_id=client.id,
                    contract_id=contract.id,
                    renewal_type=product.name,
                    due_date=ends_on,
                    status="pendiente",
                    notes=(
                        "Creada desde importación controlada "
                        "del Excel operativo."
                    ),
                )
            )

    return contract, created, "ok"


def execute_batch(
    batch,
    *,
    create_missing=True,
    overwrite_existing=False,
    replace_owner=False,
    import_contracts=True,
):
    if batch.status == "completed":
        raise ValueError("Este lote ya fue importado.")

    result = {
        "created_clients": 0,
        "updated_clients": 0,
        "skipped_clients": 0,
        "ambiguous_clients": 0,
        "profiles_created": 0,
        "platforms_created": 0,
        "platforms_updated": 0,
        "contracts_created": 0,
        "contracts_existing": 0,
        "contracts_manual_review": 0,
        "financial_rows_imported": 0,
        "items": [],
    }

    for source in batch.payload_json.get("clients", []):
        analysis = analyze_client_payload(source)
        client = analysis["matched_client"]
        action = "updated"

        if analysis["match_state"] == "ambiguous":
            result["ambiguous_clients"] += 1
            result["skipped_clients"] += 1
            result["items"].append(
                {
                    "slot": source.get("slot"),
                    "business_name": source.get("business_name"),
                    "action": "skipped",
                    "reason": analysis["match_reason"],
                }
            )
            continue

        if not client:
            if not create_missing:
                result["skipped_clients"] += 1
                result["items"].append(
                    {
                        "slot": source.get("slot"),
                        "business_name": source.get("business_name"),
                        "action": "skipped",
                        "reason": (
                            "La creación de clientes nuevos "
                            "estaba desactivada."
                        ),
                    }
                )
                continue

            client = Client(
                code=next_code("CLI", Client),
                business_name=source["business_name"],
                contact_name=(
                    source.get("contact_name")
                    or source["business_name"]
                ),
                record_type="cliente",
                pipeline_stage="venta_cerrada",
                client_status="activo",
                priority="media",
                source="Importación Excel operativo",
            )
            db.session.add(client)
            db.session.flush()
            action = "created"
            result["created_clients"] += 1
        else:
            result["updated_clients"] += 1
            was_followup = client.record_type != "cliente"
            client.record_type = "cliente"
            client.pipeline_stage = "venta_cerrada"
            if was_followup and not client.client_status:
                client.client_status = "activo"

        base_fields = {
            "business_name": source.get("business_name"),
            "contact_name": source.get("contact_name"),
            "phone": source.get("phone"),
            "other_phones": source.get("other_phones"),
            "email": (source.get("email") or "").lower(),
            "address": source.get("address"),
            "website": source.get("website"),
            "facebook": source.get("facebook"),
            "instagram": source.get("instagram"),
        }

        for field, value in base_fields.items():
            _set_value(
                client,
                field,
                value,
                overwrite=overwrite_existing,
            )

        client.country = "USA"

        services_to_promote = (
            source.get("operational") or {}
        ).get("services_to_promote")
        _set_value(
            client,
            "services_offered",
            services_to_promote,
            overwrite=False,
        )

        owner = analysis["owner"]
        if owner and (not client.owner_id or replace_owner):
            client.owner_id = owner.id

        _, profile_created, _ = _upsert_profile(
            client,
            source,
            overwrite=overwrite_existing,
            batch_id=batch.id,
        )
        if profile_created:
            result["profiles_created"] += 1

        p_created, p_updated = _upsert_platforms(
            client,
            source,
            overwrite=overwrite_existing,
        )
        result["platforms_created"] += p_created
        result["platforms_updated"] += p_updated

        contract_action = "disabled"
        if import_contracts:
            contract, contract_created, contract_action = (
                _upsert_safe_contract(
                    client,
                    source,
                    analysis,
                    overwrite=overwrite_existing,
                )
            )

            if contract_action == "manual_review":
                result["contracts_manual_review"] += 1
            elif contract:
                if contract_created:
                    result["contracts_created"] += 1
                else:
                    result["contracts_existing"] += 1

        audit(
            "importar_excel_operativo_cliente",
            "Client",
            client.id,
            after={
                "batch_id": batch.id,
                "slot": source.get("slot"),
                "action": action,
                "secret_fields_skipped": source.get(
                    "secret_fields_skipped",
                    0,
                ),
                "contract_action": contract_action,
            },
        )

        result["items"].append(
            {
                "slot": source.get("slot"),
                "business_name": source.get("business_name"),
                "client_id": client.id,
                "action": action,
                "contract_action": contract_action,
            }
        )

    batch.status = "completed"
    batch.executed_at = datetime.utcnow()
    batch.result_json = result

    audit(
        "importar_excel_operativo_lote",
        "ClientImportBatch",
        batch.id,
        after={
            "created": result["created_clients"],
            "updated": result["updated_clients"],
            "skipped": result["skipped_clients"],
            "secret_fields_imported": False,
        },
    )

    return result
