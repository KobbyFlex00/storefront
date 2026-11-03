import time

import pytest
from flask import g
from werkzeug.security import generate_password_hash

from storefront import totp
from storefront.auth import sync_superadmin
from storefront.extensions import db
from storefront.models import AdminUser, AuditLog

from .conftest import PASSWORD

SUPER_PW = "support-passphrase-123"
SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # RFC 6238 test key "12345678901234567890"


def test_totp_rfc6238_vector():
    assert totp.code_at(SECRET, 59 // 30) == "287082"
    assert totp.verify(SECRET, "287082", now=59) == 1
    assert totp.verify(SECRET, "287082", last_step=1, now=59) is None  # replay
    assert totp.verify(SECRET, "000000", now=59) is None
    assert totp.verify(SECRET, "abc", now=59) is None


@pytest.fixture
def support(app):
    app.config.update(SUPERADMIN_USERNAME="support", SUPERADMIN_PASSWORD_HASH=generate_password_hash(SUPER_PW),
                      SUPERADMIN_TOTP_SECRET=SECRET)
    sync_superadmin()
    return app


def _login_super(client, code=None):
    resp = client.post("/admin/login", data={"username": "support", "password": SUPER_PW})
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/admin/login/2fa")
    return client.post("/admin/login/2fa", data={"code": code or totp.code_at(SECRET, int(time.time() // 30))})


def test_super_needs_2fa(support, client):
    client.post("/admin/login", data={"username": "support", "password": SUPER_PW})
    assert client.get("/admin/").status_code == 302  # password alone is not enough
    assert client.post("/admin/login/2fa", data={"code": "123456"}).status_code == 401
    resp = client.post("/admin/login/2fa", data={"code": totp.code_at(SECRET, int(time.time() // 30))})
    assert resp.status_code == 302
    assert client.get("/admin/support").status_code == 200
    assert AuditLog.query.filter_by(action="support_login_ok").count() == 1


def test_2fa_code_cannot_be_replayed(support, app):
    code = totp.code_at(SECRET, int(time.time() // 30))
    assert _login_super(app.test_client(), code).status_code == 302
    g.pop("_login_user", None)
    assert _login_super(app.test_client(), code).status_code == 401


def test_2fa_step_requires_password_first(support, client):
    resp = client.post("/admin/login/2fa", data={"code": totp.code_at(SECRET, int(time.time() // 30))})
    assert resp.status_code == 302 and "/admin/login" in resp.headers["Location"]
    assert client.get("/admin/").status_code == 302


def test_production_refuses_super_without_2fa(support, client):
    support.config.update(SUPERADMIN_TOTP_SECRET="", PRODUCTION=True)
    resp = client.post("/admin/login", data={"username": "support", "password": SUPER_PW})
    assert resp.status_code == 403


def test_owner_cannot_open_support(admin):
    assert admin.get("/admin/support").status_code == 403
    assert admin.post("/admin/support", data={"action": "add", "username": "x"}).status_code == 403


def test_super_resets_owner_and_signs_them_out(support, app, admin):
    sc = app.test_client()
    g.pop("_login_user", None)  # the fixture shares one app context between clients
    _login_super(sc)
    owner = AdminUser.query.filter_by(role="owner").one()
    resp = sc.post("/admin/support", data={"action": "reset", "id": owner.id})
    html = resp.get_data(as_text=True)
    assert "Temporary password for owner" in html
    assert not AdminUser.query.filter_by(role="owner").one().check_password(PASSWORD)
    g.pop("_login_user", None)
    assert admin.get("/admin/").status_code == 302
    assert AuditLog.query.filter_by(action="support_reset").count() == 1


def test_super_cannot_target_itself_via_support(support, app):
    sc = app.test_client()
    _login_super(sc)
    sup = AdminUser.query.filter_by(role="super").one()
    assert sc.post("/admin/support", data={"action": "reset", "id": sup.id}).status_code == 400


def test_owner_sees_support_access_notice(support, admin):
    html = admin.get("/admin/account").get_data(as_text=True)
    assert "Support access" in html and "support" in html


def test_removing_env_revokes_support(support):
    support.config.update(SUPERADMIN_USERNAME="", SUPERADMIN_PASSWORD_HASH="")
    sync_superadmin()
    assert AdminUser.query.filter_by(role="super").count() == 0


def test_rotating_hash_signs_out_support_sessions(support):
    old = AdminUser.query.filter_by(role="super").one().session_token
    support.config["SUPERADMIN_PASSWORD_HASH"] = generate_password_hash("another-long-passphrase")
    sync_superadmin()
    assert AdminUser.query.filter_by(role="super").one().session_token != old


def test_plain_password_in_hash_var_rejected(app):
    app.config.update(SUPERADMIN_USERNAME="support", SUPERADMIN_PASSWORD_HASH="hunter2hunter2")
    with pytest.raises(ValueError):
        sync_superadmin()
    db.session.rollback()


def test_superadmin_setup_cli(app):
    result = app.test_cli_runner().invoke(args=["superadmin-setup", "--username", "kobby"],
                                          input="a-very-long-support-pass\na-very-long-support-pass\n")
    assert result.exit_code == 0, result.output
    assert "SUPERADMIN_PASSWORD_HASH=scrypt:" in result.output
    assert "SUPERADMIN_TOTP_SECRET=" in result.output
