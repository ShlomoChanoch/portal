"""Build/Run log routes (FR-3, FR-7).

Plain JSON replay from an offset (refresh survival) + SSE streams that live on
the persisted lines and end explicitly. The api streams what the runner wrote;
it does not fabricate lines or terminal states (UX-DR11).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from adapters.sse import event_stream_response, stream_log
from api.bootstrap import Workspace
from api.deps import get_tenant, get_ws
from api.routes.deployments import _touch
from core.errors import NotFound
from core import state as st

router = APIRouter(prefix="/api/deployments", tags=["logs"])

STREAMS = ("build", "run")


def _probe(ws: Workspace, tenant: dict, slug: str) -> dict:
    proj = ws.store.get_projection(slug)
    if proj is None or proj.get("tenant_id") != tenant["id"]:
        raise NotFound("That Deployment doesn't exist or isn't yours.")
    _touch(ws, slug)
    return proj


def _read_logs(ws: Workspace, tenant: dict, slug: str, stream: str, offset: int, limit: int):
    if stream not in STREAMS:
        raise NotFound("Unknown log stream.")
    _probe(ws, tenant, slug)
    data = ws.logs.read(tenant["id"], slug, stream, offset=offset, limit=limit)
    data["status"] = _probe(ws, tenant, slug).get("status")
    return data


@router.get("/{slug}/build/logs")
def build_logs(slug: str, tenant: dict = Depends(get_tenant), ws: Workspace = Depends(get_ws),
               offset: int = Query(0, ge=0), limit: int = Query(500, ge=1, le=2000)):
    return _read_logs(ws, tenant, slug, "build", offset, limit)


@router.get("/{slug}/run/logs")
def run_logs(slug: str, tenant: dict = Depends(get_tenant), ws: Workspace = Depends(get_ws),
             offset: int = Query(0, ge=0), limit: int = Query(500, ge=1, le=2000)):
    return _read_logs(ws, tenant, slug, "run", offset, limit)


@router.get("/{slug}/build/logs/stream")
def build_log_stream(slug: str, tenant: dict = Depends(get_tenant), ws: Workspace = Depends(get_ws),
                     offset: int = Query(0, ge=0)):
    _probe(ws, tenant, slug)

    async def terminal():
        p = ws.store.get_projection(slug)
        return p is not None and p.get("status") not in (None, "submitted", "building")

    return event_stream_response(
        stream_log(ws.logs, tenant["id"], slug, "build", offset, terminal=terminal)
    )


@router.get("/{slug}/run/logs/stream")
def run_log_stream(slug: str, tenant: dict = Depends(get_tenant), ws: Workspace = Depends(get_ws),
                   offset: int = Query(0, ge=0)):
    _probe(ws, tenant, slug)

    async def terminal():
        p = ws.store.get_projection(slug)
        if p is None:
            return False
        return p.get("status") in (st.STOPPED, st.FAILED, st.DENIED)

    return event_stream_response(
        stream_log(ws.logs, tenant["id"], slug, "run", offset, terminal=terminal)
    )