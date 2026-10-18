"""Serve the built React app from the same origin as the API (one container, no CORS needed).

Only the two real pages are served as the SPA shell; any other unknown path stays a plain 404, and
unknown /api paths never fall back to HTML.
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

DEFAULT_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

# Scripts, styles and images only from this origin. Inline styles are not used by the app.
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; base-uri 'none'; form-action 'self'; object-src 'none'; "
    "frame-ancestors 'self' https://huggingface.co"
)


def register_static_site(app: FastAPI, static_dir: Path | None = None) -> bool:
    root = static_dir or DEFAULT_STATIC_DIR
    index = root / "index.html"
    if not index.is_file():
        return False

    assets = root / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    def shell() -> FileResponse:
        return FileResponse(index, media_type="text/html", headers={"Cache-Control": "no-cache", "Content-Security-Policy": CSP})

    @app.get("/", include_in_schema=False)
    def home() -> FileResponse:
        return shell()

    @app.get("/staff", include_in_schema=False)
    @app.get("/staff/", include_in_schema=False)
    def staff_page() -> FileResponse:
        return shell()

    return True
