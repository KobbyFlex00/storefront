import io

from storefront.extensions import db
from storefront.models import FAQ, Category, Media, Product, Setting

from .conftest import image_bytes


def test_settings_change_restyles_site(admin, client):
    resp = admin.post("/admin/settings/brand", data={
        "store_name": "Kente & Co", "color_primary": "#112233", "color_accent": "#aa5500",
        "color_bg": "#ffffff", "color_surface": "#ffffff", "color_text": "#000000",
        "font_pair": "modern", "currency": "NGN",
    })
    assert resp.status_code == 302
    html = client.get("/").get_data(as_text=True)
    assert "Kente &amp; Co" in html
    assert "--primary: #112233" in html
    assert "DM+Serif+Display" in html


def test_settings_reject_css_injection(admin):
    admin.post("/admin/settings/brand", data={
        "store_name": "X", "color_primary": "red;}</style><script>alert(1)</script>", "color_accent": "#aa5500",
        "color_bg": "#ffffff", "color_surface": "#ffffff", "color_text": "#000000",
        "font_pair": "modern", "currency": "GHS",
    })
    assert db.session.get(Setting, "color_primary") is None


def test_settings_reject_javascript_url(admin):
    admin.post("/admin/settings/contact", data={"instagram": "javascript:alert(1)", "whatsapp": "233501234567"})
    assert db.session.get(Setting, "instagram") is None


def test_unknown_font_rejected(admin):
    admin.post("/admin/settings/brand", data={"font_pair": "evil", "color_primary": "#000000"})
    assert db.session.get(Setting, "font_pair") is None


def test_product_crud_with_image(admin, client):
    cat = Category(name="Sets", slug="sets")
    db.session.add(cat)
    db.session.commit()
    resp = admin.post("/admin/products/new", data={
        "name": "Lilac Co-ord", "price": "480.50", "category_id": str(cat.id), "sizes": "S, M", "visible": "1",
        "images": (image_bytes(), "photo.jpg"),
    }, content_type="multipart/form-data")
    assert resp.status_code == 302
    p = Product.query.one()
    assert p.price_minor == 48050 and p.category == cat and len(p.images) == 1
    media = p.images[0].media
    assert media.content_type == "image/webp" and max(media.width, media.height) <= 1600
    served = client.get(media.url)
    assert served.status_code == 200 and served.mimetype == "image/webp"
    assert "GH₵480.50" in client.get("/").get_data(as_text=True)

    admin.post(f"/admin/products/{p.id}/delete")
    assert Product.query.count() == 0 and Media.query.count() == 0


def test_non_image_upload_rejected(admin):
    admin.post("/admin/products/new", data={
        "name": "Bad", "price": "1", "visible": "1",
        "images": (io.BytesIO(b"<?php system($_GET['c']); ?>"), "shell.php.jpg"),
    }, content_type="multipart/form-data")
    assert Product.query.count() == 0 and Media.query.count() == 0


def test_svg_rejected(admin):
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"></svg>'
    admin.post("/admin/settings/brand", data={
        "store_name": "X", "color_primary": "#000000", "color_accent": "#000000", "color_bg": "#ffffff",
        "color_surface": "#ffffff", "color_text": "#000000", "font_pair": "modern", "currency": "GHS",
        "logo": (io.BytesIO(svg), "logo.svg"),
    }, content_type="multipart/form-data")
    assert Media.query.count() == 0


def test_bad_price_rejected(admin):
    admin.post("/admin/products/new", data={"name": "X", "price": "free"})
    assert Product.query.count() == 0


def test_video_magic_bytes(admin):
    fake = io.BytesIO(b"not a video at all" * 10)
    admin.post("/admin/settings/hero", data={"hero_video": (fake, "clip.mp4")}, content_type="multipart/form-data")
    assert Media.query.count() == 0
    mp4 = io.BytesIO(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64)
    admin.post("/admin/settings/hero", data={"hero_video": (mp4, "clip.mp4"), "hero_title": "Hi", "hero_eyebrow": "",
                                             "hero_cta": "Go"}, content_type="multipart/form-data")
    m = Media.query.one()
    assert m.content_type == "video/mp4"


def test_media_range_requests(app, admin, client):
    m = Media(content_type="video/mp4", data=b"0123456789", size=10)
    db.session.add(m)
    db.session.commit()
    resp = client.get(m.url, headers={"Range": "bytes=2-5"})
    assert resp.status_code == 206 and resp.data == b"2345"
    assert client.get(f"/media/{m.token}.webp").status_code == 404
    assert client.get("/media/../../etc.mp4").status_code == 404


def test_categories_and_faq(admin, client):
    admin.post("/admin/categories", data={"action": "add", "name": "Occasion"})
    assert Category.query.one().slug == "occasion"
    admin.post("/admin/faq", data={"action": "add", "question": "Do you ship?", "answer": "Yes.", "visible": "1"})
    assert FAQ.query.count() == 1
    assert "Do you ship?" in client.get("/").get_data(as_text=True)


def test_enquiry_inbox(admin):
    admin.post("/enquiry", data={"name": "Ama", "phone": "+233 50 123 4567", "items": "Dress"})
    html = admin.get("/admin/enquiries").get_data(as_text=True)
    assert "https://wa.me/233501234567" in html
