from datetime import datetime, timedelta
import hashlib
import secrets

from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app
from flask_login import login_user, logout_user, login_required, current_user

from app.extensions import db
from app.models import User, PasswordReset
from app.helpers import audit, send_email

bp = Blueprint("auth", __name__, url_prefix="/auth")


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
        return redirect(request.args.get("next") or url_for("dashboard.index"))
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
        if user:
            token = secrets.token_urlsafe(32)
            token_hash = hashlib.sha256(token.encode()).hexdigest()
            reset = PasswordReset(user_id=user.id, token_hash=token_hash, expires_at=datetime.utcnow() + timedelta(hours=1))
            db.session.add(reset)
            db.session.commit()
            link = url_for("auth.reset_password", token=token, _external=True)
            send_email(user.email, "Restablecer contraseña - Impacto Manager", f"Usa este enlace durante la próxima hora:\n\n{link}")
            if current_app.debug and not current_app.config.get("SMTP_HOST"):
                flash(f"Modo desarrollo: {link}", "info")
        flash("Si el correo existe, recibirás instrucciones para restablecer la contraseña.", "success")
    return render_template("auth/forgot.html")


@bp.route("/reset/<token>", methods=["GET", "POST"])
def reset_password(token):
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    reset = PasswordReset.query.filter_by(token_hash=token_hash, used_at=None).first()
    if not reset or reset.expires_at < datetime.utcnow():
        flash("El enlace no es válido o ya expiró.", "danger")
        return redirect(url_for("auth.forgot"))
    if request.method == "POST":
        password = request.form.get("password", "")
        if len(password) < 8:
            flash("La contraseña debe tener al menos 8 caracteres.", "danger")
        else:
            user = db.session.get(User, reset.user_id)
            user.set_password(password)
            reset.used_at = datetime.utcnow()
            audit("password_reset", "User", user.id)
            db.session.commit()
            flash("Contraseña actualizada. Ya puedes iniciar sesión.", "success")
            return redirect(url_for("auth.login"))
    return render_template("auth/reset.html")


@bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    if request.method == "POST":
        current_user.name = request.form.get("name", current_user.name).strip()
        new_password = request.form.get("new_password", "")
        if new_password:
            if len(new_password) < 8:
                flash("La nueva contraseña debe tener al menos 8 caracteres.", "danger")
                return render_template("auth/profile.html")
            current_user.set_password(new_password)
        audit("editar_perfil", "User", current_user.id)
        db.session.commit()
        flash("Perfil actualizado.", "success")
    return render_template("auth/profile.html")
