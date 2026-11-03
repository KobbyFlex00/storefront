import os

from dotenv import load_dotenv

# Load env files before .config is imported: Config reads os.environ at class definition.
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
load_dotenv(os.path.join(_ROOT, ".env.local"))
load_dotenv(os.path.join(_ROOT, ".env"))

from flask import Flask, render_template  # noqa: E402
from werkzeug.middleware.proxy_fix import ProxyFix  # noqa: E402

from .config import Config  # noqa: E402
from .extensions import csrf, db, login_manager, migrate  # noqa: E402


def create_app(config_overrides: dict | None = None) -> Flask:
    app = Flask(__name__, instance_path=os.path.abspath("instance"))
    app.config.from_object(Config)
    if config_overrides:
        app.config.update(config_overrides)
    if not app.config["SECRET_KEY"]:
        raise RuntimeError("SECRET_KEY must be set when STOREFRONT_ENV=production")
    if app.config.get("PRODUCTION") and app.debug:
        raise RuntimeError("Never run with debug mode in production")

    # Render terminates TLS at its proxy; trust exactly one hop of forwarded headers.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    db.init_app(app)
    migrate.init_app(app, db, directory=os.path.join(_ROOT, "migrations"))
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "Please sign in."
    login_manager.login_message_category = "warning"
    login_manager.session_protection = "strong"
    csrf.init_app(app)

    from . import models  # noqa: F401  (register models)
    from .admin.routes import bp as admin_bp
    from .auth import bp as auth_bp
    from .cli import register_cli
    from .media import bp as media_bp
    from .payments import bp as payments_bp
    from .public.routes import bp as public_bp
    from .security import init_security
    from .site import get_site

    init_security(app)
    app.register_blueprint(public_bp)
    app.register_blueprint(media_bp)
    app.register_blueprint(payments_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    register_cli(app)

    @app.context_processor
    def _site():
        from datetime import date

        from .payments import paystack_ready

        return {"site": get_site(), "now_year": date.today().year, "paystack_ready": paystack_ready}

    @app.template_filter("minor_input")
    def _minor_input(minor):
        if minor is None:
            return ""
        whole, cents = divmod(int(minor), 100)
        return f"{whole}.{cents:02d}" if cents else str(whole)

    # Friendly error pages that never leak stack traces (OWASP A05).
    for code, title in ((400, "Bad request"), (403, "Not allowed"), (404, "Page not found"),
                        (413, "File too large"), (429, "Slow down"), (500, "Something went wrong")):
        def handler(_e, code=code, title=title):
            if code == 500:
                db.session.rollback()
            return render_template("error.html", code=code, title=title), code
        app.register_error_handler(code, handler)

    from flask_wtf.csrf import CSRFError

    @app.errorhandler(CSRFError)
    def _csrf(_e):
        title = "Your session expired. Go back, refresh and try again."
        return render_template("error.html", code=400, title=title), 400

    return app
