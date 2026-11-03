import os
import platform
import re
import secrets
from decimal import Decimal, InvalidOperation
from functools import wraps

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from ..extensions import db
from ..media import UploadError, save_image, save_video
from ..models import FAQ, AdminUser, AuditLog, Category, Enquiry, Media, Order, Product, ProductImage, utcnow
from ..security import audit
from ..settings_schema import GROUPS, clean
from ..site import get_site, save_settings

bp = Blueprint("admin", __name__, url_prefix="/admin")


@bp.before_request
@login_required
def _require_login():
    """Every admin route requires a signed-in owner, even if a route forgets a decorator (OWASP A01)."""


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "item"


def _unique_slug(name: str, exclude_id: int | None = None) -> str:
    base = _slugify(name)
    slug, n = base, 2
    while (c := Category.query.filter_by(slug=slug).first()) and c.id != exclude_id:
        slug, n = f"{base}-{n}", n + 1
    return slug


def _parse_money(text: str, required: bool) -> int | None:
    text = (text or "").replace(",", "").strip()
    if not text:
        if required:
            raise ValueError("Add a price.")
        return None
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError("Prices must be numbers, e.g. 450 or 450.50.") from exc
    if value < 0 or value > Decimal("10000000"):
        raise ValueError("That price is out of range.")
    return int((value * 100).quantize(Decimal("1")))


def _int(value, default=0, lo=-100000, hi=100000) -> int:
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------- dashboard


@bp.route("/")
def home():
    stats = {
        "products": Product.query.count(),
        "visible": Product.query.filter_by(visible=True).count(),
        "new_enquiries": Enquiry.query.filter_by(handled=False).count(),
        "categories": Category.query.count(),
        "orders_to_send": Order.query.filter_by(status="paid", fulfilled=False).count(),
    }
    recent = Enquiry.query.order_by(Enquiry.created_at.desc()).limit(5).all()
    return render_template("admin/home.html", stats=stats, recent=recent)


# ---------------------------------------------------------------- settings


@bp.route("/settings")
def settings_index():
    return redirect(url_for("admin.settings", group="brand"))


@bp.route("/settings/<group>", methods=["GET", "POST"])
def settings(group):
    groups = {key: (title, fields) for key, title, fields in GROUPS}
    if group not in groups:
        abort(404)
    title, fields = groups[group]
    if request.method == "POST":
        values, errors = {}, []
        for f in fields:
            if f.kind in ("image", "video"):
                if request.form.get(f"remove_{f.key}"):
                    values[f.key] = ""
                upload = request.files.get(f.key)
                if upload and upload.filename:
                    try:
                        if f.kind == "video":
                            media = save_video(upload)
                        else:
                            media = save_image(upload, max_side=256 if f.key == "favicon" else 1800,
                                               fmt="PNG" if f.key == "favicon" else "WEBP")
                        values[f.key] = media.token
                    except UploadError as e:
                        errors.append(f"{f.label}: {e}")
                continue
            value, error = clean(f, request.form.get(f.key, ""))
            if error:
                errors.append(error)
            else:
                values[f.key] = value
        if errors:
            db.session.rollback()
            for e in errors:
                flash(e, "danger")
        else:
            save_settings(values)
            audit("settings_saved", group)
            flash("Saved. Your site is updated.", "success")
            return redirect(url_for("admin.settings", group=group))
    return render_template("admin/settings.html", groups=GROUPS, group=group, title=title, fields=fields,
                           site=get_site())


# ---------------------------------------------------------------- products


@bp.route("/products")
def products():
    items = Product.query.order_by(Product.position, Product.created_at.desc()).all()
    return render_template("admin/products.html", products=items)


@bp.route("/products/new", methods=["GET", "POST"])
@bp.route("/products/<int:pid>", methods=["GET", "POST"])
def product_edit(pid=None):
    product = db.get_or_404(Product, pid) if pid else Product()
    categories = Category.query.order_by(Category.position, Category.name).all()
    if request.method == "POST":
        f = request.form
        errors = []
        name = f.get("name", "").strip()
        if not name or len(name) > 120:
            errors.append("Give the product a name (up to 120 characters).")
        try:
            price = _parse_money(f.get("price"), required=True)
            sale = _parse_money(f.get("sale_price"), required=False)
        except ValueError as e:
            errors.append(str(e))
            price = sale = None
        description = f.get("description", "").strip()
        if len(description) > 3000:
            errors.append("Keep the description under 3000 characters.")
        sizes = ", ".join(s.strip()[:20] for s in f.get("sizes", "").split(",") if s.strip())[:200]
        cat_id = _int(f.get("category_id"), 0)
        category = db.session.get(Category, cat_id) if cat_id else None

        if not errors:
            product.name = name
            product.price_minor = price
            product.sale_price_minor = sale
            product.description = description
            product.sizes = sizes
            product.category = category
            product.featured = bool(f.get("featured"))
            product.sold_out = bool(f.get("sold_out"))
            product.visible = bool(f.get("visible"))
            product.position = _int(f.get("position"), 0)
            db.session.add(product)
            db.session.flush()
            next_pos = len(product.images)
            for upload in request.files.getlist("images")[:10]:
                if not upload or not upload.filename:
                    continue
                try:
                    media = save_image(upload)
                    product.images.append(ProductImage(media=media, position=next_pos))
                    next_pos += 1
                except UploadError as e:
                    errors.append(f"{upload.filename[:60]}: {e}")
        if errors:
            db.session.rollback()
            for e in errors:
                flash(e, "danger")
            if pid is None:
                product = Product(name=name, description=description, sizes=sizes)
        else:
            db.session.commit()
            audit("product_saved", f"{product.id} {product.name}")
            flash("Product saved.", "success")
            return redirect(url_for("admin.product_edit", pid=product.id))
    return render_template("admin/product_edit.html", product=product, categories=categories, site=get_site())


