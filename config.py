import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

_VOLUME_MOUNT = os.getenv("RAILWAY_VOLUME_MOUNT_PATH")
_DEFAULT_UPLOAD_FOLDER = (
    str(Path(_VOLUME_MOUNT) / "uploads")
    if _VOLUME_MOUNT
    else str(BASE_DIR / "app" / "static" / "uploads")
)
_DEFAULT_BACKUP_DIR = (
    str(Path(_VOLUME_MOUNT) / "backups")
    if _VOLUME_MOUNT
    else str(BASE_DIR / "backups")
)


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "change-this-in-production")
    SQLALCHEMY_DATABASE_URI = os.getenv(
        "DATABASE_URL",
        f"sqlite:///{BASE_DIR / 'impacto_manager.db'}",
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_recycle": int(os.getenv("DB_POOL_RECYCLE_SECONDS", "1800")),
    }

    MAX_CONTENT_LENGTH = int(os.getenv("MAX_UPLOAD_MB", "20")) * 1024 * 1024
    MAX_FILE_UPLOAD_MB = int(os.getenv("MAX_FILE_UPLOAD_MB", "20"))
    UPLOAD_FOLDER = os.getenv("UPLOAD_FOLDER", _DEFAULT_UPLOAD_FOLDER)
    BACKUP_DIR = os.getenv("BACKUP_DIR", _DEFAULT_BACKUP_DIR)

    LIST_PAGE_SIZE = int(os.getenv("LIST_PAGE_SIZE", "25"))
    LIST_MAX_PAGE_SIZE = int(os.getenv("LIST_MAX_PAGE_SIZE", "100"))

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "1") == "1"
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_SECURE = os.getenv("REMEMBER_COOKIE_SECURE", "1") == "1"

    WTF_CSRF_TIME_LIMIT = None
    COMPANY_NAME = os.getenv("COMPANY_NAME", "Impacto Media Agency")
    COMPANY_TIMEZONE = os.getenv("COMPANY_TIMEZONE", "America/Managua")

    SMTP_HOST = os.getenv("SMTP_HOST")
    SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USER = os.getenv("SMTP_USER")
    SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
    SMTP_FROM = os.getenv(
        "SMTP_FROM",
        os.getenv("SMTP_USER", "no-reply@impactomedia.local"),
    )
    SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "1") == "1"
