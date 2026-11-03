import hashlib
import hmac
import json

import pytest

from storefront import payments
from storefront.extensions import db
from storefront.models import Order, Product, Setting

KEY = "sk_test_abc123"


@pytest.fixture
def shop(app):
    app.config["PAYSTACK_SECRET_KEY"] = KEY
    db.session.add(Product(id=1, name="Rosé Wrap", price_minor=45000, sizes="S, M"))
    db.session.add(Product(id=2, name="Scarf", price_minor=10000, sale_price_minor=8000))
    db.session.add(Product(id=3, name="Gone", price_minor=500, sold_out=True))
    db.session.commit()
    return app


@pytest.fixture
def paystack(monkeypatch):
    calls = []

    def fake(method, path, payload=None):
        calls.append((method, path, payload))
        if path == "/transaction/initialize":
            return {"authorization_url": "https://checkout.paystack.com/abc123", "reference": payload["reference"]}
        ref = path.rsplit("/", 1)[-1]
        order = Order.query.filter_by(reference=ref).one()
        return {"status": "success", "reference": ref, "amount": order.amount_minor, "currency": order.currency,
                "channel": "mobile_money"}

    monkeypatch.setattr(payments, "_api", fake)
    return calls


BUYER = {"name": "Ama", "phone": "0501234567", "email": "ama@example.com", "location": "Accra"}


def test_server_computes_the_price(shop, client, paystack):
    items = [{"id": 1, "size": "M", "qty": 2, "price": 1}, {"id": 2, "qty": 1}]
    resp = client.post("/checkout", json={**BUYER, "items": items})
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["url"].startswith("https://checkout.paystack.com/")
    _, _, payload = paystack[0]
    assert payload["amount"] == 45000 * 2 + 8000  # sale price used, client "price" ignored
    assert payload["currency"] == "GHS" and payload["channels"] == ["card", "mobile_money"]
    assert Order.query.one().status == "pending"


@pytest.mark.parametrize("items,msg", [
    ([{"id": 3, "qty": 1}], "sold out"),
    ([{"id": 1, "qty": 1}], "choose a size"),
    ([{"id": 1, "size": "XXL", "qty": 1}], "choose a size"),
    ([{"id": 2, "qty": 0}], "between 1 and 20"),
    ([{"id": 2, "qty": 999}], "between 1 and 20"),
    ([{"id": 99, "qty": 1}], "no longer available"),
    ([], "empty"),
    ("nope", "empty"),
])
def test_bad_bags_rejected(shop, client, paystack, items, msg):
    resp = client.post("/checkout", json={**BUYER, "items": items})
    assert resp.status_code == 400 and msg in resp.get_json()["error"]
    assert not paystack


def test_email_required(shop, client, paystack):
    resp = client.post("/checkout", json={**BUYER, "email": "nope", "items": [{"id": 2, "qty": 1}]})
    assert resp.status_code == 400


def test_checkout_off_without_key(app, client):
    assert client.post("/checkout", json={}).status_code == 404


def test_checkout_off_when_disabled_or_unsupported_currency(shop, client, paystack):
    db.session.add(Setting(key="currency", value="GBP"))
    db.session.commit()
    assert client.post("/checkout", json={**BUYER, "items": [{"id": 2, "qty": 1}]}).status_code == 404


def test_unexpected_redirect_url_rejected(shop, client, monkeypatch):
    monkeypatch.setattr(payments, "_api", lambda *a, **k: {"authorization_url": "https://evil.example/pay"})
    resp = client.post("/checkout", json={**BUYER, "items": [{"id": 2, "qty": 1}]})
    assert resp.status_code == 502


def _start(client):
    client.post("/checkout", json={**BUYER, "items": [{"id": 2, "qty": 1}]})
    return Order.query.one()


def test_return_page_verifies_with_paystack(shop, client, paystack):
    order = _start(client)
    html = client.get(f"/checkout/done?reference={order.reference}").get_data(as_text=True)
    assert "Payment received" in html
    assert Order.query.one().status == "paid" and Order.query.one().channel == "mobile_money"


def test_return_page_rejects_bad_reference(shop, client):
    assert client.get("/checkout/done?reference=../../etc").status_code == 404


def _signed(client, event, key=KEY):
    raw = json.dumps(event).encode()
    sig = hmac.new(key.encode(), raw, hashlib.sha512).hexdigest()
    return client.post("/paystack/webhook", data=raw, headers={"X-Paystack-Signature": sig},
                       content_type="application/json")


def test_webhook_marks_paid(shop, client, paystack):
    order = _start(client)
    event = {"event": "charge.success", "data": {"status": "success", "reference": order.reference,
                                                 "amount": 8000, "currency": "GHS", "channel": "card"}}
    assert _signed(client, event).status_code == 200
    assert Order.query.one().status == "paid"
    assert _signed(client, event).status_code == 200  # idempotent
    assert Order.query.one().status == "paid"


def test_webhook_rejects_bad_signature(shop, client, paystack):
    order = _start(client)
    event = {"event": "charge.success", "data": {"status": "success", "reference": order.reference,
                                                 "amount": 8000, "currency": "GHS"}}
    assert _signed(client, event, key="sk_test_wrong").status_code == 401
    assert Order.query.one().status == "pending"


def test_webhook_amount_mismatch_not_paid(shop, client, paystack):
    order = _start(client)
    event = {"event": "charge.success", "data": {"status": "success", "reference": order.reference,
                                                 "amount": 100, "currency": "GHS"}}
    _signed(client, event)
    assert Order.query.one().status == "pending"


def test_orders_page(shop, admin, paystack):
    order = _start(admin)
    admin.get(f"/checkout/done?reference={order.reference}")
    html = admin.get("/admin/orders").get_data(as_text=True)
    assert order.reference in html and "GH₵80" in html
    admin.post(f"/admin/orders/{order.id}", data={"action": "fulfil"})
    assert Order.query.one().fulfilled


def test_checkout_requires_csrf_token(tmp_path):
    from storefront import create_app

    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'c.db'}",
                      "PAYSTACK_SECRET_KEY": KEY})
    with app.app_context():
        db.create_all()
        db.session.add(Product(id=2, name="Scarf", price_minor=10000))
        db.session.commit()
        resp = app.test_client().post("/checkout", json={**BUYER, "items": [{"id": 2, "qty": 1}]})
        assert resp.status_code == 400
