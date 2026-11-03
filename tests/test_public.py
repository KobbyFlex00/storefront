from storefront.extensions import db
from storefront.models import Category, Enquiry, Product


def test_home_renders_with_products(app, client):
    cat = Category(name="Dresses", slug="dresses")
    db.session.add(Product(name="Rosé Wrap", price_minor=45000, category=cat, sizes="S, M"))
    db.session.add(Product(name="Hidden piece", price_minor=100, visible=False))
    db.session.commit()
    html = client.get("/").get_data(as_text=True)
    assert "Rosé Wrap" in html
    assert "GH₵450" in html
    assert "Hidden piece" not in html


def test_enquiry_saved(client):
    resp = client.post("/enquiry", data={"name": "Ama", "phone": "0501234567", "items": "Rosé Wrap, size M"})
    assert resp.status_code == 302
    assert Enquiry.query.one().name == "Ama"


def test_enquiry_validation(client):
    client.post("/enquiry", data={"name": "", "phone": "", "items": ""})
    assert Enquiry.query.count() == 0


def test_enquiry_honeypot_drops_bots(client):
    client.post("/enquiry", data={"name": "Bot", "phone": "1", "items": "x", "website": "http://spam"})
    assert Enquiry.query.count() == 0


def test_enquiry_rate_limited(client):
    data = {"name": "Ama", "phone": "0501234567", "items": "x"}
    for _ in range(8):
        client.post("/enquiry", data=data)
    assert Enquiry.query.count() == 5


def test_enquiry_is_escaped_in_admin(admin):
    admin.post("/enquiry", data={"name": "<script>alert(1)</script>", "phone": "1", "items": "x"})
    html = admin.get("/admin/enquiries").get_data(as_text=True)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_robots_and_sitemap(client):
    assert "Disallow: /admin" in client.get("/robots.txt").get_data(as_text=True)
    assert "<urlset" in client.get("/sitemap.xml").get_data(as_text=True)


def test_store_data_json_cannot_break_out_of_script(app, client):
    db.session.add(Product(name="</script><img src=x onerror=alert(1)>", price_minor=1))
    db.session.commit()
    html = client.get("/").get_data(as_text=True)
    assert "</script><img" not in html
