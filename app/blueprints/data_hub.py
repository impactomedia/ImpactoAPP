from __future__ import annotations

import csv
import io
import os
from datetime import datetime
from pathlib import Path

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_login import current_user, login_required
from sqlalchemy import or_
from werkzeug.utils import secure_filename

from app.catalog_runtime import catalog_options
from app.client_v2_models import ClientImportBatch
from app.data_exchange import (
    TabularImportError,
    business_key,
    canonical_contact_row,
    normalize_key,
    normalize_text,
    parse_tabular_file,
    phone_key,
)
from app.decorators import permission_required, roles_required
from app.extensions import db
from app.helpers import audit, next_code
from app.models import AdvisorProject, Client, Collaborator, User
from app.security_controls import BUILTIN_ROLES, permission_scope


bp = Blueprint("data_hub", __name__, url_prefix="/data")


def _role_name():
    return current_user.role.name if current_user.role else ""


def _team_ids():
    collaborator = current_user.collaborator
    if not collaborator:
        return []
    return [collaborator.id] + [row.id for row in collaborator.subordinates]


def _custom_role_company_scope(permission_code):
    role = _role_name()
    if not role or role in BUILTIN_ROLES:
        return True
    return permission_scope(current_user, permission_code) == "company"


def _visible_clients_query(query=None, permission_code="clients.view"):
    query = query if query is not None else Client.query
    role = _role_name()
    collaborator = current_user.collaborator

    if role not in BUILTIN_ROLES:
        if not _custom_role_company_scope(permission_code):
            return query.filter(Client.id == -1)
        return query

    if role == "advisor":
        if not collaborator:
            return query.filter(Client.id == -1)
        return query.filter(Client.owner_id == collaborator.id)

    if role == "supervisor":
        if not collaborator:
            return query.filter(Client.id == -1)
        return query.filter(Client.owner_id.in_(_team_ids()))

    if role in {"production", "development_coordinator"}:
        if not collaborator:
            return query.filter(Client.id == -1)
        from app.client_v3_services import my_clients_query

        allowed = [
            row[0]
            for row in my_clients_query().with_entities(Client.id).all()
        ]
        return query.filter(Client.id.in_(allowed or [-1]))

    return query


def _assignable_collaborators():
    query = (
        Collaborator.query
        .join(User, Collaborator.user_id == User.id)
        .filter(
            Collaborator.status == "activo",
            User.active.is_(True),
        )
    )
    role = _role_name()
    collaborator = current_user.collaborator

    if role == "advisor":
        if not collaborator:
            return []
        query = query.filter(Collaborator.id == collaborator.id)
    elif role == "supervisor":
        if not collaborator:
            return []
        query = query.filter(Collaborator.id.in_(_team_ids()))

    return query.order_by(User.name).all()


def _owner_aliases(collaborators):
    aliases = {}
    for collaborator in collaborators:
        if not collaborator.user:
            continue
        full = normalize_text(collaborator.user.name)
        if full:
            aliases.setdefault(full, collaborator.id)
            aliases.setdefault(full.split()[0], collaborator.id)
        if collaborator.code:
            aliases.setdefault(normalize_text(collaborator.code), collaborator.id)
    return aliases


def _batch_allowed(batch):
    if _role_name() in {"superadmin", "admin", "manager"}:
        return True
    return batch.created_by_id == current_user.id


def _general_batches():
    batches = (
        ClientImportBatch.query
        .order_by(ClientImportBatch.created_at.desc())
        .limit(40)
        .all()
    )
    result = []
    for batch in batches:
        payload = batch.payload_json or {}
        if payload.get("kind") != "general_contacts_v12":
            continue
        if _batch_allowed(batch):
            result.append(batch)
        if len(result) >= 10:
            break
    return result


def _duplicate_indexes():
    by_email = {}
    by_phone = {}
    by_business = {}

    for client in Client.query.all():
        email = (client.email or "").strip().lower()
        phone = phone_key(client.phone)
        business = business_key(client.business_name)
        if email:
            by_email.setdefault(email, client.id)
        if phone:
            by_phone.setdefault(phone, client.id)
        if business:
            by_business.setdefault(business, client.id)

    return by_email, by_phone, by_business


def _find_duplicate_id(row, indexes):
    by_email, by_phone, by_business = indexes
    email = (row.get("email") or "").strip().lower()
    phone = phone_key(row.get("phone"))
    business = business_key(row.get("business_name"))

    if email and email in by_email:
        return by_email[email], "Correo ya registrado"
    if phone and phone in by_phone:
        return by_phone[phone], "Teléfono ya registrado"
    if business and business in by_business:
        return by_business[business], "Negocio ya registrado"
    return None, None


