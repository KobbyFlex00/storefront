from datetime import timedelta

from flask import g

from storefront import create_app
from storefront.extensions import db
from storefront.models import AdminUser, AuditLog, utcnow

from .conftest import PASSWORD


def test_security_headers(client):
    resp = client.get("/")
    csp = resp.headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp and "frame-ancestors 'none'" in csp and "unsafe-inline" not in csp
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert "Server" not in resp.headers


def test_admin_requires_login(client):
    for path in ["/admin/", "/admin/products", "/admin/settings/brand", "/admin/enquiries",
                 "/admin/activity", "/admin/account", "/admin/faq", "/admin/categories"]:
        resp = client.get(path)
        assert resp.status_code == 302 and "/admin/login" in resp.headers["Location"], path
    assert client.post("/admin/products/1/delete").status_code == 302


def test_admin_pages_not_cached(admin):
    assert admin.get("/admin/").headers["Cache-Control"] == "no-store"


def test_wrong_password_is_logged_and_generic(client):
    resp = client.post("/admin/login", data={"username": "owner", "password": "nope"})
    assert resp.status_code == 401
    assert "Wrong username or password" in resp.get_data(as_text=True)
    resp2 = client.post("/admin/login", data={"username": "ghost", "password": "nope"})
    assert resp2.get_data(as_text=True).count("Wrong username or password") == 1
    assert AuditLog.query.filter_by(action="login_failed").count() == 2


def test_account_lockout(client):
    for _ in range(5):
        client.post("/admin/login", data={"username": "owner", "password": "bad"})
    resp = client.post("/admin/login", data={"username": "owner", "password": PASSWORD})
    assert resp.status_code == 401  # locked even with the right password
    user = AdminUser.query.one()
    user.locked_until = utcnow() - timedelta(seconds=1)
    db.session.commit()
    assert client.post("/admin/login", data={"username": "owner", "password": PASSWORD}).status_code == 302


def test_open_redirect_blocked(client):
    resp = client.post("/admin/login?next=//evil.example", data={"username": "owner", "password": PASSWORD})
    assert resp.headers["Location"].endswith("/admin/")


def test_password_change_signs_out_other_sessions(app, admin):
    other = app.test_client()
    other.post("/admin/login", data={"username": "owner", "password": PASSWORD})
    assert other.get("/admin/").status_code == 200
    new = "a-brand-new-phrase"
    resp = admin.post("/admin/account", data={"current": PASSWORD, "new": new, "confirm": new})
    assert resp.status_code == 302
    assert admin.get("/admin/").status_code == 200
    g.pop("_login_user", None)  # the fixture shares one app context; real requests each get a fresh `g`
    assert other.get("/admin/").status_code == 302


def test_weak_password_rejected(admin):
    admin.post("/admin/account", data={"current": PASSWORD, "new": "password1", "confirm": "password1"})
    assert AdminUser.query.one().check_password(PASSWORD)


def test_csrf_enforced(tmp_path):
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'c.db'}"})
    with app.app_context():
        db.create_all()
        resp = app.test_client().post("/enquiry", data={"name": "a", "phone": "1", "items": "x"})
        assert resp.status_code == 400


def test_production_requires_secret(monkeypatch):
    from storefront.config import Config
    monkeypatch.setattr(Config, "SECRET_KEY", "")
    try:
        create_app()
    except RuntimeError as e:
        assert "SECRET_KEY" in str(e)
    else:
        raise AssertionError("expected RuntimeError")


def test_allowed_hosts(app, client):
    app.config["ALLOWED_HOSTS"] = ["myshop.com"]
    assert client.get("/", headers={"Host": "evil.com"}).status_code == 400
