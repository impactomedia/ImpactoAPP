from datetime import datetime, timedelta
import hashlib
import secrets
from urllib.parse import urlsplit

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from app.extensions import db
from app.helpers import audit, send_email
from app.models import PasswordReset, User

bp = Blueprint("auth", __name__, url_prefix="/auth")


def _safe_next_url(target):
    """Acepta únicamente rutas internas para evitar redirecciones abiertas."""
    if not target:
        return None
    target = target.strip()
    parts = urlsplit(target)
    if parts.scheme or parts.netloc or not target.startswith("/"):
        return None
    return target


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = User.query.filter(db.func.lower(User.email) == email).first()
        now = datetime.utcnow()

        if user and user.locked_until and user.locked_until > now:
            flash("La cuenta está bloqueada temporalmente por intentos fallidos.", "danger")
            return render_template("auth/login.html")

        if not user or not user.active or not user.check_password(password):
            if user:
                user.failed_attempts += 1
                if user.failed_attempts >= 5:
                    user.locked_until = now + timedelta(minutes=15)
                db.session.commit()
            flash("Correo o contraseña incorrectos.", "danger")
            return render_template("auth/login.html")

        user.failed_attempts = 0
        user.locked_until = None
        user.last_login_at = now
        login_user(user, remember=bool(request.form.get("remember")))
        audit("login", "User", user.id)
        db.session.commit()

        destination = _safe_next_url(request.args.get("next")) or url_for("dashboard.index")
        return redirect(destination)

    return render_template("auth/login.html")


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    audit("logout", "User", current_user.id)
    db.session.commit()
    logout_user()
    flash("Sesión cerrada.", "success")
    return redirect(url_for("auth.login"))


@bp.route("/forgot", methods=["GET", "POST"])
def forgot():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = User.query.filter(db.func.lower(User.email) == email).first()

        if user and user.active:
            token = secrets.token_urlsafe(32)
            token_hash = hashlib.sha256(token.encode()).hexdigest()
            reset = PasswordReset(
                user_id=user.id,
                token_hash=token_hash,
                expires_at=datetime.utcnow() + timedelta(hours=1),
            )
            db.session.add(reset)
            db.session.commit()

            link = url_for("auth.reset_password", token=token, _external=True)
            try:
                delivered = send_email(
                    user.email,
                    "Restablecer contraseña - Impacto Manager",
                    f"Usa este enlace durante la próxima hora:\n\n{link}",
                )
                if not delivered:
                    current_app.logger.warning(
                        "No se envió el correo de recuperación para el usuario %s porque SMTP no está configurado.",
                        user.id,
                    )
            except Exception:
                # El formulario nunca debe revelar si el correo existe ni romperse por un fallo SMTP.
                current_app.logger.exception("Falló el envío del correo de recuperación para el usuario %s", user.id)

            if current_app.debug and not current_app.config.get("SMTP_HOST"):
                flash(f"Modo desarrollo: {link}", "info")

        flash("Si el correo existe y está activo, recibirás instrucciones para restablecer la contraseña.", "success")

    return render_template("auth/forgot.html")


@bp.route("/reset/<token>", methods=["GET", "POST"])
def reset_password(token):
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    reset = PasswordReset.query.filter_by(token_hash=token_hash, used_at=None).first()
    now = datetime.utcnow()

    if not reset or reset.expires_at < now:
        flash("El enlace no es válido o ya expiró.", "danger")
        return redirect(url_for("auth.forgot"))

    user = db.session.get(User, reset.user_id)
    if not user or not user.active:
        flash("El enlace no es válido o ya expiró.", "danger")
        return redirect(url_for("auth.forgot"))

    if request.method == "POST":
        password = request.form.get("password", "")
        if len(password) < 8:
            flash("La contraseña debe tener al menos 8 caracteres.", "danger")
        else:
            user.set_password(password)
            used_at = datetime.utcnow()
            PasswordReset.query.filter(
                PasswordReset.user_id == user.id,
                PasswordReset.used_at.is_(None),
            ).update({PasswordReset.used_at: used_at}, synchronize_session=False)
            user.failed_attempts = 0
            user.locked_until = None
            audit("password_reset", "User", user.id)
            db.session.commit()
            flash("Contraseña actualizada. Ya puedes iniciar sesión.", "success")
            return redirect(url_for("auth.login"))

    return render_template("auth/reset.html")


@bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        if not name:
            flash("El nombre no puede quedar vacío.", "danger")
            return render_template("auth/profile.html")

        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")
        current_password = request.form.get("current_password", "")

        if new_password:
            if not current_user.check_password(current_password):
                flash("La contraseña actual no es correcta.", "danger")
                return render_template("auth/profile.html")
            if len(new_password) < 8:
                flash("La nueva contraseña debe tener al menos 8 caracteres.", "danger")
                return render_template("auth/profile.html")
            if new_password != confirm_password:
                flash("La confirmación de la nueva contraseña no coincide.", "danger")
                return render_template("auth/profile.html")

        current_user.name = name
        if new_password:
            current_user.set_password(new_password)
            # Invalida enlaces de recuperación todavía abiertos después de un cambio de contraseña autenticado.
            PasswordReset.query.filter(
                PasswordReset.user_id == current_user.id,
                PasswordReset.used_at.is_(None),
            ).update({PasswordReset.used_at: datetime.utcnow()}, synchronize_session=False)

        audit("editar_perfil", "User", current_user.id)
        db.session.commit()
        flash("Perfil actualizado.", "success")

    return render_template("auth/profile.html")
