"""Identidad visual del cliente: logotipo principal seguro y con historial.

Se reutilizan Attachment y ClientDocumentMeta para no alterar MySQL.
"""
from pathlib import Path

from flask import (
    Blueprint, abort, current_app, flash, redirect, request,
    send_from_directory, url_for,
)
from flask_login import current_user, login_required

from app.access_control import _commercial_client_allowed
from app.client_v2_models import ClientDocumentMeta
from app.client_v3_services import client_coordination_allowed
from app.decorators import permission_required
from app.extensions import db
from app.helpers import audit, save_upload
from app.models import Attachment, Client
from app.security_controls import BUILTIN_ROLES, permission_scope

bp = Blueprint("client_identity", __name__, url_prefix="/clients")
LOGO_CATEGORY = "logo_principal"
ALLOWED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
IMAGE_MIMES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
LOGO_MAX_BYTES = 5 * 1024 * 1024


def primary_client_logo(client_id):
    """Devuelve el único logotipo principal del cliente si lo hay."""
    return (
        Attachment.query.join(ClientDocumentMeta, ClientDocumentMeta.attachment_id == Attachment.id)
        .filter(
            Attachment.entity_type == "Client",
            Attachment.entity_id == client_id,
            ClientDocumentMeta.client_id == client_id,
            ClientDocumentMeta.category == LOGO_CATEGORY,
        )
        .order_by(Attachment.created_at.desc(), Attachment.id.desc())
        .first()
    )


def _can_access(client, *, modify=False):
    required = "clients.edit" if modify else "clients.view"
    if not current_user.has_permission(required):
        return False
    if client.record_type != "cliente":
        return False
    role = current_user.role.name if current_user.role else ""
    if not client_coordination_allowed(client):
        return False
    if role in {"advisor", "supervisor"}:
        # Una ficha comercial no se modifica por una asignación operativa.
        return _commercial_client_allowed(client) if modify else True
    if role not in BUILTIN_ROLES:
        # Respetar también los permisos de alcance de los roles personalizados.
        scope = permission_scope(current_user, required)
        if scope == "company":
            return True
        collaborator = current_user.collaborator
        return bool(collaborator and client.owner_id == collaborator.id and scope in {"own", "assigned"})
    return True


def _client_or_403(client_id, *, modify=False):
    client = db.get_or_404(Client, client_id)
    if not _can_access(client, modify=modify):
        abort(403)
    return client


def _file_size(upload):
    stream = upload.stream
    try:
        pos = stream.tell()
        stream.seek(0, 2)
        size = stream.tell()
        stream.seek(pos)
        return size
    except (OSError, ValueError):
        return upload.content_length or 0


@bp.route("/<int:client_id>/brand-logo", methods=["POST"])
@login_required
@permission_required("clients.edit")
def upload_brand_logo(client_id):
    client = _client_or_403(client_id, modify=True)
    uploaded = request.files.get("logo")
    if not uploaded or not uploaded.filename:
        flash("Selecciona la imagen del logotipo.", "warning")
        return redirect(url_for("clients.detail", client_id=client.id))

    suffix = Path(uploaded.filename).suffix.lower()
    if suffix not in ALLOWED_IMAGE_EXTENSIONS:
        flash("El logotipo debe ser una imagen PNG, JPG o WEBP.", "danger")
        return redirect(url_for("clients.detail", client_id=client.id))
    size = _file_size(uploaded)
    if size <= 0 or size > LOGO_MAX_BYTES:
        flash("El logotipo debe pesar entre 1 byte y 5 MB.", "danger")
        return redirect(url_for("clients.detail", client_id=client.id))

    try:
        path = save_upload(uploaded, prefix=f"client_{client.id}_logo")
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("clients.detail", client_id=client.id))

    try:
        old = primary_client_logo(client.id)
        if old:
            meta = ClientDocumentMeta.query.filter_by(attachment_id=old.id).first()
            meta.category = "branding"
            meta.description = "Logotipo anterior (historial de marca)"

        attachment = Attachment(
            entity_type="Client",
            entity_id=client.id,
            file_name=uploaded.filename[:255],
            file_path=path,
            uploaded_by_id=current_user.id,
        )
        db.session.add(attachment)
        db.session.flush()
        db.session.add(ClientDocumentMeta(
            client_id=client.id,
            attachment_id=attachment.id,
            category=LOGO_CATEGORY,
            description="Logotipo principal de la empresa",
        ))
        audit(
            "actualizar_logo_cliente", "Client", client.id,
            before={"attachment_id": old.id if old else None},
            after={"attachment_id": attachment.id},
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
        (Path(current_app.config["UPLOAD_FOLDER"]) / Path(path).name).unlink(missing_ok=True)
        raise

    flash("Logotipo guardado correctamente.", "success")
    return redirect(url_for("clients.detail", client_id=client.id))


@bp.route("/<int:client_id>/brand-logo/remove", methods=["POST"])
@login_required
@permission_required("clients.edit")
def remove_brand_logo(client_id):
    client = _client_or_403(client_id, modify=True)
    old = primary_client_logo(client.id)
    if old:
        meta = ClientDocumentMeta.query.filter_by(attachment_id=old.id).first()
        meta.category = "branding"
        meta.description = "Logotipo retirado (historial de marca)"
        audit("retirar_logo_cliente", "Client", client.id, before={"attachment_id": old.id})
        db.session.commit()
    flash("Logotipo principal retirado. El archivo se conserva en el historial.", "info")
    return redirect(url_for("clients.detail", client_id=client.id))


@bp.route("/<int:client_id>/brand-logo/<int:attachment_id>", methods=["GET"])
@login_required
@permission_required("clients.view")
def view_brand_logo(client_id, attachment_id):
    client = _client_or_403(client_id)
    logo = primary_client_logo(client.id)
    if not logo or logo.id != attachment_id:
        abort(404)
    filename = Path(logo.file_path or "").name
    suffix = Path(filename).suffix.lower()
    if not filename or suffix not in IMAGE_MIMES:
        abort(404)
    response = send_from_directory(
        current_app.config["UPLOAD_FOLDER"], filename,
        mimetype=IMAGE_MIMES[suffix], as_attachment=False,
    )
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response
