"""Paystack checkout: card + Mobile Money.

Security model:
- The browser only sends product ids, sizes and quantities. Prices and the total are recomputed here
  from the database, so a tampered cart can't change what is charged.
- An order is marked paid only after the server confirms it with Paystack: either via the signed
  webhook (HMAC-SHA512 with the secret key), or by calling Paystack's verify API when the customer
  returns. In both cases the amount and currency must match the order exactly.
- The server only ever talks to the fixed Paystack API host (no user-controlled URLs, no SSRF).
"""

import hashlib
import hmac
import json
import logging
import re
import smtplib
import urllib.error
import urllib.request
from email.message import EmailMessage
from urllib.parse import quote

from flask import Blueprint, abort, current_app, jsonify, render_template, request, url_for

from .extensions import csrf, db
from .models import Order, Product, utcnow
from .security import audit, client_ip, rate_limited
from .settings_schema import EMAIL_RE
from .site import get_site

bp = Blueprint("payments", __name__)
log = logging.getLogger("storefront.payments")

PAYSTACK_CURRENCIES = {"GHS", "NGN", "ZAR", "KES", "USD"}
REF_RE = re.compile(r"^SF-[0-9a-f]{20}$")


class PaystackError(Exception):
    pass


def paystack_ready(site=None) -> bool:
    site = site or get_site()
    return bool(
        current_app.config["PAYSTACK_SECRET_KEY"]
        and site.enable_paystack
        and site.raw("currency") in PAYSTACK_CURRENCIES
    )


