"""Deployment routes (FR-1, FR-5, FR-6).

The api is admission + command-sink: it validates, gates (auth → Allow-list →
Quota → halt), writes deployment metadata, appends audit events, and enqueues a
command for the runner. It never mutates a phase (AD-3).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from api.bootstrap import Workspace
from api.deps import get_tenant, get_ws
from api.schemas import DeployRequest, PortOverrideUpdate, RedeployRequest
from core import events as ev
from core import policy as pol
from core.errors import NotFound, PortalError
from core.url import slug_for
from core import state as st

router = APIRouter(prefix="/api/deployments", tags=["deployments"])


def _touch(ws: Workspace, slug: str) -> None:
    """Dev surrogate for the Caddy last-access idle signal (AD-12); prod writes
    the signal directly from the proxy adapter."""
    if ws.settings.is_prod:
        return
    try:
        ws.store.touch_last_activity(slug)
    except Exception:
        pass


def _admit(ws: Workspace, tenant: dict, repo_url: str, *, redeploy: bool = False) -> None:
    """Double-gate: Allow-list + Quota + halt, checked at api admission (AD-8).
    The runner re-checks before consuming."""
    cfg = ws.policy_config()
    if cfg["halted"]:
        raise PortalError(
            "Portal is halted. An operator paused new builds and runs.",
            status=409, code="platform_halted",
        )
    try:
        if not (repo_url == "demo" and not ws.settings.is_prod):
            pol.check_allowlist(cfg, repo_url)
    except PortalError as exc:
        ws.store.append_event(tenant["id"], "", ev.ADMISSION_ALLOWLIST_DENIED, tenant["email"],
                              {"repo_url": repo_url, "reason": exc.code})
        raise
    active = _active_counts(ws, tenant["id"])
    try:
        pol.check_quota(cfg, tenant["id"], active, ws.store)
    except PortalError as exc:
        ws.store.append_event(tenant["id"], "", ev.ADMISSION_QUOTA_HIT, tenant["email"],
                              {"repo_url": repo_url, "message": exc.message})
        raise


def _active_counts(ws: Workspace, tenant_id: int) -> dict:
    rows = ws.store.list_projections(tenant_id, limit=1000)
    count = 0
    building = 0
    for r in rows:
        s = r.get("status")
        if s and s not in (st.DENIED, st.FAILED):
            count += 1
            if s in (st.SUBMITTED, st.BUILDING, st.STARTING):
                building += 1
    return {"count": count, "running": sum(1 for r in rows if r.get("status") == st.RUNNING),
            "building": building}


_DELETABLE_STATUSES = {st.STOPPED, st.FAILED, st.DENIED}


def _assert_deletable(status: str | None) -> None:
    """A Deployment owns its quota slot until deleted (owner decision
    2026-09-17); stop it first — a live Deployment cannot be deleted."""
    if status in _DELETABLE_STATUSES:
        return
    raise PortalError(
        "Stop the deployment before deleting it.",
        status=409, code="deployment_not_stopped",
    )


def _own(ws: Workspace, tenant: dict, slug: str) -> dict:
    """FR-9: a tenant sees only their own Deployments; another tenant's slug is
    indistinguishable from missing."""
    proj = ws.store.get_projection(slug)
    if proj is None or proj.get("tenant_id") != tenant["id"]:
        raise NotFound("That Deployment doesn't exist or isn't yours.")
    _touch(ws, slug)
    return proj


@router.post("")
def create_deployment(body: DeployRequest, tenant: dict = Depends(get_tenant),
                      ws: Workspace = Depends(get_ws)):
    # FR-1: same URL already Deployed → re-offer Re-deploy, never a duplicate.
    for row in ws.store.list_projections(tenant["id"], limit=500):
        if row.get("repo_url") == body.repo_url:
            row["already_deployed"] = True
            _touch(ws, row["id"])
            return {"deployment": row}

    _admit(ws, tenant, body.repo_url)
    existing = {r["id"] for r in ws.store.list_projections(tenant["id"], limit=10000)}
    slug = slug_for(body.repo_url, tenant["id"], existing)
    ws.store.create_deployment(slug, tenant["id"], body.repo_url, body.port_override)
    ws.store.append_event(tenant["id"], slug, ev.DEPLOY_SUBMITTED, tenant["email"],
                          {"repo_url": body.repo_url, "port_override": body.port_override})
    ws.store.enqueue_command("deploy", tenant["id"], slug, tenant["email"],
                             {"repo_url": body.repo_url, "port_override": body.port_override})
    proj = ws.store.get_projection(slug) or {}
    proj.setdefault("id", slug)
    proj.setdefault("status", "submitted")
    proj.setdefault("phase", "preparing")
    proj["already_deployed"] = False
    return {"deployment": proj}


@router.get("")
def list_deployments(tenant: dict = Depends(get_tenant), ws: Workspace = Depends(get_ws),
                     limit: int = Query(25, ge=1, le=50), offset: int = Query(0, ge=0)):
    rows = ws.store.list_projections(tenant["id"], limit=limit, offset=offset)
    for r in rows:
        r["already_deployed"] = False
        _touch(ws, r["id"])
    return {
        "deployments": rows,
        "total": ws.store.count_projections(tenant["id"]),
        "limit": limit,
        "offset": offset,
    }


@router.get("/{slug}")
def get_deployment(slug: str, tenant: dict = Depends(get_tenant), ws: Workspace = Depends(get_ws)):
    proj = _own(ws, tenant, slug)
    proj["secrets"] = ws.store.list_secrets(tenant["id"], slug)
    return {"deployment": proj}


@router.post("/{slug}/redeploy")
def redeploy(slug: str, body: RedeployRequest | None = None,
             tenant: dict = Depends(get_tenant), ws: Workspace = Depends(get_ws)):
    proj = _own(ws, tenant, slug)
    repo_url = (body.repo_url if body and body.repo_url else proj["repo_url"])
    port_override = (body.port_override if body else None) or proj.get("port_override")
    _admit(ws, tenant, repo_url, redeploy=True)
    ws.store.update_deployment_meta(slug, port_override=port_override)
    ws.store.append_event(tenant["id"], slug, ev.DEPLOYMENT_REDEPLOYED, tenant["email"],
                          {"repo_url": repo_url, "port_override": port_override})
    ws.store.enqueue_command("redeploy", tenant["id"], slug, tenant["email"],
                             {"repo_url": repo_url, "port_override": port_override})
    proj = _own(ws, tenant, slug)
    return {"deployment": proj}


@router.patch("/{slug}")
def update_settings(slug: str, body: PortOverrideUpdate,
                    tenant: dict = Depends(get_tenant), ws: Workspace = Depends(get_ws)):
    """Port override persists per Deployment and applies at next (re)start
    (FR-5, AD-7). Fractal settings: only port override in v1."""
    _own(ws, tenant, slug)
    ws.store.update_deployment_meta(slug, port_override=body.port_override)
    ws.store.append_event(tenant["id"], slug, ev.DEPLOYMENT_SETTINGS_CHANGED, tenant["email"],
                          {"port_override": body.port_override})
    return {"deployment": _own(ws, tenant, slug)}


@router.post("/{slug}/start")
def start_deployment(slug: str, tenant: dict = Depends(get_tenant), ws: Workspace = Depends(get_ws)):
    proj = _own(ws, tenant, slug)
    if proj.get("status") == "running":
        return {"deployment": proj}
    if proj.get("status") in ("building", "starting"):
        return {"deployment": proj}
    if ws.policy_config()["halted"]:
        # Halt pauses new runs too, not just new Deployments. Refusing here
        # keeps the tenant from queueing a start the runner would only refuse;
        # the runner re-checks the same gate before consuming it.
        raise PortalError(
            "Portal is halted. An operator paused new builds and runs.",
            status=409, code="platform_halted",
        )
    ws.store.append_event(tenant["id"], slug, ev.INSTANCE_START_REQUESTED, tenant["email"])
    ws.store.enqueue_command("start", tenant["id"], slug, tenant["email"])
    return {"deployment": proj}


@router.post("/{slug}/stop")
def stop_deployment(slug: str, tenant: dict = Depends(get_tenant), ws: Workspace = Depends(get_ws)):
    proj = _own(ws, tenant, slug)
    if proj.get("status") not in ("running", "starting", "building", "submitted"):
        return {"deployment": proj}
    ws.store.append_event(tenant["id"], slug, ev.INSTANCE_STOP_REQUESTED, tenant["email"])
    ws.store.enqueue_command("stop", tenant["id"], slug, tenant["email"])
    return {"deployment": proj}


@router.delete("/{slug}")
def delete_deployment(slug: str, tenant: dict = Depends(get_tenant), ws: Workspace = Depends(get_ws)):
    proj = _own(ws, tenant, slug)
    _assert_deletable(proj.get("status"))
    ws.store.append_event(tenant["id"], slug, ev.DEPLOYMENT_DELETE_REQUESTED, tenant["email"])
    ws.store.enqueue_command("delete", tenant["id"], slug, tenant["email"])
    return {"deployment": proj, "pending_delete": True}