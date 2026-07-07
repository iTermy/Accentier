"""Accentier API server. Serves the built frontend from web/dist when present."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import WEB_DIST
from .db import init_db
# importing modules registers them
from .languages import generic, japanese  # noqa: F401
from .routers import auth_routes, deck_routes, item_routes

app = FastAPI(title="Accentier", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

init_db()

app.include_router(auth_routes.router)
app.include_router(deck_routes.router)
app.include_router(item_routes.router)


@app.get("/api/health")
def health():
    return {"ok": True}


if WEB_DIST.exists():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        target = WEB_DIST / path
        if path and target.is_file():
            return FileResponse(target)
        return FileResponse(WEB_DIST / "index.html")
