import io

import pytest
from PIL import Image

from storefront import create_app
from storefront.extensions import db
from storefront.models import AdminUser

PASSWORD = "correct-horse-battery"


@pytest.fixture
def app(tmp_path):
    app = create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'test.db'}",
        "WTF_CSRF_ENABLED": False,
        "SERVER_NAME": "shop.test",
    })
    with app.app_context():
        db.create_all()
        user = AdminUser(username="owner")
        user.set_password(PASSWORD)
        db.session.add(user)
        db.session.commit()
        yield app
        db.session.remove()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def admin(client):
    resp = client.post("/admin/login", data={"username": "owner", "password": PASSWORD})
    assert resp.status_code == 302
    return client


def image_bytes(fmt="JPEG", size=(2400, 3200), color=(200, 120, 120)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, fmt)
    buf.seek(0)
    return buf
