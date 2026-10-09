"""Quota bug regression tests (TODO 16 + delete feature).

Quota contract (owner decision, 2026-09-17): every created Deployment owns a
slot until it is deleted — a `stopped` Deployment still counts toward
max_instances. The remedy for a slot-full tenant is the Delete action, not an
automatic recount. Phantom rows (created but never claimed) are swept away by
the runner because they read as `submitted` forever.
"""
from __future__ import annotations

import pytest

from adapters.store import Store
from api.bootstrap import create_workspace
from api.config import Settings
from api.routes import deployments as routes
from core import policy
from core.errors import PortalError, QuotaExceeded


@pytest.fixture()
def ws(tmp_path):
    settings = Settings(data_dir=str(tmp_path / "data"), simulate_build_delay=0.0)
    workspace = create_workspace(settings, migrate=True)
    tenant = workspace.store.upsert_tenant("tester@portal.test")
    yield workspace, settings, tenant
    workspace.store.close()


def test_active_counts_includes_stopped_and_terminals_excluded(ws):
    """Stopped keeps its slot (owner decision) — only failed/denied release."""
    workspace, settings, tenant = ws
    ws_store = workspace.store
    for slug, status in {
        "q-run": "running", "q-stop": "stopped", "q-sub": "submitted",
        "q-bld": "building", "q-sta": "starting", "q-fail": "failed", "q-den": "denied",
    }.items():
        ws_store.create_deployment(slug, tenant["id"], "demo", None)
    runner = Store(settings.db_path, mode="executor")
    try:
        for slug, status in {
            "q-run": "running", "q-stop": "stopped", "q-sub": "submitted",
            "q-bld": "building", "q-sta": "starting", "q-fail": "failed", "q-den": "denied",
        }.items():
            runner.set_projection(slug, status=status, phase=status)
    finally:
        runner.close()

    active = routes._active_counts(workspace, tenant["id"])
    assert active["count"] == 5          # running + stopped + submitted + building + starting
    assert active["building"] == 3       # submitted + building + starting
    assert active["running"] == 1


def test_three_stopped_block_quota_until_deleted(ws):
    """Headline bug: 3 stopped instances with max 3 → blocked; deleting one
    frees the slot (the remedy is the Delete feature, not a recount)."""
    workspace, settings, tenant = ws
    cfg = policy.normalize_config({"quota": {"max_instances": 3}})
    for i in range(3):
        workspace.store.create_deployment(f"s{i}", tenant["id"], "demo", None)
    runner = Store(settings.db_path, mode="executor")
    try:
        for i in range(3):
            runner.set_projection(f"s{i}", status="stopped", phase="stopped")
    finally:
        runner.close()

    active = routes._active_counts(workspace, tenant["id"])
    assert active["count"] == 3
    with pytest.raises(QuotaExceeded):
        policy.check_quota(cfg, tenant["id"], active, workspace.store)

    assert workspace.store.delete_deployment("s0") is True
    active = routes._active_counts(workspace, tenant["id"])
    assert active["count"] == 2
    policy.check_quota(cfg, tenant["id"], active, workspace.store)  # no raise


def test_orphan_phantom_counts_until_deleted(ws):
    """A deployments row with no projection coalesces to `submitted` (phantom
    slot). Deleting it releases count and building."""
    workspace, _settings, tenant = ws
    workspace.store.create_deployment("phantom-1", tenant["id"], "demo", None)

    active = routes._active_counts(workspace, tenant["id"])
    assert active["count"] == 1
    assert active["building"] == 1

    assert workspace.store.delete_deployment("phantom-1") is True
    active = routes._active_counts(workspace, tenant["id"])
    assert active["count"] == 0
    assert active["building"] == 0
    assert workspace.store.get_projection("phantom-1") is None


@pytest.mark.parametrize(
    ("status", "expect_delete"),
    [
        ("stopped", True),
        ("failed", True),
        ("denied", True),
        ("running", False),
        ("starting", False),
        ("building", False),
        ("submitted", False),
        (None, False),
    ],
)
def test_assert_deletable_gate(status, expect_delete):
    if expect_delete:
        routes._assert_deletable(status)
        return
    with pytest.raises(PortalError) as exc:
        routes._assert_deletable(status)
    assert exc.value.code == "deployment_not_stopped"
    assert exc.value.status == 409