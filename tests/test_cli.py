import json

from storefront.extensions import db
from storefront.models import FAQ, Media, Product, Setting


def test_seed_export_import_roundtrip(app, tmp_path):
    runner = app.test_cli_runner()
    assert "Demo store ready" in runner.invoke(args=["seed-demo"]).output
    assert Product.query.count() == 8 and FAQ.query.count() == 4
    db.session.add(Setting(key="store_name", value="Template Store"))
    db.session.commit()
    path = tmp_path / "store.json"
    assert runner.invoke(args=["export-store", str(path)]).exit_code == 0
    data = json.loads(path.read_text())
    assert len(data["products"]) == 8
    result = runner.invoke(args=["import-store", str(path), "--replace"])
    assert result.exit_code == 0, result.output
    assert Product.query.count() == 8
    assert all(p.images for p in Product.query.all())
    assert Media.query.count() == len(data["media"])


def test_ensure_admin_rejects_weak_password(app, monkeypatch):
    monkeypatch.setenv("ADMIN_USERNAME", "newowner")
    monkeypatch.setenv("ADMIN_PASSWORD", "short")
    result = app.test_cli_runner().invoke(args=["ensure-admin"])
    assert result.exit_code != 0 and "not strong enough" in result.output
