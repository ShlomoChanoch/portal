"""Operator health surfaces the runner runtime and image lifecycle (TODO 12).

/health is the surface where UI + operator page + audit can distinguish a
real podman runtime (persisted OCI images) from the simulated dev facade.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from adapters.store import Store
from api.bootstrap import create_workspace
from api.routes.operator import health


@pytest.fixture()
def ws(tmp_path):
    from api.config import Settings

    settings = Settings(data_dir=str(tmp_path / "data"), simulate_build_delay=0.0)
    workspace = create_workspace(settings, migrate=True)
    tenant = workspace.store.upsert_tenant("ops@portal.test", role="operator")
    yield workspace, settings, tenant
    workspace.store.close()


def _set_heartbeat(workspace, executor: str, builder: str | None = None) -> None:
    workspace.store.set_config("runner:heartbeat", {
        "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "executor": executor,
        "builder": builder,
        "pid": "4242",
    })


def _set_projection(settings, slug: str, *, tenant_id: int, status: str,
                    image_ref: str | None = None) -> None:
    runner = Store(settings.db_path, mode="executor")
    try:
        runner.set_projection(slug, status=status, phase=status, image_ref=image_ref)
    finally:
        runner.close()


def test_health_surfaces_podman_runtime_and_image_split(ws):
    """A podman heartbeat reports the runtime block, and running instances are
    split by persisted image_ref (real image vs dev facade)."""
    workspace, settings, tenant = ws
    _set_heartbeat(workspace, "podman", "podman")
    for slug in ("img-up", "img-none", "img-stopped"):
        workspace.store.create_deployment(slug, tenant["id"], "demo", None)
    _set_projection(settings, "img-up", tenant_id=tenant["id"], status="running",
                    image_ref="portal/img-up:latest")
    _set_projection(settings, "img-none", tenant_id=tenant["id"], status="running")
    _set_projection(settings, "img-stopped", tenant_id=tenant["id"], status="stopped")

    payload = health(tenant=tenant, ws=workspace)

    assert payload["healthy"] is True
    assert payload["runtime"] == {
        "executor": "podman",
        "builder": "podman",
        "real_images": True,
    }
    usage = payload["resource_usage"]
    assert usage["running_instances"] == 2  # stopped is not running
    assert usage["running_with_image"] == 1
    assert usage["running_without_image"] == 1


def test_health_simulated_executor_flags_dev_facade(ws):
    """A simulated heartbeat is surfaced without pretending to be real."""
    workspace, _settings, tenant = ws
    _set_heartbeat(workspace, "simulated", "simulated")

    payload = health(tenant=tenant, ws=workspace)

    assert payload["runtime"] == {
        "executor": "simulated",
        "builder": "simulated",
        "real_images": False,
    }
    assert payload["healthy"] is True  # dev facade is still a live runner


def test_health_stale_or_missing_heartbeat_yields_null_runtime(ws):
    """No heartbeat → no runtime, runner degraded, not healthy."""
    workspace, _settings, tenant = ws

    payload = health(tenant=tenant, ws=workspace)

    assert payload["runtime"] is None
    assert payload["components"]["runner"] == "degraded"
    assert payload["healthy"] is False

# --- halt / resume route symmetry (bug #4) ---------------------------------
#
# `POST /halt` enqueues a command the runner replays; `halt/clear` used to only
# rewrite policy, so nothing restarted the instances the halt stopped. Both
# routes now enqueue a command, and a start is refused while halted.

def _queued(store, type_: str) -> dict | None:
    return store._one("SELECT * FROM commands WHERE type = ? ORDER BY id DESC LIMIT 1",
                      (type_,))


def test_halt_enqueues_a_command_and_clearing_it_enqueues_a_resume(ws):
    from api.routes.operator import clear_halt, halt
    from api.schemas import ConfirmRequest

    workspace, _settings, tenant = ws
    halt(ConfirmRequest(confirm=True, reason="maintenance"), tenant=tenant, ws=workspace)
    assert _queued(workspace.store, "halt") is not None
    assert workspace.policy_config()["halted"] is True

    clear_halt(tenant=tenant, ws=workspace)

    resume = _queued(workspace.store, "resume")
    assert resume is not None
    assert workspace.policy_config()["halted"] is False


def test_halt_requires_explicit_confirmation(ws):
    from api.routes.operator import halt
    from api.schemas import ConfirmRequest
    from core.errors import PortalError

    workspace, _settings, tenant = ws
    with pytest.raises(PortalError) as err:
        halt(ConfirmRequest(confirm=False, reason="oops"), tenant=tenant, ws=workspace)
    assert err.value.status == 422
    assert _queued(workspace.store, "halt") is None


def test_start_is_refused_while_the_platform_is_halted(ws):
    """Containment: halted means no new runs, including cold starts. 409 (not
    denied) so the Deployment survives the halt instead of being bricked."""
    from api.routes.deployments import start_deployment
    from core.errors import PortalError

    workspace, settings, tenant = ws
    workspace.store.create_deployment("gate-1", tenant["id"], "demo", None)
    _set_projection(settings, "gate-1", tenant_id=tenant["id"], status="stopped")
    workspace.store.set_config("policy", {**workspace.policy_config(),
                                          "halted": True, "halt_reason": "maintenance"})

    with pytest.raises(PortalError) as err:
        start_deployment("gate-1", tenant=tenant, ws=workspace)
    assert err.value.status == 409
    assert err.value.code == "platform_halted"
    assert _queued(workspace.store, "start") is None  # nothing reached the runner


def test_start_is_allowed_once_the_halt_is_cleared(ws):
    from api.routes.deployments import start_deployment

    workspace, settings, tenant = ws
    workspace.store.create_deployment("gate-2", tenant["id"], "demo", None)
    _set_projection(settings, "gate-2", tenant_id=tenant["id"], status="stopped")
    workspace.store.set_config("policy", {**workspace.policy_config(),
                                          "halted": True, "halt_reason": "maintenance"})
    workspace.store.set_config("policy", {**workspace.policy_config(),
                                          "halted": False, "halt_reason": None})

    result = start_deployment("gate-2", tenant=tenant, ws=workspace)

    assert result["deployment"]["id"] == "gate-2"
    assert _queued(workspace.store, "start") is not None
