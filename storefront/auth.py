from datetime import timedelta
from urllib.parse import urlsplit

from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user

from .extensions import db, login_manager
from .models import AdminUser, utcnow
from .security import audit, client_ip, rate_limited

bp = Blueprint("auth", __name__, url_prefix="/admin")

MAX_FAILURES = 5
LOCK_MINUTES = 15
COMMON_PASSWORDS = {
    "password", "password1", "password123", "12345678", "123456789", "1234567890", "qwerty123",
    "iloveyou", "admin123", "letmein123", "welcome123", "changeme", "change-me-please",
}


@login_manager.user_loader
def load_user(user_id: str):
    try:
        uid, token = user_id.split(":", 1)
        user = db.session.get(AdminUser, int(uid))
    except (ValueError, TypeError):
        return None
    # A changed password rotates the token, which signs out every other session.
    if user is None or user.session_token != token:
        return None
    return user


def password_problem(password: str, username: str = "") -> str | None:
    if len(password) < 10:
        return "Use at least 10 characters."
    if len(password) > 128:
        return "Use at most 128 characters."
    if password.lower() in COMMON_PASSWORDS or (username and username.lower() in password.lower()):
        return "That password is too easy to guess."
    return None


def _safe_next(target):
    if target and urlsplit(target).netloc == "" and target.startswith("/") and not target.startswith("//"):
        return target
    return url_for("admin.home")


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("admin.home"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()[:80]
        if rate_limited(f"login-ip:{client_ip()}", limit=20, window_seconds=15 * 60):
            audit("login_rate_limited", username=username)
            flash("Too many attempts. Wait 15 minutes and try again.", "danger")
            return render_template("admin/login.html"), 429
        user = AdminUser.query.filter_by(username=username).first()
        now = utcnow()
        if user and user.locked_until and user.locked_until > now:
            audit("login_locked", username=username)
            flash("Wrong username or password.", "danger")
            return render_template("admin/login.html"), 401
        if user and user.check_password(request.form.get("password", "")):
            user.failed_logins = 0
            user.locked_until = None
            user.last_login_at = now
            db.session.commit()
            session.clear()  # fresh session on login: no fixation
            login_user(user, remember=False)
            session.permanent = True
            audit("login_ok", username=user.username)
            return redirect(_safe_next(request.args.get("next")))
        if user:
            user.failed_logins += 1
            if user.failed_logins >= MAX_FAILURES:
                user.locked_until = now + timedelta(minutes=LOCK_MINUTES)
                user.failed_logins = 0
            db.session.commit()
        audit("login_failed", username=username)
        # Same message whether or not the user exists (no account enumeration).
        flash("Wrong username or password.", "danger")
        return render_template("admin/login.html"), 401
    return render_template("admin/login.html")


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    audit("logout")
    logout_user()
    session.clear()
    flash("Signed out.", "success")
    return redirect(url_for("auth.login"))


@bp.route("/account", methods=["GET", "POST"])
@login_required
def account():
    if request.method == "POST":
        if not current_user.check_password(request.form.get("current", "")):
            audit("password_change_failed")
            flash("Your current password is wrong.", "danger")
        elif request.form.get("new") != request.form.get("confirm"):
            flash("The new passwords don't match.", "danger")
        elif problem := password_problem(request.form.get("new", ""), current_user.username):
            flash(problem, "danger")
        else:
            user = current_user._get_current_object()
            user.set_password(request.form["new"])
            db.session.commit()
            login_user(user)  # re-issue this session with the new token
            audit("password_changed")
            flash("Password changed. Other devices have been signed out.", "success")
            return redirect(url_for("auth.account"))
    return render_template("admin/account.html")
