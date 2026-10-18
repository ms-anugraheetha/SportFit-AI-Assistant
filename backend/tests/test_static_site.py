from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.static_site import CSP, register_static_site


def make_site(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app.js").write_text("console.log('x')")
    (tmp_path / "index.html").write_text("<!doctype html><title>shell</title>")
    app = FastAPI()

    @app.get("/api/ping")
    def ping():
        return {"ok": True}

    assert register_static_site(app, tmp_path) is True
    return TestClient(app)


def test_pages_and_assets_are_served(tmp_path):
    c = make_site(tmp_path)
    for path in ("/", "/staff", "/staff/"):
        r = c.get(path)
        assert r.status_code == 200 and "shell" in r.text
        assert r.headers["content-security-policy"] == CSP
        assert r.headers["cache-control"] == "no-cache"
    assert c.get("/assets/app.js").status_code == 200


def test_unknown_paths_and_api_do_not_fall_back_to_html(tmp_path):
    c = make_site(tmp_path)
    assert c.get("/api/nope").status_code == 404
    assert "shell" not in c.get("/api/nope").text
    assert c.get("/staff/../etc/passwd").status_code == 404
    assert c.get("/random").status_code == 404
    assert c.get("/assets/../index.html").status_code in (404, 200) and "shell" not in c.get("/assets/%2e%2e/index.html").text


def test_no_build_means_no_routes(tmp_path):
    app = FastAPI()
    assert register_static_site(app, tmp_path) is False
    assert TestClient(app).get("/").status_code == 404
