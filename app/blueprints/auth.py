from datetime import datetime, timedelta
import hashlib
import secrets
from urllib.parse import urlsplit

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user

from app.extensions import db
from app.helpers import audit, send_email
from app.models import PasswordReset, User
from app.security_controls import (
    begin_two_factor_challenge,
    clear_two_factor_challenge,
    pending_two_factor_options,
    security_policy,
    set_two_factor,
    two_factor_enabled,
    verify_two_factor_code,
)

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


def _finish_login(user, *, remember=False, destination=None, action="login"):
    policy = security_policy()
    user.failed_attempts = 0
    user.locked_until = None
    user.last_login_at = datetime.utcnow()

    login_user(
        user,
        remember=bool(remember and policy["allow_remember_me"]),
    )
    session["security_last_activity"] = datetime.utcnow().timestamp()
    audit(action, "User", user.id)
    db.session.commit()

    return redirect(destination or url_for("dashboard.index"))


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    policy = security_policy()

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = User.query.filter(db.func.lower(User.email) == email).first()
        now = datetime.utcnow()

        if user and user.locked_until and user.locked_until > now:
            audit(
                "login_bloqueado",
                "User",
                user.id,
                after={"email": email},
                reason="Cuenta temporalmente bloqueada",
            )
            db.session.commit()
            flash("La cuenta está bloqueada temporalmente por intentos fallidos.", "danger")
            return render_template(
                "auth/login.html",
                allow_remember=policy["allow_remember_me"],
            )

        if not user or not user.active or not user.check_password(password):
            if user:
                user.failed_attempts += 1
                if user.failed_attempts >= policy["failed_attempt_limit"]:
                    user.locked_until = now + timedelta(minutes=policy["lock_minutes"])
                audit(
                    "login_fallido",
                    "User",
                    user.id,
                    after={
                        "email": email,
                        "failed_attempts": user.failed_attempts,
                        "locked_until": user.locked_until,
                    },
                    reason="Credenciales incorrectas o usuario inactivo",
                )
            else:
                audit(
                    "login_fallido",
                    "User",
                    None,
                    after={"email": email},
                    reason="Correo no registrado",
                )
            db.session.commit()
            flash("Correo o contraseña incorrectos.", "danger")
            return render_template(
                "auth/login.html",
                allow_remember=policy["allow_remember_me"],
            )

        remember = bool(request.form.get("remember"))
        destination = _safe_next_url(request.args.get("next")) or url_for("dashboard.index")

        user.failed_attempts = 0
        user.locked_until = None

        if two_factor_enabled(user):
            code = begin_two_factor_challenge(
                user,
                remember=remember,
                destination=destination,
            )
            try:
                delivered = send_email(
                    user.email,
                    "Código de seguridad - Impacto Nexora",
                    (
                        "Tu código de verificación es:\n\n"
                        f"{code}\n\n"
                        "Expira en 10 minutos. Si no intentaste iniciar sesión, "
                        "cambia tu contraseña y avisa a administración."
                    ),
                )
            except Exception:
                current_app.logger.exception(
                    "Falló el envío del código 2FA para usuario %s",
                    user.id,
                )
                delivered = False

            if not delivered:
                clear_two_factor_challenge()
                audit(
                    "2fa_envio_fallido",
                    "User",
                    user.id,
                    reason="SMTP no disponible o error de envío",
                )
                db.session.commit()
                flash(
                    "No fue posible enviar el código de seguridad. Contacta a administración.",
                    "danger",
                )
                return render_template(
                    "auth/login.html",
                    allow_remember=policy["allow_remember_me"],
                )

            audit("2fa_codigo_enviado", "User", user.id)
            db.session.commit()
            return redirect(url_for("auth.verify_2fa"))

        return _finish_login(
            user,
            remember=remember,
            destination=destination,
            action="login",
        )

    return render_template(
        "auth/login.html",
        allow_remember=policy["allow_remember_me"],
    )


