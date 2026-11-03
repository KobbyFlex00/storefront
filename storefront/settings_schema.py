"""Every brand-specific field a client can edit in Admin -> Settings.

To make a new field editable for all stores, add it here and use `site.<key>` in a template.
No database migration is needed: values live in the key/value `Setting` table.
"""

import re
from dataclasses import dataclass, field

# Curated font pairs (heading, body). Only these names are ever placed in the Google Fonts URL,
# so admins cannot inject arbitrary CSS or URLs through a font field.
FONT_PAIRS = {
    "editorial": ("Playfair Display", "Inter"),
    "romantic": ("Cormorant Garamond", "Lato"),
    "modern": ("DM Serif Display", "DM Sans"),
    "minimal": ("Jost", "Jost"),
    "bold": ("Bebas Neue", "Montserrat"),
    "soft": ("Fraunces", "Nunito Sans"),
}

CURRENCIES = {
    "GHS": "GH₵",
    "NGN": "₦",
    "KES": "KSh",
    "ZAR": "R",
    "USD": "$",
    "GBP": "£",
    "EUR": "€",
}


@dataclass(frozen=True)
class F:
    key: str
    label: str
    kind: str = "text"  # text, textarea, color, bool, url, phone, email, select, image, video
    default: str = ""
    max_len: int = 200
    help: str = ""
    choices: dict = field(default_factory=dict)


GROUPS: list[tuple[str, str, list[F]]] = [
    ("brand", "Brand", [
        F("store_name", "Store name", default="Maison Lumière", max_len=60),
        F("logo", "Logo", "image", help="PNG/JPG/WebP. Leave empty to show the store name as text."),
        F("favicon", "Browser icon", "image", help="A square image, at least 64×64."),
        F("color_primary", "Primary colour", "color", default="#1f1a17", help="Buttons, headings."),
        F("color_accent", "Accent colour", "color", default="#c98b8b", help="Highlights, badges."),
        F("color_bg", "Background colour", "color", default="#fbf7f4"),
        F("color_surface", "Card colour", "color", default="#ffffff"),
        F("color_text", "Text colour", "color", default="#2b2522"),
        F("font_pair", "Fonts", "select", default="editorial",
          choices={k: f"{h} + {b}" for k, (h, b) in FONT_PAIRS.items()}),
        F("currency", "Currency", "select", default="GHS",
          choices={k: f"{k} ({v})" for k, v in CURRENCIES.items()}),
    ]),
    ("hero", "Hero", [
        F("hero_video", "Hero video", "video", help="MP4 or WebM, up to 15 MB. Shown instead of the image."),
        F("hero_image", "Hero image", "image", help="Also used as the video poster and share image."),
        F("hero_eyebrow", "Small line above headline", default="New pieces, made to elevate you", max_len=80),
        F("hero_title", "Headline", default="Wear your light. Own your presence.", max_len=90),
        F("hero_cta", "Button text", default="Explore the collection", max_len=40),
    ]),
    ("intro", "Brand story", [
        F("show_intro", "Show this section", "bool", default="1"),
        F("intro_kicker", "Small heading", default="Made for her", max_len=60),
        F("intro_title", "Title", default="Feminine by nature. Playful by choice.", max_len=100),
        F("intro_body", "Text", "textarea", max_len=1200,
          default="Thoughtfully made ready-to-wear for women who love pieces that flatter, move and make "
                  "every day feel like an occasion."),
        F("intro_image", "Image", "image"),
    ]),
    ("shop", "Shop", [
        F("shop_title", "Section title", default="Shop the edit", max_len=60),
        F("shop_subtitle", "Subtitle", default="Selected sizes in stock. Some pieces made to order.", max_len=160),
        F("enable_cart", "Let customers build a cart and send it on WhatsApp", "bool", default="1"),
    ]),
    ("payments", "Payments", [
        F("enable_paystack", "Accept card & Mobile Money with Paystack", "bool", default="1",
          help="Works when PAYSTACK_SECRET_KEY is set on the server and the currency is GHS, NGN, KES, ZAR or USD. "
               "WhatsApp ordering stays available either way."),
        F("paystack_note", "Note under the Pay button", max_len=160,
          default="Secure payment by Paystack: card or Mobile Money."),
    ]),
    ("faq", "FAQ", [
        F("show_faq", "Show this section", "bool", default="1"),
        F("faq_title", "Section title", default="Good to know", max_len=60),
    ]),
    ("cta", "Closing banner", [
        F("show_cta", "Show this section", "bool", default="1"),
        F("cta_title", "Title", default="Your light is your signature.", max_len=90),
        F("cta_button", "Button text", default="Make an enquiry", max_len=40),
    ]),
    ("order", "Order section", [
        F("order_title", "Title", default="Order your piece", max_len=80),
        F("order_body", "Delivery & payment notes", "textarea", max_len=1500,
          default="Accra delivery takes 1–3 working days and nationwide 2–5 working days after confirmation. "
                  "Payment details are shared personally once your order is confirmed."),
        F("enquiry_success", "Message after an enquiry is sent", max_len=200,
          default="Thank you — your enquiry has been sent. We'll be in touch soon."),
    ]),
    ("contact", "Contact & social", [
        F("whatsapp", "WhatsApp number", "phone", default="233500000000",
          help="International format, digits only, e.g. 233501234567."),
        F("phone", "Phone (as displayed)", default="050 000 0000", max_len=40),
        F("email", "Public email", "email", max_len=200),
        F("enquiry_email", "Send enquiries to (email)", "email", max_len=200,
          help="Needs SMTP configured on the server. Enquiries are always saved under Admin → Enquiries."),
        F("instagram", "Instagram URL", "url", max_len=300),
        F("tiktok", "TikTok URL", "url", max_len=300),
        F("facebook", "Facebook URL", "url", max_len=300),
        F("location", "Location line", default="Accra, Ghana", max_len=100),
    ]),
    ("footer", "Footer & SEO", [
        F("footer_tagline", "Footer tagline", default="Embrace your light.", max_len=120),
        F("seo_title", "Page title (Google & browser tab)", max_len=70,
          help="Defaults to the store name."),
        F("seo_description", "Description (Google & link previews)", "textarea", max_len=300,
          default="Feminine ready-to-wear. Order online or on WhatsApp, with delivery nationwide."),
    ]),
]

