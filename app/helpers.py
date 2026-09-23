import csv
import json
import os
import secrets
import smtplib
from email.message import EmailMessage
from pathlib import Path
from werkzeug.utils import secure_filename
from flask import current_app, request, url_for
from flask_login import current_user

from app.extensions import db
from app.models import AuditLog, Notification


def money(value):
    try:
        return f"{float(value or 0):,.2f}"
    except Exception:
        return "0.00"


def audit(action, entity, entity_id=None, before=None, after=None, reason=None):
    try:
        user_id = current_user.id if current_user.is_authenticated else None
    except Exception:
        user_id = None
    log = AuditLog(
        user_id=user_id,
        action=action,
        entity=entity,
        entity_id=str(entity_id) if entity_id is not None else None,
        before_json=json.dumps(before, default=str, ensure_ascii=False) if before is not None else None,
        after_json=json.dumps(after, default=str, ensure_ascii=False) if after is not None else None,
        reason=reason,
        ip_address=request.headers.get("X-Forwarded-For", request.remote_addr) if request else None,
    )
    db.session.add(log)


def notify(user_id, title, message, link=None, priority="normal"):
    db.session.add(Notification(user_id=user_id, title=title, message=message, link=link, priority=priority))


def save_upload(file_storage, prefix="file"):
    if not file_storage or not file_storage.filename:
        return None
    upload_dir = Path(current_app.config["UPLOAD_FOLDER"])
    upload_dir.mkdir(parents=True, exist_ok=True)
    original = secure_filename(file_storage.filename)
    suffix = Path(original).suffix.lower()
    allowed = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".doc", ".docx", ".xls", ".xlsx", ".csv", ".txt"}
    if suffix not in allowed:
        raise ValueError("Tipo de archivo no permitido")
    filename = f"{prefix}_{secrets.token_hex(8)}{suffix}"
    file_storage.save(upload_dir / filename)
    return f"uploads/{filename}"


def send_email(to_email, subject, body):
    host = current_app.config.get("SMTP_HOST")
    if not host:
        current_app.logger.warning("SMTP no configurado. Correo para %s: %s", to_email, body)
        return False
    msg = EmailMessage()
    msg["From"] = current_app.config.get("SMTP_FROM")
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body)
    port = current_app.config.get("SMTP_PORT", 587)
    with smtplib.SMTP(host, port) as server:
        if current_app.config.get("SMTP_USE_TLS"):
            server.starttls()
        user = current_app.config.get("SMTP_USER")
        password = current_app.config.get("SMTP_PASSWORD")
        if user and password:
            server.login(user, password)
        server.send_message(msg)
    return True


def next_code(prefix, model, field="id"):
    last_id = db.session.query(db.func.max(getattr(model, field))).scalar() or 0
    return f"{prefix}-{int(last_id)+1:05d}"
