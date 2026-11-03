import secrets
from datetime import UTC, datetime

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class AdminUser(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    # "owner" = the client who runs the store; "super" = you, the developer, for remote support.
    role = db.Column(db.String(10), nullable=False, default="owner", server_default="owner")
    # Last TOTP time-step accepted, so a 2FA code can't be replayed.
    totp_last_step = db.Column(db.BigInteger, nullable=True)
    password_hash = db.Column(db.String(255), nullable=False)
    # Bumped on password change so other sessions using the old one are signed out (OWASP A07).
    session_token = db.Column(db.String(64), nullable=False, default=lambda: secrets.token_hex(16))
    failed_logins = db.Column(db.Integer, nullable=False, default=0)
    locked_until = db.Column(db.DateTime, nullable=True)
    last_login_at = db.Column(db.DateTime, nullable=True)

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password, method="scrypt")
        self.session_token = secrets.token_hex(16)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    @property
    def is_super(self) -> bool:
        return self.role == "super"

    def get_id(self) -> str:
        # Flask-Login stores this in the session; including the token invalidates old sessions.
        return f"{self.id}:{self.session_token}"


class Setting(db.Model):
    """Key/value store for every brand-specific field (see settings_schema.py)."""

    key = db.Column(db.String(64), primary_key=True)
    value = db.Column(db.Text, nullable=False, default="")


class Media(db.Model):
    """Uploaded images/videos, stored in the database so each store is self-contained."""

    id = db.Column(db.Integer, primary_key=True)
    token = db.Column(db.String(32), unique=True, nullable=False, default=lambda: secrets.token_hex(12))
    content_type = db.Column(db.String(40), nullable=False)
    data = db.Column(db.LargeBinary, nullable=False)
    size = db.Column(db.Integer, nullable=False)
    width = db.Column(db.Integer, nullable=True)
    height = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    @property
    def url(self) -> str:
        ext = {"image/webp": "webp", "image/png": "png", "video/mp4": "mp4", "video/webm": "webm"}
        return f"/media/{self.token}.{ext.get(self.content_type, 'bin')}"


class Category(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(60), nullable=False)
    slug = db.Column(db.String(70), unique=True, nullable=False)
    position = db.Column(db.Integer, nullable=False, default=0)
    products = db.relationship("Product", back_populates="category")


class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text, nullable=False, default="")
    # Money is stored in minor units (pesewas/cents) to avoid float rounding.
    price_minor = db.Column(db.Integer, nullable=False, default=0)
    sale_price_minor = db.Column(db.Integer, nullable=True)
    sizes = db.Column(db.String(200), nullable=False, default="")
    category_id = db.Column(db.Integer, db.ForeignKey("category.id", ondelete="SET NULL"), nullable=True)
    featured = db.Column(db.Boolean, nullable=False, default=False)
    sold_out = db.Column(db.Boolean, nullable=False, default=False)
    visible = db.Column(db.Boolean, nullable=False, default=True)
    position = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    category = db.relationship("Category", back_populates="products")
    images = db.relationship(
        "ProductImage", order_by="ProductImage.position", cascade="all, delete-orphan", back_populates="product"
    )

    @property
    def size_list(self) -> list[str]:
        return [s.strip() for s in self.sizes.split(",") if s.strip()]

    @property
    def current_price_minor(self) -> int:
        if self.sale_price_minor is not None and self.sale_price_minor < self.price_minor:
            return self.sale_price_minor
        return self.price_minor

    @property
    def on_sale(self) -> bool:
        return self.current_price_minor != self.price_minor


class ProductImage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id", ondelete="CASCADE"), nullable=False)
    media_id = db.Column(db.Integer, db.ForeignKey("media.id", ondelete="CASCADE"), nullable=False)
    position = db.Column(db.Integer, nullable=False, default=0)
    product = db.relationship("Product", back_populates="images")
    media = db.relationship("Media")


class FAQ(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    question = db.Column(db.String(200), nullable=False)
    answer = db.Column(db.Text, nullable=False)
    position = db.Column(db.Integer, nullable=False, default=0)
    visible = db.Column(db.Boolean, nullable=False, default=True)


class Enquiry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(200), nullable=False, default="")
    phone = db.Column(db.String(40), nullable=False)
    items = db.Column(db.Text, nullable=False)
    location = db.Column(db.String(200), nullable=False, default="")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    handled = db.Column(db.Boolean, nullable=False, default=False)


class RateEvent(db.Model):
    """One row per rate-limited action; shared across gunicorn workers via the database."""

    id = db.Column(db.Integer, primary_key=True)
    bucket = db.Column(db.String(120), nullable=False, index=True)
    at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)


class AuditLog(db.Model):
    """Security-relevant events: logins, failures, and admin changes (OWASP A09)."""

    id = db.Column(db.Integer, primary_key=True)
    at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    username = db.Column(db.String(80), nullable=False, default="")
    action = db.Column(db.String(60), nullable=False)
    detail = db.Column(db.String(500), nullable=False, default="")
    ip = db.Column(db.String(64), nullable=False, default="")


class Order(db.Model):
    """An online (Paystack) order. Prices are snapshotted from the database when checkout starts."""

    __tablename__ = "shop_order"

    id = db.Column(db.Integer, primary_key=True)
    reference = db.Column(db.String(40), unique=True, nullable=False, default=lambda: "SF-" + secrets.token_hex(10))
    status = db.Column(db.String(12), nullable=False, default="pending", index=True)  # pending, paid, failed
    amount_minor = db.Column(db.Integer, nullable=False)
    currency = db.Column(db.String(3), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(200), nullable=False)
    phone = db.Column(db.String(40), nullable=False)
    location = db.Column(db.String(200), nullable=False, default="")
    items_json = db.Column(db.Text, nullable=False)
    channel = db.Column(db.String(30), nullable=False, default="")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    paid_at = db.Column(db.DateTime, nullable=True)
    fulfilled = db.Column(db.Boolean, nullable=False, default=False)

    @property
    def items(self) -> list[dict]:
        import json

        return json.loads(self.items_json)