def _analyze_rows(parsed_rows):
    indexes = _duplicate_indexes()
    visible_ids = {
        row[0]
        for row in _visible_clients_query(Client.query)
        .with_entities(Client.id)
        .all()
    }

    file_seen = set()
    output = []
    summary = {"ready": 0, "duplicate": 0, "rejected": 0}

    for source in parsed_rows:
        row = canonical_contact_row(source)
        business = row["business_name"].strip()

        if not business:
            row.update(
                status="rejected",
                reason="Falta business_name / negocio / empresa",
                existing_client_id=None,
                existing_visible=False,
            )
            summary["rejected"] += 1
            output.append(row)
            continue

        local_keys = {
            key
            for key in (
                f"email:{row['email']}" if row["email"] else "",
                f"phone:{phone_key(row['phone'])}" if phone_key(row["phone"]) else "",
                f"business:{business_key(business)}" if business_key(business) else "",
            )
            if key
        }
        repeated = bool(local_keys & file_seen)
        file_seen |= local_keys

        if repeated:
            row.update(
                status="duplicate",
                reason="Registro repetido dentro del archivo",
                existing_client_id=None,
                existing_visible=False,
            )
            summary["duplicate"] += 1
            output.append(row)
            continue

        duplicate_id, reason = _find_duplicate_id(row, indexes)
        if duplicate_id:
            row.update(
                status="duplicate",
                reason=reason,
                existing_client_id=duplicate_id if duplicate_id in visible_ids else None,
                existing_visible=duplicate_id in visible_ids,
            )
            summary["duplicate"] += 1
            output.append(row)
            continue

        row.update(
            status="ready",
            reason="Listo para importar",
            existing_client_id=None,
            existing_visible=False,
        )
        summary["ready"] += 1
        output.append(row)

    return output, summary


