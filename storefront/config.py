import os


def _database_url(name: str = "DATABASE_URL") -> str:
    url = os.environ.get(name, "").strip()
    if not url:
        os.makedirs("instance", exist_ok=True)
        return "sqlite:///" + os.path.abspath(os.path.join("instance", "storefront.db"))
    # Hosts hand out "postgres://" or driver-less "postgresql://" URLs; pin the psycopg 3 driver.
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def _flag(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).lower() in ("1", "true", "yes", "on")


class Config:
    PRODUCTION = os.environ.get("STOREFRONT_ENV", "development") == "production"
    SECRET_KEY = os.environ.get("SECRET_KEY") or ("" if PRODUCTION else "dev-only-insecure-key")
    SQLALCHEMY_DATABASE_URI = _database_url()
    # Neon's pooled URL (PgBouncer) serves the app; Alembic migrations use the direct URL when set.
    MIGRATIONS_DATABASE_URI = (
        _database_url("DATABASE_URL_UNPOOLED") if os.environ.get("DATABASE_URL_UNPOOLED") else None
    )
    # Free Postgres tiers (Neon) suspend idle connections, so check them before use.
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True, "pool_recycle": 280}

    # Session / cookie hardening (OWASP A02, A07).
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _flag("SESSION_COOKIE_SECURE", PRODUCTION)
    REMEMBER_COOKIE_SECURE = SESSION_COOKIE_SECURE
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = "Lax"
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 8  # admin sessions expire after 8 hours
    WTF_CSRF_TIME_LIMIT = 60 * 60 * 8

    # Upload limits (OWASP A04): images are re-encoded, videos capped.
    MAX_CONTENT_LENGTH = int(os.environ.get("MAX_UPLOAD_MB", "16")) * 1024 * 1024
    MAX_VIDEO_BYTES = 15 * 1024 * 1024

    # Optional SMTP for emailing enquiries to the store owner.
    SMTP_HOST = os.environ.get("SMTP_HOST", "").strip()
    SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
    SMTP_USER = os.environ.get("SMTP_USER", "").strip()
    SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
    SMTP_FROM = os.environ.get("SMTP_FROM", "").strip()

    # Superadmin ("support") login: the same three values go into every client store, so one login
    # (protected by an authenticator app) works everywhere. Generate them with `flask superadmin-setup`.
    SUPERADMIN_USERNAME = os.environ.get("SUPERADMIN_USERNAME", "").strip()
    SUPERADMIN_PASSWORD_HASH = os.environ.get("SUPERADMIN_PASSWORD_HASH", "").strip()
    SUPERADMIN_TOTP_SECRET = os.environ.get("SUPERADMIN_TOTP_SECRET", "").strip()

    # Paystack (card + Mobile Money). The secret key never leaves the server.
    PAYSTACK_SECRET_KEY = os.environ.get("PAYSTACK_SECRET_KEY", "").strip()
    PAYSTACK_API = "https://api.paystack.co"

    # Comma-separated list of hostnames the site answers on (blocks Host-header attacks). Empty = any.
    ALLOWED_HOSTS = [h.strip().lower() for h in os.environ.get("ALLOWED_HOSTS", "").split(",") if h.strip()]
