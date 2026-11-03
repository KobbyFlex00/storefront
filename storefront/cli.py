import base64
import io
import json
import os
import random

import click
from PIL import Image, ImageDraw, ImageFilter
from werkzeug.security import generate_password_hash

from . import totp
from .auth import password_problem, sync_superadmin
from .extensions import db
from .models import FAQ, AdminUser, Category, Media, Product, ProductImage, Setting
from .settings_schema import FIELDS


def register_cli(app):
    @app.cli.command("ensure-admin")
    @click.option("--reset", is_flag=True, help="Also reset the password to ADMIN_PASSWORD if the user exists.")
    def ensure_admin(reset):
        """Create the store owner's login from ADMIN_USERNAME / ADMIN_PASSWORD."""
        username = os.environ.get("ADMIN_USERNAME", "admin").strip()
        password = os.environ.get("ADMIN_PASSWORD", "")
        try:
            click.echo(sync_superadmin())
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc
        user = AdminUser.query.filter_by(username=username, role="owner").first()
        if user and not reset:
            click.echo(f"Admin '{username}' already exists.")
            return
        if problem := password_problem(password, username):
            raise click.ClickException(f"ADMIN_PASSWORD is not strong enough: {problem}")
        if user is None:
            user = AdminUser(username=username)
            db.session.add(user)
        user.set_password(password)
        user.failed_logins, user.locked_until = 0, None
        db.session.commit()
        click.echo(f"Admin '{username}' is ready.")

    @app.cli.command("superadmin-setup")
    @click.option("--username", prompt="Support username", default="support")
    def superadmin_setup(username):
        """Make the SUPERADMIN_* values to paste into every client store (run once, on your own machine)."""
        password = click.prompt("Support password (16+ characters)", hide_input=True, confirmation_prompt=True)
        if len(password) < 16:
            raise click.ClickException("Use at least 16 characters: this login opens every store.")
        if problem := password_problem(password, username):
            raise click.ClickException(problem)
        secret = totp.new_secret()
        click.echo("\nAdd these to EVERY store's environment (Render -> Environment):\n")
        click.echo(f"SUPERADMIN_USERNAME={username}")
        click.echo(f"SUPERADMIN_PASSWORD_HASH={generate_password_hash(password, method='scrypt')}")
        click.echo(f"SUPERADMIN_TOTP_SECRET={secret}")
        click.echo("\nThen add the 2FA secret to your authenticator app (Google Authenticator, Authy, 1Password):")
        click.echo(f"  Setup key: {secret}")
        click.echo(f"  Or open/scan: {totp.provisioning_uri(secret, username)}")
        click.echo("\nKeep these private. To revoke support access to a store, delete the three variables there.")

    @app.cli.command("seed-demo")
    @click.option("--force", is_flag=True, help="Add demo products even if the store already has some.")
    def seed_demo(force):
        """Fill an empty store with demo products and FAQ so it looks finished for a sales demo."""
        if Product.query.count() and not force:
            click.echo("Store already has products; use --force to add the demo anyway.")
            return
        _seed()
        click.echo("Demo store ready.")

    @app.cli.command("export-store")
    @click.argument("path", type=click.Path(dir_okay=False, writable=True))
    def export_store(path):
        """Write settings, categories, products, FAQ and media to a JSON file (no logins, no enquiries)."""
        data = {
            "version": 1,
            "settings": {s.key: s.value for s in Setting.query.all()},
            "media": [
                {"token": m.token, "content_type": m.content_type, "width": m.width, "height": m.height,
                 "data": base64.b64encode(m.data).decode()}
                for m in Media.query.all()
            ],
            "categories": [{"name": c.name, "slug": c.slug, "position": c.position} for c in Category.query.all()],
            "products": [
                {"name": p.name, "description": p.description, "price_minor": p.price_minor,
                 "sale_price_minor": p.sale_price_minor, "sizes": p.sizes,
                 "category": p.category.slug if p.category else None, "featured": p.featured,
                 "sold_out": p.sold_out, "visible": p.visible, "position": p.position,
                 "images": [i.media.token for i in p.images]}
                for p in Product.query.all()
            ],
            "faqs": [{"question": f.question, "answer": f.answer, "position": f.position, "visible": f.visible}
                     for f in FAQ.query.all()],
        }
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        click.echo(f"Exported to {path}")

    @app.cli.command("import-store")
    @click.argument("path", type=click.Path(exists=True, dir_okay=False))
    @click.option("--replace", is_flag=True, help="Delete the current catalogue first.")
    def import_store(path, replace):
        """Load a store exported with export-store (e.g. to start a new client from a template)."""
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if replace:
            ProductImage.query.delete()
            Product.query.delete()
            Category.query.delete()
            FAQ.query.delete()
            Media.query.delete()
            db.session.flush()
        tokens = {}
        for m in data.get("media", []):
            media = Media(content_type=m["content_type"], data=base64.b64decode(m["data"]),
                          width=m.get("width"), height=m.get("height"))
            media.size = len(media.data)
            if m["content_type"] not in ("image/webp", "image/png", "video/mp4", "video/webm"):
                continue
            db.session.add(media)
            db.session.flush()
            tokens[m["token"]] = media
        for key, value in data.get("settings", {}).items():
            f = FIELDS.get(key)
            if f is None:
                continue
            if f.kind in ("image", "video"):
                value = tokens[value].token if value in tokens else ""
            row = db.session.get(Setting, key) or Setting(key=key)
            row.value = value
            db.session.add(row)
        cats = {}
        for c in data.get("categories", []):
            cat = Category.query.filter_by(slug=c["slug"]).first() or Category(slug=c["slug"])
            cat.name, cat.position = c["name"], c.get("position", 0)
            db.session.add(cat)
            cats[c["slug"]] = cat
        for p in data.get("products", []):
            prod = Product(
                name=p["name"], description=p.get("description", ""), price_minor=int(p["price_minor"]),
                sale_price_minor=p.get("sale_price_minor"), sizes=p.get("sizes", ""),
                category=cats.get(p.get("category")), featured=bool(p.get("featured")),
                sold_out=bool(p.get("sold_out")), visible=bool(p.get("visible", True)),
                position=int(p.get("position", 0)),
            )
            for pos, tok in enumerate(p.get("images", [])):
                if tok in tokens:
                    prod.images.append(ProductImage(media=tokens[tok], position=pos))
            db.session.add(prod)
        for f in data.get("faqs", []):
            db.session.add(FAQ(question=f["question"], answer=f["answer"], position=f.get("position", 0),
                               visible=bool(f.get("visible", True))))
        db.session.commit()
        click.echo("Import complete.")