@bp.route("/products/<int:pid>/delete", methods=["POST"])
def product_delete(pid):
    product = db.get_or_404(Product, pid)
    name = product.name
    media_ids = [img.media_id for img in product.images]
    db.session.delete(product)
    db.session.flush()
    if media_ids:
        Media.query.filter(Media.id.in_(media_ids)).delete(synchronize_session=False)
    db.session.commit()
    audit("product_deleted", f"{pid} {name}")
    flash("Product deleted.", "success")
    return redirect(url_for("admin.products"))


@bp.route("/products/<int:pid>/images/<int:iid>", methods=["POST"])
def product_image(pid, iid):
    img = db.get_or_404(ProductImage, iid)
    if img.product_id != pid:
        abort(404)
    action = request.form.get("action")
    product = img.product
    if action == "delete":
        media = img.media
        product.images.remove(img)
        db.session.delete(media)
    elif action == "first":
        others = [i for i in product.images if i.id != img.id]
        for pos, i in enumerate([img, *others]):
            i.position = pos
    db.session.commit()
    audit("product_image_" + (action or "none"), f"{pid}/{iid}")
    return redirect(url_for("admin.product_edit", pid=pid))


# ---------------------------------------------------------------- categories


@bp.route("/categories", methods=["GET", "POST"])
def categories():
    if request.method == "POST":
        action = request.form.get("action")
        name = request.form.get("name", "").strip()[:60]
        cid = _int(request.form.get("id"), 0)
        if action == "add" and name:
            db.session.add(Category(name=name, slug=_unique_slug(name),
                                    position=_int(request.form.get("position"), Category.query.count())))
        elif action == "save" and cid and name:
            c = db.get_or_404(Category, cid)
            c.name, c.slug = name, _unique_slug(name, exclude_id=c.id)
            c.position = _int(request.form.get("position"), c.position)
        elif action == "delete" and cid:
            db.session.delete(db.get_or_404(Category, cid))
        else:
            flash("Add a name.", "danger")
            return redirect(url_for("admin.categories"))
        db.session.commit()
        audit("category_" + action, name or str(cid))
        flash("Categories updated.", "success")
        return redirect(url_for("admin.categories"))
    items = Category.query.order_by(Category.position, Category.name).all()
    return render_template("admin/categories.html", categories=items)


# ---------------------------------------------------------------- FAQ


@bp.route("/faq", methods=["GET", "POST"])
def faq():
    if request.method == "POST":
        action = request.form.get("action")
        fid = _int(request.form.get("id"), 0)
        question = request.form.get("question", "").strip()[:200]
        answer = request.form.get("answer", "").strip()[:2000]
        if action == "delete" and fid:
            db.session.delete(db.get_or_404(FAQ, fid))
        elif action in ("add", "save") and question and answer:
            item = db.get_or_404(FAQ, fid) if action == "save" else FAQ()
            item.question, item.answer = question, answer
            item.position = _int(request.form.get("position"), item.position or FAQ.query.count())
            item.visible = bool(request.form.get("visible"))
            db.session.add(item)
        else:
            flash("Add both a question and an answer.", "danger")
            return redirect(url_for("admin.faq"))
        db.session.commit()
        audit("faq_" + action, question or str(fid))
        flash("FAQ updated.", "success")
        return redirect(url_for("admin.faq"))
    items = FAQ.query.order_by(FAQ.position, FAQ.id).all()
    return render_template("admin/faq.html", faqs=items)


# ---------------------------------------------------------------- enquiries


@bp.route("/enquiries")
def enquiries():
    show = request.args.get("show", "new")
    q = Enquiry.query
    if show == "new":
        q = q.filter_by(handled=False)
    items = q.order_by(Enquiry.created_at.desc()).limit(300).all()
    return render_template("admin/enquiries.html", enquiries=items, show=show, site=get_site())


