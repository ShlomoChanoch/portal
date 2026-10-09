"""Store split (api=control view, runner=executor writes) + contract basics."""
from __future__ import annotations

import pytest

from adapters.store import Store
from api.bootstrap import create_workspace


@pytest.fixture()
def ws(tmp_path):
    from api.config import Settings

    settings = Settings(data_dir=str(tmp_path / "data"), simulate_build_delay=0.0)
    workspace = create_workspace(settings, migrate=True)
    tenant = workspace.store.upsert_tenant("tester@portal.test")
    yield workspace, settings, tenant
    workspace.store.close()


def test_admin_migration_creates_view(ws):
    _ws, settings, _tenant = ws
    admin = Store(settings.db_path, mode="admin")
    row = admin._one("SELECT name FROM sqlite_master WHERE type='view' AND name='deployment_status'")
    assert row is not None
    admin.close()


def test_unclaimed_deployment_reads_as_submitted(ws):
    workspace, settings, tenant = ws
    workspace.store.create_deployment("slug-a", tenant["id"], "https://github.com/x/a", None)
    runner = Store(settings.db_path, mode="executor")
    try:
        proj = runner.get_projection("slug-a")
        assert proj["status"] == "submitted"
        assert proj["repo_url"] == "https://github.com/x/a"
    finally:
        runner.close()


def test_runner_write_visible_to_control_view(ws):
    workspace, settings, tenant = ws
    workspace.store.create_deployment("slug-b", tenant["id"], "demo", None)
    runner = Store(settings.db_path, mode="executor")
    try:
        runner.set_projection("slug-b", status="running", phase="preparing",
                              mapped_port=8080, public_url="http://127.0.0.1:8080")
    finally:
        runner.close()
    proj = workspace.store.get_projection("slug-b")
    assert proj["status"] == "running"
    assert proj["public_url"] == "http://127.0.0.1:8080"


def test_events_are_append_only(ws):
    workspace, _settings, tenant = ws
    workspace.store.append_event(tenant["id"], "", "some.type", "tester", {})
    with pytest.raises(Exception):
        workspace.store._exec("UPDATE events SET actor = 'hacker'")
    with pytest.raises(Exception):
        workspace.store._exec("DELETE FROM events")


def test_command_lifecycle(ws):
    workspace, settings, tenant = ws
    cid = workspace.store.enqueue_command("deploy", tenant["id"], "slug-c", "tester", {"repo_url": "demo"})
    runner = Store(settings.db_path, mode="executor")
    try:
        claimed = runner.claim_command()
        assert claimed["id"] == cid
        status = runner._one("SELECT status FROM commands WHERE id = ?", (cid,))
        assert status["status"] == "running"
        runner.finish_command(cid, "done")
        assert workspace.store.pending_count() == 0
    finally:
        runner.close()


def test_config_rev_and_restore(ws):
    workspace, _settings, _tenant = ws
    prev0 = workspace.store.set_config("tenant-x", {"enabled": False})
    assert prev0 == 0
    prev1 = workspace.store.set_config("tenant-x", {"enabled": True})
    assert prev1 == 1
    snap = workspace.store.get_config_snapshot("tenant-x")
    assert snap["revision"] == 2
    assert snap["value"] == {"enabled": True}
    assert workspace.store.restore_config("tenant-x", 1) is True
    assert workspace.store.get_config("tenant-x") == {"enabled": False}


def test_reaper_smoke(ws):
    """Reaper tick must not raise against a fresh store (empty running set)."""
    from runner.reaper import Reaper

    workspace, settings, tenant = ws
    workspace.store.create_deployment("slug-r", tenant["id"], "demo", None)
    runner = Store(settings.db_path, mode="executor")
    pipeline = object.__new__(object)  # Reaper only stores it
    reaper = Reaper(runner, pipeline, idle_cadence=0.01)
    try:
        reaper.tick()
    finally:
        runner.close()


def _backdate(runner, slug, ts: str) -> None:
    runner._exec("UPDATE deployments SET created_at = ? WHERE id = ?", (ts, slug))


def test_orphan_candidates_and_delete_teardown(ws):
    """Only old, unclaimed deployments (no projection, no queued command) are
    orphan candidates; delete_deployment tears everything down and aborts
    commands."""
    workspace, settings, tenant = ws
    for slug in ("orph-old", "orph-fresh", "orph-cmd", "orph-claimed"):
        workspace.store.create_deployment(slug, tenant["id"], "demo", None)
    workspace.store.create_deployment("orph-del", tenant["id"], "demo", None)
    workspace.store.enqueue_command("deploy", tenant["id"], "orph-del", "tester",
                                    {"repo_url": "demo"})
    workspace.store.upsert_secret(tenant["id"], "orph-del", "DATABASE_URL",
                                  "ct", "alg", "pk", "nn")

    runner = Store(settings.db_path, mode="executor")
    try:
        _backdate(runner, "orph-old", "2026-01-01 00:00:00")
        _backdate(runner, "orph-cmd", "2026-01-01 00:00:00")
        _backdate(runner, "orph-claimed", "2026-01-01 00:00:00")
        runner.set_projection("orph-claimed", status="running", phase="running")
        workspace.store.enqueue_command("deploy", tenant["id"], "orph-cmd", "tester",
                                        {"repo_url": "demo"})

        only = {r["id"] for r in runner.list_orphan_candidates(grace_seconds=60)}
        assert only == {"orph-old"}

        assert runner.delete_deployment("orph-del") is True
        assert runner.delete_deployment("orph-del") is False  # idempotent
        assert runner.get_projection("orph-del") is None
        assert runner.get_deployment("orph-del") is None
        assert runner.list_secrets(tenant["id"], "orph-del") == []
        pending = runner._one(
            "SELECT COUNT(*) AS c FROM commands WHERE deployment_id = ? AND status = 'pending'",
            ("orph-del",),
        )["c"]
        assert pending == 0
        # events survive the teardown — append-only audit (AD-10)
        workspace.store.append_event(tenant["id"], "orph-del", "deploy.submitted", "tester")
    finally:
        runner.close()