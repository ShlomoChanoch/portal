"""Operator surface (FR-16, FR-12, FR-11, FR-15, FR-17, NFR-4).

Health, usage, policy configuration (with last-known-good rollback), the
append-only audit, force-stop, and the platform Halt. Operators see activity
and policy outcome metadata only — never source, logs, or secrets (Story 5.1).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query

from api.bootstrap import Workspace
from api.deps import get_tenant, get_ws
from api.schemas import ConfirmRequest, OperatorConfigUpdate, RollbackRequest
from core import events as ev
from core.errors import PortalError

router = APIRouter(prefix="/api/operator", tags=["operator"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _resolve_store(ws: Workspace, slug: str) -> dict | None:
    """Operator resource resolution is id-based (slug) across all tenants."""
    return ws.store.get_projection(slug)


@router.get("/health")
def health(tenant: dict = Depends(get_tenant), ws: Workspace = Depends(get_ws)):
    cfg = ws.policy_config()
    heartbeat = ws.store.get_config("runner:heartbeat")
    stale = True
    if heartbeat and heartbeat.get("ts"):
        try:
            stale = (datetime.now(timezone.utc) - datetime.fromisoformat(heartbeat["ts"])).total_seconds() > 15
        except ValueError:
            stale = True
    running = ws.store.list_running_projections()
    tenants = ws.store.list_tenants()
    now = datetime.now(timezone.utc)
    recent = ws.store.read_events(since_ts=(now - timedelta(hours=24)).isoformat(), limit=1000)
    active_users = {e["tenant_id"] for e in recent}
    admissions = [e for e in recent if e["type"] == ev.DEPLOY_SUBMITTED]
    quota_hits = [e for e in recent if e["type"] == ev.ADMISSION_QUOTA_HIT]
    quota_hit_rate = round(len(quota_hits) / max(1, len(admissions) + len(quota_hits)), 3)
    running_with_image = sum(1 for r in running if r.get("image_ref"))
    runtime = None
    if heartbeat and heartbeat.get("executor"):
        runtime = {
            "executor": heartbeat.get("executor"),
            "builder": heartbeat.get("builder"),
            "real_images": heartbeat["executor"] == "podman",
        }
    return {
        "healthy": not stale and not cfg["halted"],
        "components": {
            "runner": "ok" if not stale else "degraded",
            "halted": True if cfg["halted"] else False,
        },
        "runner_heartbeat": heartbeat,
        "runtime": runtime,
        "active_users": sorted(active_users),
        "active_user_count": len(active_users),
        "resource_usage": {
            "running_instances": len(running),
            "running_with_image": running_with_image,
            "running_without_image": len(running) - running_with_image,
            "quota_hit_rate": quota_hit_rate,
            "quota_hits": len(quota_hits),
        },
        "tenant_count": len(tenants),
        "per_tenant": [
            {
                "tenant_id": t["id"],
                "email": t["email"],
                "role": t["role"],
                "instances": sum(1 for r in running if r["tenant_id"] == t["id"]),
            }
            for t in tenants
        ],
    }


@router.get("/activities")
def activities(limit: int = Query(50, ge=1, le=200),
               tenant: dict = Depends(get_tenant), ws: Workspace = Depends(get_ws)):
    """Per-user recent activity — metadata only (never content)."""
    rows = ws.store.read_events(limit=limit)
    return {"activities": [
        {
            "id": e["id"],
            "tenant_id": e["tenant_id"],
            "actor": e["actor"],
            "deployment_id": e["deployment_id"],
            "type": e["type"],
            "created_at": e["created_at"],
        }
        for e in rows
    ]}


@router.get("/config")
def get_policy(tenant: dict = Depends(get_tenant), ws: Workspace = Depends(get_ws)):
    cfg = ws.policy_config()
    snap = ws.store.get_config_snapshot("policy")
    return {"config": cfg, "revision": snap["revision"] if snap else 0}


@router.put("/config")
def set_policy(body: OperatorConfigUpdate, tenant: dict = Depends(get_tenant),
               ws: Workspace = Depends(get_ws)):
    current = ws.policy_config()
    patch = body.model_dump(exclude_none=True)
    if "quota" in patch:
        merged = {**current["quota"]["per_user"], **patch.pop("quota")}
        patch["quota"] = merged
    new_policy = {
        "allow_all": patch.get("allow_all", current["allow_all"]),
        "allow_list": patch.get("allow_list", current["allow_list"]),
        "quota": patch.get("quota", current["quota"]),
        "egress": patch.get("egress", current.get("egress") or []),
        "idle_stop_seconds": patch.get("idle_stop_seconds", current.get("idle_stop_seconds", 1800)),
        "halted": current["halted"],
        "halt_reason": current.get("halt_reason"),
    }
    prev = ws.store.set_config("policy", new_policy)
    ws.store.append_event(tenant["id"], "", ev.OPERATOR_CONFIG_CHANGED, tenant["email"],
                          {"actor": tenant["email"], "previous_revision": prev})
    return {"config": ws.policy_config(), "revision": ws.store.get_config_snapshot("policy")["revision"]}


@router.post("/config/rollback")
def rollback_policy(body: RollbackRequest, tenant: dict = Depends(get_tenant),
                    ws: Workspace = Depends(get_ws)):
    ok = ws.store.restore_config("policy", body.revision)
    if not ok:
        raise PortalError("That configuration revision no longer exists.", status=404, code="not_found")
    ws.store.append_event(tenant["id"], "", ev.OPERATOR_CONFIG_ROLLBACK, tenant["email"],
                          {"restored_revision": body.revision})
    snap = ws.store.get_config_snapshot("policy")
    return {"config": ws.policy_config(), "revision": snap["revision"] if snap else 0}


@router.post("/instances/{slug}/force-stop")
def force_stop(slug: str, tenant: dict = Depends(get_tenant), ws: Workspace = Depends(get_ws)):
    proj = _resolve_store(ws, slug)
    if proj is None:
        raise PortalError("No such Deployment.", status=404, code="not_found")
    ws.store.append_event(tenant["id"], slug, ev.OPERATOR_FORCE_STOP, tenant["email"])
    ws.store.enqueue_command("force_stop", proj["tenant_id"], slug, tenant["email"])
    return {"ok": True}


@router.post("/halt")
def halt(body: ConfirmRequest, tenant: dict = Depends(get_tenant), ws: Workspace = Depends(get_ws)):
    if not body.confirm:
        raise PortalError("Halt requires explicit confirmation.", status=422, code="confirmation_required")
    new_policy = {**ws.policy_config(), "halted": True, "halt_reason": body.reason or "operator_halt"}
    prev = ws.store.set_config("policy", new_policy)
    ws.store.append_event(tenant["id"], "", ev.OPERATOR_HALT, tenant["email"],
                          {"reason": body.reason, "previous_revision": prev})
    ws.store.enqueue_command("halt", tenant["id"], "", tenant["email"])
    return {"ok": True, "halted": True}


@router.post("/halt/clear")
def clear_halt(tenant: dict = Depends(get_tenant), ws: Workspace = Depends(get_ws)):
    new_policy = {**ws.policy_config(), "halted": False, "halt_reason": None}
    prev = ws.store.set_config("policy", new_policy)
    ws.store.append_event(tenant["id"], "", ev.OPERATOR_HALT, tenant["email"],
                          {"cleared": True, "previous_revision": prev})
    # Symmetric with /halt: the api only flips policy (it never mutates a
    # projection, AD-3), so the runner is told to restart the instances the halt
    # stopped. Without this the halt is a one-way door.
    ws.store.enqueue_command("resume", tenant["id"], "", tenant["email"])
    return {"ok": True, "halted": False}


@router.get("/audit")
def audit(tenant: dict = Depends(get_tenant), ws: Workspace = Depends(get_ws),
          actor: str | None = Query(None), since: str | None = Query(None),
          until: str | None = Query(None), limit: int = Query(200, ge=1, le=1000)):
    rows = ws.store.read_events(actor=actor, since_ts=since, until_ts=until, limit=limit)
    return {"audit": [
        {
            "id": e["id"],
            "tenant_id": e["tenant_id"],
            "actor": e["actor"],
            "deployment_id": e["deployment_id"],
            "type": e["type"],
            "created_at": e["created_at"],
            "meta": e["meta"],
        }
        for e in rows
    ]}