# ------------------------------------------------------------------ demo content

PALETTES = [
    ((233, 196, 190), (176, 112, 112)),
    ((242, 226, 207), (196, 160, 120)),
    ((214, 223, 210), (120, 146, 120)),
    ((228, 214, 232), (150, 120, 168)),
    ((240, 214, 200), (200, 122, 96)),
    ((215, 222, 234), (110, 130, 170)),
    ((246, 232, 220), (160, 110, 90)),
    ((230, 220, 212), (90, 70, 62)),
]


def _demo_image(seed: int, w: int = 900, h: int = 1200, label: str = "") -> Media:
    rnd = random.Random(seed)
    top, bottom = PALETTES[seed % len(PALETTES)]
    img = Image.new("RGB", (w, h))
    draw = ImageDraw.Draw(img)
    for y in range(h):
        t = y / h
        draw.line([(0, y), (w, y)], fill=tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    for _ in range(5):
        r = rnd.randint(w // 5, w // 2)
        x, y = rnd.randint(0, w), rnd.randint(0, h)
        od.ellipse([x - r, y - r, x + r, y + r], fill=(255, 255, 255, rnd.randint(25, 60)))
    # A simple dress silhouette so the cards read as fashion.
    cx, top_y = w // 2, int(h * 0.22)
    od.polygon([(cx - 70, top_y), (cx + 70, top_y), (cx + 95, top_y + 230), (cx + 230, int(h * 0.85)),
                (cx - 230, int(h * 0.85)), (cx - 95, top_y + 230)], fill=(255, 255, 255, 70))
    od.ellipse([cx - 40, top_y - 110, cx + 40, top_y - 30], fill=(255, 255, 255, 70))
    img = Image.alpha_composite(img.convert("RGBA"), overlay.filter(ImageFilter.GaussianBlur(2))).convert("RGB")
    out = io.BytesIO()
    img.save(out, "WEBP", quality=80)
    data = out.getvalue()
    media = Media(content_type="image/webp", data=data, size=len(data), width=w, height=h)
    db.session.add(media)
    db.session.flush()
    return media


DEMO_PRODUCTS = [
    ("Rosé Wrap Dress", "dresses", 45000, None, "S, M, L, XL",
     "A soft wrap silhouette with fluttered sleeves and a tie waist that flatters every shape."),
    ("Celeste Two-Piece Set", "sets", 52000, 46000, "S, M, L",
     "Cropped top and high-waisted wide-leg trousers in breathable linen blend."),
    ("Golden Hour Maxi", "occasion", 78000, None, "XS, S, M, L",
     "Floor-length satin with a cowl neckline and open back, made for evenings to remember."),
    ("Blush Pleated Midi", "dresses", 39000, None, "S, M, L, XL, XXL",
     "Pleated chiffon midi with a smocked waist and puff sleeves."),
    ("Sage Lounge Set", "sets", 35000, None, "S, M, L, XL",
     "Relaxed shirt and shorts set in a buttery-soft fabric."),
    ("Noir Corset Gown", "occasion", 95000, None, "Made to order",
     "Structured corset bodice with a flowing skirt. Made to your measurements in 7–10 days."),
    ("Sunday Shirt Dress", "dresses", 32000, None, "S, M, L",
     "Belted poplin shirt dress that goes from brunch to the office."),
    ("Lilac Co-ord", "sets", 48000, None, "S, M, L",
     "Fitted waistcoat and matching mini skirt in textured crepe."),
]

DEMO_FAQS = [
    ("How do I order?", "Add pieces to your bag and send it on WhatsApp, or fill the enquiry form. "
                        "We'll confirm availability, your size and delivery, then share payment details."),
    ("What sizes do you carry?", "Most pieces come in S to XL. Some are made to order, so we can "
                                 "make them to your measurements."),
    ("How long does delivery take?", "Accra: 1–3 working days. Nationwide: 2–5 working days after "
                                     "your order is confirmed."),
    ("Can I request a custom piece?", "Yes. We can adjust colour, fit and small details. Message us "
                                      "with what you have in mind."),
]


def _seed():
    cats = {}
    for pos, (slug, name) in enumerate([("dresses", "Dresses"), ("sets", "Sets"), ("occasion", "Occasion")]):
        cat = Category.query.filter_by(slug=slug).first() or Category(slug=slug, name=name, position=pos)
        db.session.add(cat)
        cats[slug] = cat
    for i, (name, cat, price, sale, sizes, desc) in enumerate(DEMO_PRODUCTS):
        p = Product(name=name, category=cats[cat], price_minor=price, sale_price_minor=sale, sizes=sizes,
                    description=desc, featured=i < 2, position=i, sold_out=(i == 6))
        db.session.add(p)
        p.images.append(ProductImage(media=_demo_image(i * 2), position=0))
        p.images.append(ProductImage(media=_demo_image(i * 2 + 1), position=1))
        db.session.add(p)
    for pos, (q, a) in enumerate(DEMO_FAQS):
        db.session.add(FAQ(question=q, answer=a, position=pos))
    hero = _demo_image(4, 1600, 1100)
    intro = _demo_image(9, 1000, 1250)
    for key, value in (("hero_image", hero.token), ("intro_image", intro.token)):
        row = db.session.get(Setting, key) or Setting(key=key)
        row.value = value
        db.session.add(row)
    db.session.commit()