FIELDS: dict[str, F] = {f.key: f for _, _, fields in GROUPS for f in fields}

HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
PHONE_RE = re.compile(r"^[0-9]{7,15}$")
EMAIL_RE = re.compile(r"^[^@\s<>\"']{1,64}@[^@\s<>\"']{1,190}\.[A-Za-z]{2,}$")


def clean(f: F, raw: str) -> tuple[str | None, str | None]:
    """Validate one submitted value. Returns (value, error). Allow-list validation (OWASP A03)."""
    value = (raw or "").strip()
    if f.kind == "bool":
        return ("1" if value in ("1", "on", "true", "y") else "0"), None
    if f.kind in ("image", "video"):
        return None, None  # handled by the upload path
    if len(value) > f.max_len:
        return None, f"{f.label}: keep it under {f.max_len} characters."
    if f.kind == "color":
        if not HEX_RE.match(value):
            return None, f"{f.label}: use a colour like #1f1a17."
        return value.lower(), None
    if f.kind == "select":
        if value not in f.choices:
            return None, f"{f.label}: pick one of the options."
        return value, None
    if f.kind == "phone":
        digits = re.sub(r"[\s+\-()]", "", value)
        if digits and not PHONE_RE.match(digits):
            return None, f"{f.label}: digits only in international format, e.g. 233501234567."
        return digits, None
    if f.kind == "email":
        if value and not EMAIL_RE.match(value):
            return None, f"{f.label}: that doesn't look like an email address."
        return value, None
    if f.kind == "url":
        if value and not safe_url(value):
            return None, f"{f.label}: use a full https:// link."
        return value, None
    return value, None


def safe_url(value: str) -> bool:
    """Only plain https links: blocks javascript:, data: and other dangerous schemes."""
    return bool(re.match(r"^https://[A-Za-z0-9.-]+\.[A-Za-z]{2,}(/[^\s<>\"']*)?$", value))