def _csv_response(filename, headers, rows):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    writer.writerows(rows)
    return Response(
        "\ufeff" + buf.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _backup_dir():
    from scripts.backup import backup_directory
    return backup_directory()


def _backup_files():
    directory = _backup_dir()
    if not directory.exists():
        return []

    paths = sorted(
        (
            path
            for path in directory.glob("impacto_nexora_*.zip")
            if path.is_file()
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )[:20]

    return [
        {
            "name": path.name,
            "size_mb": round(path.stat().st_size / 1024 / 1024, 2),
            "modified": datetime.fromtimestamp(path.stat().st_mtime),
        }
        for path in paths
    ]


def _integration_statuses():
    return [
        {
            "name": "Email corporativo / SMTP",
            "configured": bool(current_app.config.get("SMTP_HOST")),
            "note": "Disponible para recuperación de contraseña, 2FA y futuros correos automáticos.",
        },
        {
            "name": "WhatsApp Business API",
            "configured": bool(os.getenv("WHATSAPP_API_TOKEN")),
            "note": "Preparado para integrar mensajería cuando se definan credenciales/proveedor.",
        },
        {
            "name": "Telefonía / VoIP",
            "configured": bool(os.getenv("VOIP_PROVIDER")),
            "note": "Futura integración para llamadas y registro automático.",
        },
        {
            "name": "Google / Microsoft Calendar",
            "configured": bool(
                os.getenv("GOOGLE_CALENDAR_CREDENTIALS")
                or os.getenv("MICROSOFT_CALENDAR_CLIENT_ID")
            ),
            "note": "Futura sincronización de agenda, seguimientos y entregas.",
        },
        {
            "name": "Pasarela / conciliación de pagos",
            "configured": bool(os.getenv("PAYMENT_INTEGRATION_URL")),
            "note": "Futura conciliación de cobros externos.",
        },
        {
            "name": "Almacenamiento cloud",
            "configured": bool(os.getenv("CLOUD_STORAGE_BUCKET")),
            "note": "Recomendado para copias offsite y archivos de clientes.",
        },
        {
            "name": "Firma electrónica",
            "configured": bool(os.getenv("ESIGN_PROVIDER")),
            "note": "Futura firma de contratos/documentos.",
        },
        {
            "name": "Sistema contable externo",
            "configured": bool(os.getenv("ACCOUNTING_INTEGRATION_URL")),
            "note": "Futura sincronización contable.",
        },
    ]


@bp.route("/")
@login_required
def index():
    can_import = current_user.has_permission("clients.create") and _role_name() in {
        "superadmin", "admin", "manager", "supervisor", "advisor"
    }
    can_export_clients = current_user.has_permission("clients.export")
    can_export_crm = current_user.has_permission("crm.export")
    can_backups = _role_name() in {"superadmin", "admin", "manager"}

    return render_template(
        "data_hub/index.html",
        can_import=can_import,
        can_export_clients=can_export_clients,
        can_export_crm=can_export_crm,
        can_backups=can_backups,
        batches=_general_batches(),
        backups=_backup_files() if can_backups else [],
        integrations=_integration_statuses() if can_backups else [],
        advisor_projects=AdvisorProject.query.filter_by(active=True).order_by(AdvisorProject.name).all(),
        collaborators=_assignable_collaborators(),
        sources=catalog_options("prospect_source"),
        stages=catalog_options("pipeline_stage"),
        volume_mount=os.getenv("RAILWAY_VOLUME_MOUNT_PATH"),
    )


@bp.route("/import/template.csv")
@login_required
def import_template():
    if not current_user.has_permission("clients.create"):
        abort(403)

    headers = [
        "business_name",
        "contact_name",
        "phone",
        "email",
        "source",
        "pipeline_stage",
        "record_type",
        "priority",
        "advisor",
        "industry",
        "website",
        "city",
        "state",
        "notes",
    ]
    example = [
        "Ejemplo Company LLC",
        "Nombre Contacto",
        "(000) 000-0000",
        "contacto@example.com",
        "Referido",
        "nuevo",
        "seguimiento",
        "media",
        "",
        "Landscaping",
        "https://example.com",
        "Orlando",
        "FL",
        "Fila de ejemplo: elimínala antes de importar datos reales.",
    ]
    return _csv_response("plantilla_importacion_nexora.csv", headers, [example])


@bp.route("/import", methods=["POST"])
@login_required
@roles_required("superadmin", "admin", "manager", "supervisor", "advisor")
@permission_required("clients.create")
def import_upload():
    uploaded = request.files.get("file")
    if not uploaded or not uploaded.filename:
        flash("Selecciona un archivo CSV o XLSX.", "danger")
        return redirect(url_for("data_hub.index"))

    file_name = secure_filename(Path(uploaded.filename).name) or "importacion.csv"
    file_bytes = uploaded.read()

    try:
        parsed = parse_tabular_file(file_bytes, file_name)
    except TabularImportError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("data_hub.index"))

    rows, summary = _analyze_rows(parsed["rows"])
    payload = {
        "kind": "general_contacts_v12",
        "format": parsed["format"],
        "headers": parsed["headers"],
        "rows": rows,
        "summary": summary,
        "security": {
            "original_file_stored": False,
            "raw_file_removed_after_preview": True,
        },
    }

    batch = ClientImportBatch(
        file_name=file_name,
        file_sha256=parsed["file_sha256"],
        file_size=parsed["file_size"],
        status="previewed",
        client_count=len(rows),
        payload_json=payload,
        created_by_id=current_user.id,
    )
    db.session.add(batch)
    db.session.flush()

    audit(
        "previsualizar_importacion_general",
        "ClientImportBatch",
        batch.id,
        after={
            "file_name": file_name,
            "format": parsed["format"],
            **summary,
        },
    )
    db.session.commit()

    flash(
        (
            f"Vista previa creada: {summary['ready']} listo(s), "
            f"{summary['duplicate']} duplicado(s) y "
            f"{summary['rejected']} rechazado(s)."
        ),
        "success" if summary["ready"] else "warning",
    )
    return redirect(url_for("data_hub.import_preview", batch_id=batch.id))


@bp.route("/import/<int:batch_id>")
@login_required
def import_preview(batch_id):
    batch = db.get_or_404(ClientImportBatch, batch_id)
    payload = batch.payload_json or {}
    if payload.get("kind") != "general_contacts_v12" or not _batch_allowed(batch):
        abort(403)

    return render_template(
        "data_hub/preview.html",
        batch=batch,
        payload=payload,
        rows=payload.get("rows", []),
        collaborators=_assignable_collaborators(),
        sources=catalog_options("prospect_source"),
        stages=catalog_options("pipeline_stage"),
        priorities=catalog_options("priority"),
    )


@bp.route("/import/<int:batch_id>/execute", methods=["POST"])
@login_required
@roles_required("superadmin", "admin", "manager", "supervisor", "advisor")
@permission_required("clients.create")
def import_execute(batch_id):
    batch = db.get_or_404(ClientImportBatch, batch_id)
    payload = batch.payload_json or {}
    if payload.get("kind") != "general_contacts_v12" or not _batch_allowed(batch):
        abort(403)
    if batch.status == "completed":
        flash("Este lote ya fue ejecutado.", "info")
        return redirect(url_for("data_hub.import_preview", batch_id=batch.id))
    if not request.form.get("confirm_import"):
        flash("Confirma que revisaste la vista previa.", "danger")
        return redirect(url_for("data_hub.import_preview", batch_id=batch.id))

    assignable = _assignable_collaborators()
    assignable_ids = {row.id for row in assignable}
    aliases = _owner_aliases(assignable)

    owner_override = request.form.get("owner_id", type=int)
    if owner_override and owner_override not in assignable_ids:
        abort(403)

    if not current_user.has_permission("crm.transfer"):
        owner_override = current_user.collaborator.id if current_user.collaborator else None

    source_override = (request.form.get("source") or "").strip()
    valid_sources = {row["value"] for row in catalog_options("prospect_source")}
    if source_override and source_override not in valid_sources:
        source_override = ""

    stage_override = (request.form.get("pipeline_stage") or "").strip()
    valid_stages = {row["value"] for row in catalog_options("pipeline_stage")}
    if stage_override and stage_override not in valid_stages:
        stage_override = ""

    record_type_override = (request.form.get("record_type") or "from_file").strip()
    if record_type_override not in {"from_file", "seguimiento", "cliente"}:
        record_type_override = "from_file"

    valid_priorities = {row["value"] for row in catalog_options("priority")}
    created = 0
    skipped_duplicates = 0
    rejected = []
    created_ids = []

    try:
        # Se vuelve a revisar duplicidad al confirmar para cubrir cambios
        # ocurridos entre la vista previa y la ejecución.
        indexes = _duplicate_indexes()
        for row in payload.get("rows", []):
            if row.get("status") == "rejected":
                rejected.append({
                    "line": row.get("line"),
                    "business_name": row.get("business_name"),
                    "reason": row.get("reason"),
                })
                continue

            if row.get("status") == "duplicate":
                skipped_duplicates += 1
                rejected.append({
                    "line": row.get("line"),
                    "business_name": row.get("business_name"),
                    "reason": row.get("reason"),
                })
                continue

            duplicate_id, duplicate_reason = _find_duplicate_id(row, indexes)
            if duplicate_id:
                skipped_duplicates += 1
                rejected.append({
                    "line": row.get("line"),
                    "business_name": row.get("business_name"),
                    "reason": f"{duplicate_reason} al momento de confirmar",
                })
                continue

            record_type = record_type_override
            if record_type == "from_file":
                file_type = normalize_key(row.get("record_type"))
                record_type = "cliente" if file_type == "cliente" else "seguimiento"

            owner_id = owner_override
            if not owner_id and current_user.has_permission("crm.transfer"):
                advisor_name = normalize_text(row.get("advisor"))
                owner_id = aliases.get(advisor_name) if advisor_name else None
            if not owner_id and current_user.collaborator:
                owner_id = current_user.collaborator.id
            if owner_id and owner_id not in assignable_ids:
                owner_id = None

            source = source_override or (row.get("source") or "").strip() or None
            if source and valid_sources and source not in valid_sources:
                # Se conserva el texto importado si no coincide con catálogo,
                # pero la asignación masiva solo acepta valores configurados.
                source = (row.get("source") or "").strip() or None

            priority = normalize_key(row.get("priority")) or "media"
            if priority not in valid_priorities:
                priority = "media"

            if record_type == "cliente":
                stage = "venta_cerrada"
            else:
                stage = stage_override or normalize_key(row.get("pipeline_stage")) or "nuevo"
                if stage not in valid_stages:
                    stage = "nuevo"

            client = Client(
                code=next_code("CLI", Client),
                business_name=(row.get("business_name") or "").strip(),
                contact_name=(row.get("contact_name") or row.get("business_name") or "").strip(),
                phone=(row.get("phone") or "").strip() or None,
                email=(row.get("email") or "").strip().lower() or None,
                source=source,
                priority=priority,
                owner_id=owner_id,
                industry=(row.get("industry") or "").strip() or None,
                website=(row.get("website") or "").strip() or None,
                city=(row.get("city") or "").strip() or None,
                state=(row.get("state") or "").strip() or None,
                country="USA",
                notes=(row.get("notes") or "").strip() or None,
                record_type=record_type,
                pipeline_stage=stage,
                client_status="activo",
            )
            db.session.add(client)
            db.session.flush()

            created += 1
            created_ids.append(client.id)

            # Actualiza índices en memoria para impedir duplicados internos
            # durante una misma transacción.
            if client.email:
                indexes[0][client.email.lower()] = client.id
            if phone_key(client.phone):
                indexes[1][phone_key(client.phone)] = client.id
            if business_key(client.business_name):
                indexes[2][business_key(client.business_name)] = client.id

            audit(
                "importar_registro_general",
                "Client",
                client.id,
                after={
                    "batch_id": batch.id,
                    "record_type": record_type,
                    "owner_id": owner_id,
                    "source": source,
                    "pipeline_stage": stage,
                },
            )

        result = {
            "created": created,
            "duplicates_skipped": skipped_duplicates,
            "rejected": len(rejected),
            "rejected_rows": rejected,
            "created_ids": created_ids,
            "bulk": {
                "record_type": record_type_override,
                "owner_id": owner_override,
                "source": source_override or None,
                "pipeline_stage": stage_override or None,
            },
        }
        batch.status = "completed"
        batch.executed_at = datetime.utcnow()
        batch.result_json = result

        audit(
            "importar_lote_general",
            "ClientImportBatch",
            batch.id,
            after={
                "created": created,
                "duplicates_skipped": skipped_duplicates,
                "rejected": len(rejected),
            },
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Falló importación general lote %s", batch_id)
        failed = db.session.get(ClientImportBatch, batch_id)
        if failed:
            failed.status = "failed"
            failed.result_json = {
                "error": "La importación fue revertida; no quedaron cambios parciales."
            }
            db.session.commit()
        flash("La importación falló y fue revertida completamente.", "danger")
        return redirect(url_for("data_hub.import_preview", batch_id=batch_id))

    flash(
        (
            f"Importación completada: {created} creado(s), "
            f"{skipped_duplicates} duplicado(s) omitido(s) y "
            f"{len(rejected)} fila(s) reportada(s)."
        ),
        "success",
    )
    return redirect(url_for("data_hub.import_preview", batch_id=batch.id))


@bp.route("/import/<int:batch_id>/rejections.csv")
@login_required
def import_rejections(batch_id):
    batch = db.get_or_404(ClientImportBatch, batch_id)
    if not _batch_allowed(batch):
        abort(403)

    payload = batch.payload_json or {}
    if payload.get("kind") != "general_contacts_v12":
        abort(404)

    rows = []
    result = batch.result_json or {}
    if result.get("rejected_rows"):
        source_rows = result["rejected_rows"]
    else:
        source_rows = [
            {
                "line": row.get("line"),
                "business_name": row.get("business_name"),
                "reason": row.get("reason"),
            }
            for row in payload.get("rows", [])
            if row.get("status") != "ready"
        ]

    for row in source_rows:
        rows.append([
            row.get("line"),
            row.get("business_name"),
            row.get("reason"),
        ])

    audit(
        "exportar_rechazos_importacion",
        "ClientImportBatch",
        batch.id,
        after={"rows": len(rows)},
    )
    db.session.commit()
    return _csv_response(
        f"rechazos_lote_{batch.id}.csv",
        ["linea", "negocio", "motivo"],
        rows,
    )


@bp.route("/export/clients.csv")
@login_required
def export_clients():
    if not current_user.has_permission("clients.export"):
        abort(403)
    if _role_name() not in BUILTIN_ROLES and not _custom_role_company_scope("clients.export"):
        abort(403)

    query = _visible_clients_query(Client.query, "clients.export").filter(
        Client.record_type == "cliente"
    )

    search = (request.args.get("q") or "").strip()
    status = (request.args.get("status") or "").strip()
    advisor_project_id = request.args.get("advisor_project_id", type=int)

    if search:
        like = f"%{search}%"
        query = query.filter(or_(
            Client.business_name.ilike(like),
            Client.contact_name.ilike(like),
            Client.phone.ilike(like),
            Client.email.ilike(like),
            Client.code.ilike(like),
        ))
    if status:
        query = query.filter(Client.client_status == status)
    else:
        query = query.filter(Client.client_status != "archivado")
    if advisor_project_id:
        query = query.join(
            Collaborator,
            Client.owner_id == Collaborator.id,
        ).filter(Collaborator.advisor_project_id == advisor_project_id)

    clients = query.order_by(Client.updated_at.desc()).all()
    rows = []
    for client in clients:
        rows.append([
            client.code,
            client.business_name,
            client.contact_name,
            client.phone,
            client.email,
            client.client_status,
            client.source,
            client.priority,
            client.owner.user.name if client.owner and client.owner.user else "",
            client.owner.advisor_project.name if client.owner and client.owner.advisor_project else "",
            client.city,
            client.state,
        ])

    audit(
        "exportar_clientes_filtrados",
        "Client",
        after={
            "rows": len(rows),
            "q": search,
            "status": status,
            "advisor_project_id": advisor_project_id,
        },
    )
    db.session.commit()

    return _csv_response(
        "clientes_filtrados.csv",
        [
            "code", "business_name", "contact_name", "phone", "email",
            "client_status", "source", "priority", "advisor",
            "advisor_project", "city", "state",
        ],
        rows,
    )


@bp.route("/export/prospects.csv")
@login_required
def export_prospects():
    if not current_user.has_permission("crm.export"):
        abort(403)
    if _role_name() not in BUILTIN_ROLES and not _custom_role_company_scope("crm.export"):
        abort(403)

    query = _visible_clients_query(Client.query, "crm.export").filter(
        Client.record_type == "seguimiento"
    )

    search = (request.args.get("q") or "").strip()
    stage = (request.args.get("stage") or "").strip()
    source = (request.args.get("source") or "").strip()
    owner_id = request.args.get("owner_id", type=int)
    advisor_project_id = request.args.get("advisor_project_id", type=int)

    if search:
        like = f"%{search}%"
        query = query.filter(or_(
            Client.business_name.ilike(like),
            Client.contact_name.ilike(like),
            Client.phone.ilike(like),
            Client.email.ilike(like),
            Client.code.ilike(like),
        ))
    if stage:
        query = query.filter(Client.pipeline_stage == stage)
    if source:
        query = query.filter(Client.source == source)
    if owner_id:
        allowed_ids = {row.id for row in _assignable_collaborators()}
        if owner_id not in allowed_ids:
            abort(403)
        query = query.filter(Client.owner_id == owner_id)
    if advisor_project_id:
        query = query.join(
            Collaborator,
            Client.owner_id == Collaborator.id,
        ).filter(Collaborator.advisor_project_id == advisor_project_id)

    prospects = query.order_by(Client.updated_at.desc()).all()
    rows = []
    for client in prospects:
        rows.append([
            client.code,
            client.business_name,
            client.contact_name,
            client.phone,
            client.email,
            client.pipeline_stage,
            client.source,
            client.priority,
            client.owner.user.name if client.owner and client.owner.user else "",
            client.main_interest,
            client.estimated_budget,
        ])

    audit(
        "exportar_crm_filtrado",
        "Client",
        after={
            "rows": len(rows),
            "q": search,
            "stage": stage,
            "source": source,
            "owner_id": owner_id,
            "advisor_project_id": advisor_project_id,
        },
    )
    db.session.commit()

    return _csv_response(
        "seguimientos_filtrados.csv",
        [
            "code", "business_name", "contact_name", "phone", "email",
            "pipeline_stage", "source", "priority", "advisor",
            "main_interest", "estimated_budget",
        ],
        rows,
    )


@bp.route("/backups/create", methods=["POST"])
@login_required
@roles_required("superadmin", "admin", "manager")
def backup_create():
    try:
        from scripts.backup import create_backup
        path = create_backup()
    except Exception as exc:
        current_app.logger.exception("No se pudo crear backup manual")
        flash(f"No se pudo crear el respaldo: {exc}", "danger")
        return redirect(url_for("data_hub.index", _anchor="backups"))

    audit(
        "crear_backup_manual",
        "Backup",
        after={"file_name": path.name},
    )
    db.session.commit()
    flash(f"Respaldo creado: {path.name}", "success")
    return redirect(url_for("data_hub.index", _anchor="backups"))


@bp.route("/backups/<path:file_name>")
@login_required
@roles_required("superadmin", "admin", "manager")
def backup_download(file_name):
    safe_name = Path(file_name).name
    if safe_name != file_name or not safe_name.startswith("impacto_nexora_") or not safe_name.endswith(".zip"):
        abort(404)

    path = _backup_dir() / safe_name
    if not path.is_file():
        abort(404)

    audit(
        "descargar_backup",
        "Backup",
        after={"file_name": safe_name},
    )
    db.session.commit()
    return send_file(path, as_attachment=True, download_name=safe_name)