@bp.route("/enquiries/<int:eid>", methods=["POST"])
def enquiry_update(eid):
    enq = db.get_or_404(Enquiry, eid)
    action = request.form.get("action")
    if action == "toggle":
        enq.handled = not enq.handled
    elif action == "delete":
        db.session.delete(enq)
    db.session.commit()
    audit("enquiry_" + (action or "none"), str(eid))
    return redirect(url_for("admin.enquiries", show=request.args.get("show", "new")))


# ---------------------------------------------------------------- security log


@bp.route("/activity")
def activity():
    items = AuditLog.query.order_by(AuditLog.at.desc()).limit(200).all()
    return render_template("admin/activity.html", items=items, me=current_user)


# ---------------------------------------------------------------- orders (Paystack)


@bp.route("/orders")
def orders():
    show = request.args.get("show", "paid")
    q = Order.query
    if show == "paid":
        q = q.filter_by(status="paid", fulfilled=False)
    elif show == "done":
        q = q.filter_by(status="paid", fulfilled=True)
    items = q.order_by(Order.created_at.desc()).limit(300).all()
    return render_template("admin/orders.html", orders=items, show=show, site=get_site(),
                           paystack_on=bool(current_app.config["PAYSTACK_SECRET_KEY"]))


@bp.route("/orders/<int:oid>", methods=["POST"])
def order_update(oid):
    order = db.get_or_404(Order, oid)
    if request.form.get("action") == "fulfil" and order.status == "paid":
        order.fulfilled = not order.fulfilled
        db.session.commit()
        audit("order_fulfilled" if order.fulfilled else "order_unfulfilled", order.reference)
    return redirect(url_for("admin.orders", show=request.args.get("show", "paid")))


# ---------------------------------------------------------------- support (superadmin only)


def super_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not current_user.is_super:
            audit("support_page_denied", request.path)
            abort(403)
        return view(*args, **kwargs)

    return wrapper


def _diagnostics() -> dict:
    cfg = current_app.config
    key = cfg["PAYSTACK_SECRET_KEY"]
    media_bytes = db.session.query(db.func.coalesce(db.func.sum(Media.size), 0)).scalar()
    return {
        "Version (git commit)": (os.environ.get("RENDER_GIT_COMMIT") or "unknown")[:12],
        "Python": platform.python_version(),
        "Database": db.engine.dialect.name,
        "Production mode": "yes" if cfg["PRODUCTION"] else "NO",
        "Allowed hosts": ", ".join(cfg["ALLOWED_HOSTS"]) or "any (set ALLOWED_HOSTS)",
        "Support 2FA": "on" if cfg["SUPERADMIN_TOTP_SECRET"] else "OFF",
        "Email (SMTP)": cfg["SMTP_HOST"] or "not configured",
        "Paystack": ("live" if key.startswith("sk_live_") else "test" if key.startswith("sk_test_") else "set")
        if key else "not configured",
        "Products": Product.query.count(),
        "Media files": f"{Media.query.count()} ({media_bytes / 1_048_576:.1f} MB)",
        "Orders paid / pending / failed": " / ".join(
            str(Order.query.filter_by(status=s).count()) for s in ("paid", "pending", "failed")),
        "Enquiries (new)": Enquiry.query.filter_by(handled=False).count(),
    }


@bp.route("/support", methods=["GET", "POST"])
@super_required
def support():
    temp_password, temp_for = None, None
    if request.method == "POST":
        action = request.form.get("action")
        uid = _int(request.form.get("id"), 0)
        if action in ("unlock", "reset") and uid:
            user = db.get_or_404(AdminUser, uid)
            if user.is_super:
                abort(400)
            user.failed_logins, user.locked_until = 0, None
            if action == "reset":
                temp_password = secrets.token_urlsafe(12)
                user.set_password(temp_password)  # also signs the owner out everywhere
                temp_for = user.username
            db.session.commit()
            audit("support_" + action, user.username)
            if action == "unlock":
                flash(f"{user.username} is unlocked.", "success")
        elif action == "add":
            username = request.form.get("username", "").strip()[:80]
            if not re.fullmatch(r"[A-Za-z0-9_.@-]{3,80}", username):
                flash("Usernames are 3+ letters, numbers, dots, dashes or underscores.", "danger")
            elif AdminUser.query.filter_by(username=username).first():
                flash("That username is taken.", "danger")
            else:
                temp_password = secrets.token_urlsafe(12)
                user = AdminUser(username=username, role="owner")
                user.set_password(temp_password)
                db.session.add(user)
                db.session.commit()
                temp_for = username
                audit("support_add_owner", username)
    owners = AdminUser.query.filter_by(role="owner").order_by(AdminUser.username).all()
    return render_template("admin/support.html", owners=owners, diag=_diagnostics(), temp_password=temp_password,
                           temp_for=temp_for, now=utcnow())
