import secrets
import time
from datetime import timedelta
from urllib.parse import urlsplit

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user

from . import totp
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
            if user.is_super:
                secret = current_app.config["SUPERADMIN_TOTP_SECRET"]
                if not secret and current_app.config["PRODUCTION"]:
                    audit("login_refused_no_2fa", username=username)
                    flash("Support login needs SUPERADMIN_TOTP_SECRET set on this server.", "danger")
                    return render_template("admin/login.html"), 403
                if secret:
                    next_url = _safe_next(request.args.get("next"))
                    session.clear()
                    session["2fa_uid"] = user.id
                    session["2fa_at"] = time.time()
                    session["2fa_next"] = next_url
                    return redirect(url_for("auth.two_factor"))
            return _finish_login(user)
        _record_failure(user, username)
        # Same message whether or not the user exists (no account enumeration).
        flash("Wrong username or password.", "danger")
        return render_template("admin/login.html"), 401
    return render_template("admin/login.html")


def _record_failure(user, username: str, action: str = "login_failed") -> None:
    if user:
        user.failed_logins += 1
        if user.failed_logins >= MAX_FAILURES:
            user.locked_until = utcnow() + timedelta(minutes=LOCK_MINUTES)
            user.failed_logins = 0
        db.session.commit()
    audit(action, username=username)


def _finish_login(user, next_url: str | None = None):
    user.failed_logins = 0
    user.locked_until = None
    user.last_login_at = utcnow()
    db.session.commit()
    session.clear()  # fresh session on login: no fixation
    login_user(user, remember=False)
    session.permanent = True
    audit("login_ok" if not user.is_super else "support_login_ok", username=user.username)
    return redirect(next_url or _safe_next(request.args.get("next")))


@bp.route("/login/2fa", methods=["GET", "POST"])
def two_factor():
    uid, started = session.get("2fa_uid"), session.get("2fa_at", 0)
    if not uid or time.time() - started > 5 * 60:
        session.clear()
        flash("Please sign in again.", "warning")
        return redirect(url_for("auth.login"))
    user = db.session.get(AdminUser, uid)
    if user is None or not user.is_super:
        session.clear()
        return redirect(url_for("auth.login"))
    if request.method == "POST":
        if rate_limited(f"2fa:{uid}", limit=10, window_seconds=15 * 60) or (
            user.locked_until and user.locked_until > utcnow()
        ):
            audit("2fa_rate_limited", username=user.username)
            session.clear()
            flash("Too many attempts. Wait 15 minutes and try again.", "danger")
            return redirect(url_for("auth.login"))
        step = totp.verify(current_app.config["SUPERADMIN_TOTP_SECRET"], request.form.get("code", ""),
                           last_step=user.totp_last_step)
        if step is None:
            _record_failure(user, user.username, "2fa_failed")
            flash("That code didn't work. Use the newest code from your authenticator app.", "danger")
            return render_template("admin/two_factor.html"), 401
        user.totp_last_step = step
        return _finish_login(user, session.get("2fa_next"))
    return render_template("admin/two_factor.html")


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
    if request.method == "POST" and current_user.is_super:
        flash("The support password is set with SUPERADMIN_PASSWORD_HASH on the server.", "warning")
        return redirect(url_for("auth.account"))
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
    support = AdminUser.query.filter_by(role="super").all()
    return render_template("admin/account.html", support=support)


def sync_superadmin() -> str:
    """Create/update/remove the support account from SUPERADMIN_* env vars. Returns a status line."""
    cfg = current_app.config
    username, pw_hash = cfg["SUPERADMIN_USERNAME"], cfg["SUPERADMIN_PASSWORD_HASH"]
    existing = AdminUser.query.filter_by(role="super").all()
    if not (username and pw_hash):
        for u in existing:
            db.session.delete(u)
        db.session.commit()
        return f"Support access is off (removed {len(existing)} support account(s))."
    if not pw_hash.startswith(("scrypt:", "pbkdf2:")):
        raise ValueError("SUPERADMIN_PASSWORD_HASH must be a hash from `flask superadmin-setup`, not a password.")
    clash = AdminUser.query.filter_by(username=username, role="owner").first()
    if clash:
        raise ValueError(f"An owner account is already called '{username}'; pick another SUPERADMIN_USERNAME.")
    for u in existing:
        if u.username != username:
            db.session.delete(u)
    user = next((u for u in existing if u.username == username), None)
    if user is None:
        user = AdminUser(username=username, role="super", password_hash=pw_hash)
        db.session.add(user)
    elif user.password_hash != pw_hash:
        user.password_hash = pw_hash
        user.session_token = secrets.token_hex(16)  # signs out old support sessions
    db.session.commit()
    return f"Support account '{username}' is ready" + (" (2FA on)." if cfg["SUPERADMIN_TOTP_SECRET"] else
                                                        " (WARNING: 2FA off; set SUPERADMIN_TOTP_SECRET).")