def _api(method: str, path: str, payload: dict | None = None) -> dict:
    """Call the Paystack API. `path` is always built from constants in this module."""
    url = current_app.config["PAYSTACK_API"] + path
    if not url.startswith("https://"):
        raise PaystackError("Paystack API must be https.")
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={  # noqa: S310 (https enforced above)
        "Authorization": "Bearer " + current_app.config["PAYSTACK_SECRET_KEY"],
        "Content-Type": "application/json",
        "User-Agent": "storefront/1.0",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310 (fixed https host)
            body = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        try:
            msg = json.loads(exc.read().decode()).get("message", "")
        except (ValueError, OSError):
            msg = ""
        raise PaystackError(f"Paystack said: {msg or exc.code}") from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise PaystackError("Could not reach Paystack.") from exc
    if not body.get("status"):
        raise PaystackError(body.get("message") or "Paystack rejected the request.")
    return body.get("data") or {}


def _build_lines(raw_items) -> tuple[list[dict], int]:
    if not isinstance(raw_items, list) or not 1 <= len(raw_items) <= 30:
        raise ValueError("Your bag is empty or too large.")
    lines, total = [], 0
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise ValueError("Invalid bag.")
        try:
            pid, qty = int(raw.get("id")), int(raw.get("qty"))
        except (TypeError, ValueError) as exc:
            raise ValueError("Invalid bag.") from exc
        size = str(raw.get("size") or "")[:20]
        product = db.session.get(Product, pid)
        if product is None or not product.visible:
            raise ValueError("An item in your bag is no longer available.")
        if product.sold_out:
            raise ValueError(f"{product.name} has just sold out.")
        if not 1 <= qty <= 20:
            raise ValueError("Quantities must be between 1 and 20.")
        sizes = product.size_list
        if len(sizes) > 1 and size not in sizes:
            raise ValueError(f"Please choose a size for {product.name}.")
        if len(sizes) <= 1:
            size = sizes[0] if sizes else ""
        unit = product.current_price_minor
        lines.append({"id": product.id, "name": product.name, "size": size, "qty": qty,
                      "unit": unit, "total": unit * qty})
        total += unit * qty
    if total <= 0:
        raise ValueError("Nothing to pay for.")
    return lines, total


@bp.route("/checkout", methods=["POST"])
def checkout():
    site = get_site()
    if not paystack_ready(site):
        abort(404)
    if rate_limited(f"checkout:{client_ip()}", limit=20, window_seconds=60 * 60):
        return jsonify(error="Too many checkout attempts. Please try again later or order on WhatsApp."), 429
    data = request.get_json(silent=True) or {}
    name = str(data.get("name") or "").strip()[:100]
    email = str(data.get("email") or "").strip()[:200]
    phone = str(data.get("phone") or "").strip()[:40]
    location = str(data.get("location") or "").strip()[:200]
    if not name or not phone:
        return jsonify(error="Please add your name and phone number."), 400
    if not EMAIL_RE.match(email):
        return jsonify(error="Please add a valid email address for your receipt."), 400
    try:
        lines, total = _build_lines(data.get("items"))
    except ValueError as exc:
        return jsonify(error=str(exc)), 400

    order = Order(amount_minor=total, currency=site.raw("currency"), name=name, email=email, phone=phone,
                  location=location, items_json=json.dumps(lines))
    db.session.add(order)
    db.session.commit()
    try:
        result = _api("POST", "/transaction/initialize", {
            "email": email,
            "amount": total,
            "currency": order.currency,
            "reference": order.reference,
            "callback_url": url_for("payments.done", _external=True),
            "channels": ["card", "mobile_money"],
            "metadata": {"order_id": order.id, "phone": phone,
                         "custom_fields": [{"display_name": "Customer", "variable_name": "customer",
                                            "value": name}]},
        })
    except PaystackError as exc:
        order.status = "failed"
        db.session.commit()
        log.warning("Paystack init failed for %s: %s", order.reference, exc)
        return jsonify(error="Online payment is unavailable right now. Please order on WhatsApp."), 502
    auth_url = str(result.get("authorization_url") or "")
    if not auth_url.startswith("https://checkout.paystack.com/"):
        log.error("Unexpected Paystack authorization_url for %s", order.reference)
        return jsonify(error="Online payment is unavailable right now."), 502
    return jsonify(url=auth_url, reference=order.reference)


def _confirm(order: Order, tx: dict, source: str) -> bool:
    """Mark the order paid if Paystack's transaction matches it exactly. Idempotent."""
    if order.status == "paid":
        return True
    ok = (
        tx.get("status") == "success"
        and tx.get("reference") == order.reference
        and int(tx.get("amount") or -1) == order.amount_minor
        and str(tx.get("currency") or "").upper() == order.currency
    )
    if not ok:
        if tx.get("status") in ("failed", "abandoned", "reversed"):
            order.status = "failed"
            db.session.commit()
        elif tx.get("status") == "success":
            # Paid, but not what we asked for: never auto-accept; flag for the owner.
            log.error("Paystack amount/currency mismatch on %s via %s", order.reference, source)
            audit("payment_mismatch", f"{order.reference} via {source}", username="paystack")
        return False
    order.status = "paid"
    order.paid_at = utcnow()
    order.channel = str(tx.get("channel") or "")[:30]
    db.session.commit()
    audit("order_paid", f"{order.reference} {order.currency} {order.amount_minor / 100:.2f} via {source}",
          username="paystack")
    _email_owner(order)
    return True


@bp.route("/checkout/done")
def done():
    reference = request.args.get("reference") or request.args.get("trxref") or ""
    if not REF_RE.match(reference):
        abort(404)
    order = Order.query.filter_by(reference=reference).first_or_404()
    if order.status != "paid" and current_app.config["PAYSTACK_SECRET_KEY"]:
        try:
            _confirm(order, _api("GET", "/transaction/verify/" + quote(reference, safe="")), "return")
        except PaystackError as exc:
            log.warning("Verify failed for %s: %s", reference, exc)
    return render_template("public/order_done.html", order=order)


@bp.route("/paystack/webhook", methods=["POST"])
@csrf.exempt  # authenticated by Paystack's HMAC signature instead
def webhook():
    secret = current_app.config["PAYSTACK_SECRET_KEY"]
    raw = request.get_data(cache=False)
    signature = request.headers.get("X-Paystack-Signature", "")
    expected = hmac.new(secret.encode(), raw, hashlib.sha512).hexdigest() if secret else ""
    if not secret or not hmac.compare_digest(expected, signature):
        audit("webhook_bad_signature", client_ip(), username="paystack")
        abort(401)
    try:
        event = json.loads(raw)
    except ValueError:
        abort(400)
    if event.get("event") == "charge.success":
        tx = event.get("data") or {}
        reference = str(tx.get("reference") or "")
        order = Order.query.filter_by(reference=reference).first() if REF_RE.match(reference) else None
        if order:
            _confirm(order, tx, "webhook")
    return "", 200


def _email_owner(order: Order) -> None:
    cfg = current_app.config
    site = get_site()
    to = site.enquiry_email
    if not (cfg["SMTP_HOST"] and to):
        return
    msg = EmailMessage()
    msg["Subject"] = f"Paid order {order.reference}: {site.money(order.amount_minor)}"
    msg["From"] = cfg["SMTP_FROM"] or cfg["SMTP_USER"]
    msg["To"] = to
    msg["Reply-To"] = order.email
    lines = "\n".join(f"- {i['name']}{' (' + i['size'] + ')' if i['size'] else ''} x{i['qty']}: "
                      f"{site.money(i['total'])}" for i in order.items)
    msg.set_content(f"{order.name} paid {site.money(order.amount_minor)} by {order.channel or 'Paystack'}.\n\n"
                    f"{lines}\n\nPhone: {order.phone}\nEmail: {order.email}\nDelivery: {order.location}\n\n"
                    f"Manage orders: {url_for('admin.orders', _external=True)}")
    try:
        with smtplib.SMTP(cfg["SMTP_HOST"], cfg["SMTP_PORT"], timeout=10) as smtp:
            smtp.starttls()
            if cfg["SMTP_USER"]:
                smtp.login(cfg["SMTP_USER"], cfg["SMTP_PASSWORD"])
            smtp.send_message(msg)
    except (OSError, smtplib.SMTPException):
        log.exception("Could not email paid order %s", order.reference)
