import logging
import smtplib
from email.message import EmailMessage

from flask import Blueprint, Response, current_app, flash, redirect, render_template, request, url_for

from ..extensions import db
from ..models import FAQ, Category, Enquiry, Product
from ..security import client_ip, rate_limited
from ..settings_schema import EMAIL_RE
from ..site import get_site

bp = Blueprint("public", __name__)
log = logging.getLogger("storefront.public")

ENQUIRY_LIMITS = {"name": 100, "email": 200, "phone": 40, "items": 2000, "location": 200}


def _catalogue():
    categories = Category.query.order_by(Category.position, Category.name).all()
    products = (
        Product.query.filter_by(visible=True)
        .order_by(Product.featured.desc(), Product.position, Product.created_at.desc())
        .all()
    )
    used = {p.category_id for p in products}
    return [c for c in categories if c.id in used], products


@bp.route("/")
def home():
    site = get_site()
    categories, products = _catalogue()
    faqs = FAQ.query.filter_by(visible=True).order_by(FAQ.position, FAQ.id).all()
    # Data for the cart/quick-view script; rendered with |tojson so it is safe inside <script>.
    product_data = [
        {
            "id": p.id,
            "name": p.name,
            "price": p.current_price_minor,
            "priceText": site.money(p.current_price_minor),
            "wasText": site.money(p.price_minor) if p.on_sale else "",
            "sizes": p.size_list,
            "soldOut": p.sold_out,
            "description": p.description,
            "images": [img.media.url for img in p.images],
            "category": p.category.slug if p.category else "",
        }
        for p in products
    ]
    return render_template(
        "public/home.html",
        categories=categories,
        products=products,
        product_data=product_data,
        faqs=faqs,
        currency_symbol=site.currency_symbol,
    )


@bp.route("/enquiry", methods=["POST"])
def enquiry():
    site = get_site()
    form = {k: (request.form.get(k) or "").strip() for k in ENQUIRY_LIMITS}
    # Honeypot: real people never see or fill this field.
    if request.form.get("website"):
        flash(site.enquiry_success, "success")
        return redirect(url_for("public.home", _anchor="order"))
    errors = []
    if not form["name"]:
        errors.append("Please add your name.")
    if not form["phone"]:
        errors.append("Please add a phone number so we can reach you.")
    if not form["items"]:
        errors.append("Tell us what you'd like to order.")
    if form["email"] and not EMAIL_RE.match(form["email"]):
        errors.append("That email address doesn't look right.")
    for key, limit in ENQUIRY_LIMITS.items():
        if len(form[key]) > limit:
            errors.append(f"The {key} field is too long.")
    if errors:
        for e in errors:
            flash(e, "danger")
        return redirect(url_for("public.home", _anchor="order"))
    if rate_limited(f"enquiry:{client_ip()}", limit=5, window_seconds=60 * 60):
        flash("You've sent several enquiries already. Please message us on WhatsApp instead.", "danger")
        return redirect(url_for("public.home", _anchor="order"))

    enq = Enquiry(**form)
    db.session.add(enq)
    db.session.commit()
    _email_owner(site, enq)
    flash(site.enquiry_success, "success")
    return redirect(url_for("public.home", _anchor="order"))


def _email_owner(site, enq: Enquiry) -> None:
    cfg = current_app.config
    to = site.enquiry_email
    if not (cfg["SMTP_HOST"] and to):
        return
    msg = EmailMessage()  # rejects CR/LF in headers, so no header injection
    msg["Subject"] = f"New order enquiry from {enq.name[:60]}"
    msg["From"] = cfg["SMTP_FROM"] or cfg["SMTP_USER"]
    msg["To"] = to
    if enq.email:
        msg["Reply-To"] = enq.email
    msg.set_content(
        f"Name: {enq.name}\nPhone: {enq.phone}\nEmail: {enq.email}\nDelivery: {enq.location}\n\n"
        f"Order:\n{enq.items}\n\nSee all enquiries at {url_for('admin.enquiries', _external=True)}"
    )
    try:
        with smtplib.SMTP(cfg["SMTP_HOST"], cfg["SMTP_PORT"], timeout=10) as smtp:
            smtp.starttls()
            if cfg["SMTP_USER"]:
                smtp.login(cfg["SMTP_USER"], cfg["SMTP_PASSWORD"])
            smtp.send_message(msg)
    except (OSError, smtplib.SMTPException):
        log.exception("Could not email enquiry %s; it is saved in the dashboard.", enq.id)


@bp.route("/robots.txt")
def robots():
    body = f"User-agent: *\nDisallow: /admin\nSitemap: {url_for('public.sitemap', _external=True)}\n"
    return Response(body, mimetype="text/plain")


@bp.route("/sitemap.xml")
def sitemap():
    home_url = url_for("public.home", _external=True)
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"<url><loc>{home_url}</loc></url></urlset>"
    )
    return Response(body, mimetype="application/xml")


@bp.route("/healthz")
def healthz():
    return {"ok": True}
