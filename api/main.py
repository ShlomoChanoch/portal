"""FastAPI app assembly for the Portal control plane.

The api is a standard web app: identity, Deploy form, dashboard, live-log
streaming. It never reaches a socket, a build, or a running container (AD-1).
"""

from __future__ import annotations

import logging
import os
import traceback

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.bootstrap import create_workspace
from api.routes import deployments, health, logs, operator, secrets
from core.errors import PortalError

log = logging.getLogger("portal")
logging.basicConfig(level=logging.INFO)

TITLE = "Portal — Repo URL → Running Container"


def create_app(settings=None):
    app = FastAPI(title=TITLE, version="0.1.0")
    ws = create_workspace(settings)
    app.state.ws = ws

    if ws.settings.env != "prod":
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.exception_handler(PortalError)
    async def portal_error_handler(request: Request, exc: PortalError):
        return JSONResponse(status_code=exc.status, content=exc.payload())

    @app.exception_handler(Exception)
    async def unexpected_handler(request: Request, exc: Exception):
        log.error("unhandled error on %s: %s\n%s", request.url.path, exc,
                  traceback.format_exc())
        return JSONResponse(
            status_code=500,
            content={"error": {"code": "internal_error",
                               "message": "Something went wrong on our side. Try again."}},
        )

    app.include_router(health.router)
    app.include_router(deployments.router)
    app.include_router(logs.router)
    app.include_router(secrets.router)
    app.include_router(operator.router)

    _maybe_mount_spa(app, ws.settings.data_dir)
    return app


def _maybe_mount_spa(app: FastAPI, data_dir: str) -> None:
    """Convenience: serve the built SPA from the api when no reverse proxy is
    present (laptop demo). Production serves the SPA from Caddy, never here."""
    dist = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "frontend", "dist")
    if not os.path.isdir(dist):
        return
    from fastapi.staticfiles import StaticFiles

    spa = FastAPI()

    @spa.get("/{full_path:path}")
    def spa_fallback(full_path: str):
        target = os.path.join(dist, full_path)
        if full_path and os.path.isfile(target):
            from fastapi.responses import FileResponse

            return FileResponse(target)
        from fastapi.responses import FileResponse

        return FileResponse(os.path.join(dist, "index.html"))

    app.mount("/", spa)


app = create_app()