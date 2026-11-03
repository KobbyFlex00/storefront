"""Loads the store's settings once per request and exposes them to templates as `site`."""

from urllib.parse import quote

from flask import g

from .extensions import db
from .models import Media, Setting
from .settings_schema import CURRENCIES, FIELDS, FONT_PAIRS


class Site:
    def __init__(self, values: dict[str, str]):
        self._v = values
        tokens = [values.get(f.key) for f in FIELDS.values() if f.kind in ("image", "video") and values.get(f.key)]
        self._media = {m.token: m for m in Media.query.filter(Media.token.in_(tokens)).all()} if tokens else {}

    def __getattr__(self, key: str):
        if key.startswith("_"):
            raise AttributeError(key)
        f = FIELDS.get(key)
        if f is None:
            raise AttributeError(key)
        value = self._v.get(key, f.default)
        if f.kind == "bool":
            return value == "1"
        if f.kind in ("image", "video"):
            m = self._media.get(value)
            return m.url if m else ""
        return value

    def raw(self, key: str) -> str:
        f = FIELDS[key]
        return self._v.get(key, f.default)

    @property
    def fonts(self) -> tuple[str, str]:
        return FONT_PAIRS.get(self.raw("font_pair"), FONT_PAIRS["editorial"])

    @property
    def fonts_url(self) -> str:
        families = []
        for name in dict.fromkeys(self.fonts):
            families.append("family=" + quote(name).replace("%20", "+") + ":wght@400;500;600;700")
        return "https://fonts.googleapis.com/css2?" + "&".join(families) + "&display=swap"

    @property
    def currency_symbol(self) -> str:
        return CURRENCIES.get(self.raw("currency"), "")

    @property
    def title(self) -> str:
        return self.raw("seo_title") or self.raw("store_name")

    def money(self, minor: int | None) -> str:
        if minor is None:
            return ""
        whole, cents = divmod(int(minor), 100)
        amount = f"{whole:,}" + (f".{cents:02d}" if cents else "")
        return f"{self.currency_symbol}{amount}"

    def whatsapp_link(self, text: str = "") -> str:
        number = self.raw("whatsapp")
        if not number:
            return ""
        link = f"https://wa.me/{number}"
        return link + ("?text=" + quote(text) if text else "")


def get_site() -> Site:
    if "site" not in g:
        g.site = Site({s.key: s.value for s in Setting.query.all()})
    return g.site


def save_settings(values: dict[str, str]) -> None:
    for key, value in values.items():
        row = db.session.get(Setting, key)
        if row is None:
            db.session.add(Setting(key=key, value=value))
        else:
            row.value = value
    db.session.commit()
    g.pop("site", None)
