"""Cross-cutting security controls, mapped to the OWASP Top 10 (2021) in SECURITY.md."""

import logging
import secrets
from datetime import timedelta

from flask import abort, g, request
from flask_login import current_user

from .extensions import db
from .models import AuditLog, RateEvent, utcnow

log = logging.getLogger("storefront.security")


def client_ip() -> str:
    # ProxyFix(x_for=1) has already replaced remote_addr with the address Render's proxy saw.
    return (request.remote_addr or "")[:64]


def rate_limited(bucket: str, limit: int, window_seconds: int) -> bool:
    """Record one attempt in `bucket`; True if the limit for the window is now exceeded.

    Stored in the database so the limit holds across every gunicorn worker.
    """
    now = utcnow()
    since = now - timedelta(seconds=window_seconds)
    count = db.session.query(RateEvent).filter(RateEvent.bucket == bucket, RateEvent.at >= since).count()
    if count >= limit:
        return True
    db.session.add(RateEvent(bucket=bucket, at=now))
    # Housekeeping: drop events older than a day.
    db.session.query(RateEvent).filter(RateEvent.at < now - timedelta(days=1)).delete()
    db.session.commit()
    return False


def audit(action: str, detail: str = "", username: str | None = None) -> None:
    if username is None:
        username = current_user.username if current_user and current_user.is_authenticated else ""
    entry = AuditLog(username=username[:80], action=action[:60], detail=detail[:500], ip=client_ip())
    db.session.add(entry)
    db.session.commit()
    log.info("audit action=%s user=%s ip=%s detail=%s", action, username, entry.ip, detail)


def init_security(app) -> None:
    @app.before_request
    def _nonce_and_host():
        g.csp_nonce = secrets.token_urlsafe(16)
        allowed = app.config.get("ALLOWED_HOSTS") or []
        if allowed and (request.host.split(":")[0].lower() not in allowed):
            abort(400)

    @app.after_request
    def _headers(resp):
        nonce = getattr(g, "csp_nonce", "")
        csp = (
            "default-src 'self'; "
            f"script-src 'self' 'nonce-{nonce}'; "
            f"style-src 'self' 'nonce-{nonce}' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data:; "
            "media-src 'self'; "
            "connect-src 'self'; "
            "object-src 'none'; "
            "base-uri 'self'; "
            "form-action 'self'; "
            "frame-ancestors 'none'"
        )
        if app.config.get("PRODUCTION"):
            csp += "; upgrade-insecure-requests"
            resp.headers.setdefault("Strict-Transport-Security", "max-age=63072000; includeSubDomains")
        resp.headers.setdefault("Content-Security-Policy", csp)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
        resp.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        resp.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        # Never cache admin pages (they can contain customer data).
        if request.path.startswith("/admin"):
            resp.headers["Cache-Control"] = "no-store"
        resp.headers.pop("Server", None)
        return resp

    @app.context_processor
    def _inject_nonce():
        return {"csp_nonce": getattr(g, "csp_nonce", "")}

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