@bp.route("/verify-2fa", methods=["GET", "POST"])
def verify_2fa():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    if not session.get("pending_2fa_user_id"):
        flash("No hay una verificación de seguridad pendiente.", "warning")
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        pending_user_id = session.get("pending_2fa_user_id")
        user, error = verify_two_factor_code(request.form.get("code"))
        if error:
            audit(
                "2fa_fallido",
                "User",
                pending_user_id,
                reason=error,
            )
            db.session.commit()
            flash(error, "danger")
            return render_template("auth/verify_2fa.html")

        options = pending_two_factor_options()
        clear_two_factor_challenge()
        return _finish_login(
            user,
            remember=options["remember"],
            destination=_safe_next_url(options["destination"]) or url_for("dashboard.index"),
            action="login_2fa",
        )

    return render_template("auth/verify_2fa.html")


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    audit("logout", "User", current_user.id)
    db.session.commit()
    logout_user()
    session.clear()
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
                    "Restablecer contraseña - Impacto Nexora",
                    f"Usa este enlace durante la próxima hora:\n\n{link}",
                )
                if not delivered:
                    current_app.logger.warning(
                        "No se envió el correo de recuperación para el usuario %s porque SMTP no está configurado.",
                        user.id,
                    )
            except Exception:
                current_app.logger.exception(
                    "Falló el envío del correo de recuperación para el usuario %s",
                    user.id,
                )

            if current_app.debug and not current_app.config.get("SMTP_HOST"):
                flash(f"Modo desarrollo: {link}", "info")

        flash(
            "Si el correo existe y está activo, recibirás instrucciones para restablecer la contraseña.",
            "success",
        )

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
            return render_template(
                "auth/profile.html",
                two_factor=two_factor_enabled(current_user),
                can_enable_2fa=bool(current_app.config.get("SMTP_HOST")),
            )

        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")
        current_password = request.form.get("current_password", "")

        if new_password:
            if not current_user.check_password(current_password):
                flash("La contraseña actual no es correcta.", "danger")
                return render_template(
                    "auth/profile.html",
                    two_factor=two_factor_enabled(current_user),
                    can_enable_2fa=bool(current_app.config.get("SMTP_HOST")),
                )
            if len(new_password) < 8:
                flash("La nueva contraseña debe tener al menos 8 caracteres.", "danger")
                return render_template(
                    "auth/profile.html",
                    two_factor=two_factor_enabled(current_user),
                    can_enable_2fa=bool(current_app.config.get("SMTP_HOST")),
                )
            if new_password != confirm_password:
                flash("La confirmación de la nueva contraseña no coincide.", "danger")
                return render_template(
                    "auth/profile.html",
                    two_factor=two_factor_enabled(current_user),
                    can_enable_2fa=bool(current_app.config.get("SMTP_HOST")),
                )

        current_user.name = name
        if new_password:
            current_user.set_password(new_password)
            PasswordReset.query.filter(
                PasswordReset.user_id == current_user.id,
                PasswordReset.used_at.is_(None),
            ).update(
                {PasswordReset.used_at: datetime.utcnow()},
                synchronize_session=False,
            )

        audit("editar_perfil", "User", current_user.id)
        db.session.commit()
        flash("Perfil actualizado.", "success")

    return render_template(
        "auth/profile.html",
        two_factor=two_factor_enabled(current_user),
        can_enable_2fa=bool(current_app.config.get("SMTP_HOST")),
    )


@bp.route("/profile/2fa", methods=["POST"])
@login_required
def profile_2fa():
    action = (request.form.get("action") or "").strip()
    current_password = request.form.get("current_password", "")

    if not current_user.check_password(current_password):
        flash("Confirma tu contraseña actual para cambiar el 2FA.", "danger")
        return redirect(url_for("auth.profile"))

    if action == "enable":
        if not security_policy()["allow_2fa"]:
            flash("La autenticación de dos factores está deshabilitada por política.", "warning")
            return redirect(url_for("auth.profile"))
        if not current_app.config.get("SMTP_HOST"):
            flash("Configura SMTP antes de activar la verificación por correo.", "danger")
            return redirect(url_for("auth.profile"))
        set_two_factor(current_user, True)
        audit("activar_2fa", "User", current_user.id)
        message = "Autenticación de dos factores activada."
    elif action == "disable":
        set_two_factor(current_user, False)
        audit("desactivar_2fa", "User", current_user.id)
        message = "Autenticación de dos factores desactivada."
    else:
        flash("Acción de seguridad no válida.", "danger")
        return redirect(url_for("auth.profile"))

    db.session.commit()
    flash(message, "success")
    return redirect(url_for("auth.profile"))
