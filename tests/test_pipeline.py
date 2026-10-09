"""Runner pipeline state transitions with standalone test doubles (offline)."""
from __future__ import annotations

import subprocess

import pytest

from _doubles import FakeBuilder, ImageRequiringExecutor, LoopbackExecutor
from adapters.crypt import load_existing_keypair
from adapters.store import Store
from api.bootstrap import create_workspace
from api.config import Settings
from core import events as ev
from core import state as st
from runner.main import make_executor, make_builder
from runner.pipeline import Pipeline


@pytest.fixture()
def stack(tmp_path):
    settings = Settings(data_dir=str(tmp_path / "data"), runner_executor="simulated",
                        simulate_build_delay=0.0)
    ws = create_workspace(settings, migrate=True)
    tenant = ws.store.upsert_tenant("tester@portal.test")
    ws.seed_policy_once()
    runner = Store(settings.db_path, mode="executor")
    runner_sec = load_existing_keypair(settings.runner_pub_path)[1]
    executor = LoopbackExecutor()
    builder = FakeBuilder()
    pipeline = Pipeline(settings, runner, ws.logs, runner_sec, b"", executor, builder)

    def deploy(slug, repo="demo"):
        ws.store.create_deployment(slug, tenant["id"], repo, None)
        ws.store.enqueue_command("deploy", tenant["id"], slug, "tester", {"repo_url": repo})
        cmd = runner.claim_command()
        pipeline.dispatch(cmd)
        runner.finish_command(cmd["id"], "done")

    def projection(slug):
        row = dict(ws.store.get_projection(slug))
        row.pop("secrets", None)
        return row

    yield ws, runner, pipeline, tenant, deploy, projection
    runner.close()
    ws.store.close()


def test_deploy_lands_running(stack):
    _ws, _runner, _pipeline, _tenant, deploy, projection = stack
    deploy("dl-1", repo="demo")
    p = projection("dl-1")
    assert p["status"] == "running"
    assert p["mapped_port"]
    assert p["public_url"].startswith("http://")


def test_deploy_emits_expected_events(stack):
    ws, _runner, _pipeline, tenant, deploy, _projection = stack
    deploy("dl-2", repo="demo")
    types = [e["type"] for e in ws.store.read_events(tenant_id=tenant["id"], limit=30)]
    assert ev.BUILD_STARTED in types
    assert ev.BUILD_COMPLETED in types
    assert ev.INSTANCE_STARTED in types


def test_stop_transitions_to_stopped(stack):
    ws, runner, pipeline, tenant, deploy, projection = stack
    deploy("dl-3", repo="demo")
    ws.store.enqueue_command("stop", tenant["id"], "dl-3", "tester")
    cmd = runner.claim_command()
    pipeline.dispatch(cmd)
    runner.finish_command(cmd["id"], "done")
    assert projection("dl-3")["status"] == "stopped"


def test_redeploy_keeps_running_after_previous_run(stack):
    ws, runner, pipeline, tenant, deploy, projection = stack
    deploy("dl-4", repo="demo")
    ws.store.enqueue_command("redeploy", tenant["id"], "dl-4", "tester",
                             {"repo_url": "demo", "port_override": None})
    cmd = runner.claim_command()
    pipeline.dispatch(cmd)
    runner.finish_command(cmd["id"], "done")
    assert projection("dl-4")["status"] == "running"


def test_halted_policy_denies_new_deploys(stack):
    ws, runner, pipeline, tenant, _deploy, projection = stack
    ws.store.set_config("policy", {**ws.store.get_config("policy", {}),
                                   "halted": True, "halt_reason": "test"})
    ws.store.create_deployment("dl-5", tenant["id"], "demo", None)
    ws.store.enqueue_command("deploy", tenant["id"], "dl-5", "tester", {"repo_url": "demo"})
    cmd = runner.claim_command()
    pipeline.dispatch(cmd)
    runner.finish_command(cmd["id"], "done")
    p = projection("dl-5")
    assert p["status"] == "denied"
    assert p["reason"] == "platform_halted"


def test_secrets_roundtrip_through_api_and_runner(stack):
    """AD-9 end-to-end: api encrypts, runner alone decrypts (real keypair)."""
    ws, runner, _pipeline, tenant, deploy, _projection = stack
    from api.bootstrap import encrypt_secret

    deploy("dl-6", repo="demo")
    env = encrypt_secret(ws, "postgres://secret")
    ws.store.upsert_secret(tenant["id"], "dl-6", "DATABASE_URL",
                           env["ciphertext"], env["alg"], env["sender_pk"], env["nonce"])
    rows = runner.list_secret_payloads(tenant["id"], "dl-6")
    assert len(rows) == 1
    from adapters.crypt import decrypt_from_api

    runner_sec = load_existing_keypair(ws.settings.runner_pub_path)[1]
    value = decrypt_from_api(runner_sec, {
        "alg": rows[0]["alg"],
        "sender_pk": rows[0]["sender_pk"],
        "nonce": rows[0]["nonce"],
        "ciphertext": rows[0]["ciphertext"],
    })
    assert value == "postgres://secret"

    listed = ws.store.list_secrets(tenant["id"], "dl-6")
    assert listed[0]["key"] == "DATABASE_URL"
    assert all(set(("key", "alg", "created_at")) <= set(r) for r in listed)


def test_simulated_executor_mid_probe_delay_is_zero(stack):
    """Guards against slow tests: build delay setting is honored as 0."""
    ws, _runner, _pipeline, _tenant, _deploy, _projection = stack
    assert ws.settings.simulate_build_delay == 0.0


def test_podman_selection_and_simulated_dev_gate():
    from runner.executors.podman import PodmanExecutor

    assert PodmanExecutor().name == "podman"
    assert make_executor(Settings(runner_executor="podman")).name == "podman"
    assert make_builder(Settings(runner_executor="podman")).name == "podman"
    assert make_executor(Settings(runner_executor="simulated")).name == "simulated"
    assert make_builder(Settings(runner_executor="simulated")).name == "simulated"


@pytest.mark.parametrize("factory", [make_executor, make_builder])
def test_simulated_rejected_in_prod(factory):
    with pytest.raises(SystemExit):
        factory(Settings(env="prod", runner_executor="simulated"))


def test_builder_port_smoke():
    from runner.builders.real import RealBuilder

    assert RealBuilder().name == "podman"


def test_carrier_detection_writes_dockerfile(tmp_path):
    from runner.detect import CARRIER_DOCKERFILE, carrier_detection

    plan_dir = tmp_path / "a" / "plan"
    det = carrier_detection(plan_dir)
    assert det.stack == "carrier"
    assert det.port_hint is None
    assert det.plan_dir == plan_dir
    dockerfile = plan_dir / "Dockerfile"
    assert dockerfile.read_text() == CARRIER_DOCKERFILE
    assert "FROM python" in CARRIER_DOCKERFILE


def test_clone_returns_absolute_source_path(stack, tmp_path, monkeypatch):
    """Regression: a relative data_dir must still yield an absolute source dir,
    else railpack joins its cwd with the relative arg and doubles the path."""
    _ws, _runner, pipeline, _tenant, _deploy, _projection = stack
    monkeypatch.chdir(tmp_path)
    pipeline.settings.data_dir = "reldata"
    src = pipeline._clone("abs-1", "demo")
    assert src.is_absolute()
    assert (src / "server.js").exists()


def test_podman_unsupported_repo_falls_back_to_carrier(stack, tmp_path, monkeypatch):
    """No supported stack (Railpack verdict) → carrier container carrying the
    source: lands running with no web URL/port, and no probe is attempted."""
    import runner.pipeline as pmod
    from core.errors import UnsupportedStack

    ws, runner, pipeline, tenant, deploy, projection = stack
    ws.store.set_config("policy", {**ws.store.get_config("policy", {}),
                                   "allow_all": True})

    def _no_plan(_src, _log, *, plan_dir):
        raise UnsupportedStack("no supported stack")

    monkeypatch.setattr(pmod, "railpack_detect", _no_plan)
    pipeline.executor.name = "podman"  # take the real-detection branch
    repo = _local_git_repo(tmp_path / "carrier-target")
    deploy("car-1", repo=repo)

    p = projection("car-1")
    assert p["status"] == "running"
    assert p["phase"] == "running"
    assert p["mapped_port"] is None
    assert p["public_url"] is None
    started = [e for e in ws.store.read_events(tenant_id=tenant["id"], limit=50)
               if e["type"] == ev.INSTANCE_STARTED]
    assert started and started[-1]["meta"].get("carrier") is True


def _local_git_repo(path) -> str:
    """A git repo with no recognizable source files → UnsupportedStack."""
    path.mkdir()
    (path / "README.md").write_text("nothing to detect")
    subprocess.run(["git", "-C", str(path), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(path), "-c", "user.name=Test",
                    "-c", "user.email=test@portal.test", "commit", "-q",
                    "-m", "init"], check=True)
    return str(path)


def test_unsupported_stack_fails_cleanly(stack, tmp_path):
    """Fix A: UnsupportedStack from detect must fail gracefully, not crash the
    runner with NameError and leave a zombie building projection."""
    ws, runner, pipeline, tenant, deploy, projection = stack
    ws.store.set_config("policy", {**ws.store.get_config("policy", {}),
                                   "allow_all": True})
    repo = _local_git_repo(tmp_path / "unsupported")
    deploy("ua-1", repo=repo)
    p = projection("ua-1")
    assert p["status"] == "failed"
    assert p["reason"] == st.REASON_UNKNOWN_STACK
    types = [e["type"] for e in ws.store.read_events(tenant_id=tenant["id"], limit=30)]
    assert ev.UNSUPPORTED_APP in types
    unsupported = [e for e in ws.store.read_events(tenant_id=tenant["id"], limit=30)
                   if e["type"] == ev.UNSUPPORTED_APP][0]
    assert unsupported["meta"].get("detector") == "heuristic"
    assert pipeline.instances.get("ua-1") is None


@pytest.mark.parametrize("zombie_status", [st.BUILDING, st.STARTING])
def test_stop_on_stale_building_reaps_cleanly(stack, zombie_status):
    """Fix B: a stop dispatched while the projection is stuck in
    building/starting must reap it, not raise BuildInterrupted out of stop()."""
    ws, runner, pipeline, tenant, _deploy, projection = stack
    ws.store.create_deployment("br-1", tenant["id"], "demo", None)
    runner.set_projection("br-1", status=zombie_status, phase="building")
    ws.store.enqueue_command("stop", tenant["id"], "br-1", "tester")
    cmd = runner.claim_command()
    pipeline.dispatch(cmd)  # must not raise
    runner.finish_command(cmd["id"], "done")
    p = projection("br-1")
    assert p["status"] == "failed"
    assert p["reason"] == st.REASON_KILLED
    types = [e["type"] for e in ws.store.read_events(tenant_id=tenant["id"], limit=30)]
    assert ev.BUILD_FAILED in types
    assert ev.INSTANCE_FAILED in types


# --- TODO 16: phantom (orphan) rows and the delete feature -------------------

def _backdate(runner, slug, ts: str) -> None:
    runner._exec("UPDATE deployments SET created_at = ? WHERE id = ?", (ts, slug))


def test_sweep_orphans_deletes_only_old_unclaimed(stack):
    ws, runner, pipeline, tenant, _deploy, projection = stack
    for slug in ("ph-old", "ph-recent", "ph-commanded", "ph-claimed"):
        ws.store.create_deployment(slug, tenant["id"], "demo", None)
    _backdate(runner, "ph-old", "2026-01-01 00:00:00")
    _backdate(runner, "ph-commanded", "2026-01-01 00:00:00")
    _backdate(runner, "ph-claimed", "2026-01-01 00:00:00")
    ws.store.enqueue_command("deploy", tenant["id"], "ph-commanded", "tester",
                             {"repo_url": "demo"})
    runner.set_projection("ph-claimed", status="running", phase="running")

    assert pipeline.sweep_orphans() == 1  # only ph-old has no claim and is stale

    assert ws.store.get_projection("ph-old") is None                       # gone
    assert ws.store.get_deployment("ph-old") is None                       # gone
    assert projection("ph-recent")["status"] == "submitted"              # too fresh
    assert projection("ph-commanded") is not None                        # has a command
    assert projection("ph-claimed")["status"] == "running"               # has a projection
    types = [e["type"] for e in ws.store.read_events(tenant_id=tenant["id"], limit=30)]
    assert ev.DEPLOYMENT_DELETED in types


def test_sweep_orphans_is_idempotent(stack):
    ws, runner, pipeline, tenant, _deploy, _projection = stack
    ws.store.create_deployment("ph-1", tenant["id"], "demo", None)
    _backdate(runner, "ph-1", "2026-01-01 00:00:00")

    assert pipeline.sweep_orphans() == 1
    assert pipeline.sweep_orphans() == 0
    assert ws.store.get_projection("ph-1") is None


def test_delete_command_removes_deployment(stack):
    ws, runner, pipeline, tenant, deploy, projection = stack
    deploy("del-1", repo="demo")
    assert projection("del-1")["status"] == "running"

    ws.store.enqueue_command("stop", tenant["id"], "del-1", "tester")
    cmd = runner.claim_command()
    pipeline.dispatch(cmd)
    runner.finish_command(cmd["id"], "done")
    assert projection("del-1")["status"] == "stopped"

    ws.store.enqueue_command("delete", tenant["id"], "del-1", "tester")
    cmd = runner.claim_command()
    pipeline.dispatch(cmd)
    runner.finish_command(cmd["id"], "done")

    assert ws.store.get_projection("del-1") is None
    assert ws.store.get_deployment("del-1") is None
    assert pipeline.instances.get("del-1") is None
    types = [e["type"] for e in ws.store.read_events(tenant_id=tenant["id"], limit=30)]
    assert ev.DEPLOYMENT_DELETED in types


def test_delete_command_stops_running_instance_first(stack):
    ws, runner, pipeline, tenant, deploy, projection = stack
    deploy("del-run", repo="demo")
    assert "del-run" in pipeline.instances

    ws.store.enqueue_command("delete", tenant["id"], "del-run", "tester")
    cmd = runner.claim_command()
    pipeline.dispatch(cmd)
    runner.finish_command(cmd["id"], "done")

    assert ws.store.get_projection("del-run") is None
    assert pipeline.instances.get("del-run") is None


def test_delete_command_on_missing_deployment_is_noop(stack):
    ws, runner, pipeline, tenant, _deploy, _projection = stack
    ws.store.create_deployment("del-missing", tenant["id"], "demo", None)
    runner.set_projection("del-missing", status="stopped", phase="stopped")
    runner.delete_deployment("del-missing")  # simulate the rows already gone

    ws.store.enqueue_command("delete", tenant["id"], "del-missing", "tester")
    cmd = runner.claim_command()
    pipeline.dispatch(cmd)  # must not raise
    runner.finish_command(cmd["id"], "done")


# --- TODO 9 + 11: image lifecycle / cold-start audit -------------------------

def test_cold_start_missing_image_rebuilds_with_audit(stack):
    """TODO 11: a cold start with no persisted image on an image-requiring
    executor is an audited re-deploy (AD-12): DEPLOYMENT_REDEPLOYED and
    build.started both carry reason=image_missing; the deployment lands
    running again."""
    ws, runner, pipeline, tenant, _deploy, projection = stack
    pipeline.executor = ImageRequiringExecutor()
    ws.store.create_deployment("cs-miss", tenant["id"], "demo", None)
    runner.set_projection("cs-miss", status="stopped", phase="stopped")

    ws.store.enqueue_command("start", tenant["id"], "cs-miss", "tester")
    cmd = runner.claim_command()
    pipeline.dispatch(cmd)
    runner.finish_command(cmd["id"], "done")

    p = projection("cs-miss")
    assert p["status"] == "running"
    events = ws.store.read_events(tenant_id=tenant["id"], limit=50)
    redeployed = [e for e in events if e["type"] == ev.DEPLOYMENT_REDEPLOYED]
    assert redeployed and redeployed[-1]["meta"].get("reason") == st.REASON_IMAGE_MISSING
    builds = [e for e in events if e["type"] == ev.BUILD_STARTED]
    assert builds and builds[-1]["meta"].get("reason") == st.REASON_IMAGE_MISSING


def test_cold_start_with_persisted_image_skips_rebuild(stack):
    """TODO 11: a persisted image makes cold start a plain start — no
    build.started, no redeploy marker, and advertise keeps the image_ref."""
    ws, runner, pipeline, tenant, _deploy, projection = stack
    pipeline.executor = ImageRequiringExecutor()
    ws.store.create_deployment("cs-ok", tenant["id"], "demo", None)
    runner.set_projection("cs-ok", status="stopped", phase="stopped",
                          image_ref="portal/cs-ok:latest")

    ws.store.enqueue_command("start", tenant["id"], "cs-ok", "tester")
    cmd = runner.claim_command()
    pipeline.dispatch(cmd)
    runner.finish_command(cmd["id"], "done")

    p = projection("cs-ok")
    assert p["status"] == "running"
    assert p["image_ref"] == "portal/cs-ok:latest"
    events = ws.store.read_events(tenant_id=tenant["id"], limit=50)
    assert not [e for e in events if e["type"] == ev.BUILD_STARTED]
    assert not [e for e in events if e["type"] == ev.DEPLOYMENT_REDEPLOYED]


# --- TODO 15: an unexpected dispatch crash must not leave a zombie ----------

def test_unexpected_dispatch_crash_marks_projection_failed(stack):
    from runner.main import _process

    ws, runner, pipeline, tenant, _deploy, projection = stack
    ws.store.create_deployment("crash-a", tenant["id"], "demo", None)
    runner.set_projection("crash-a", status="building", phase="building")
    ws.store.enqueue_command("deploy", tenant["id"], "crash-a", "tester",
                             {"repo_url": "demo"})
    cmd = runner.claim_command()

    def boom(_cmd):
        raise RuntimeError("kaboom")

    pipeline.dispatch = boom
    _process(runner, pipeline, cmd)  # must not re-raise

    p = projection("crash-a")
    assert p["status"] == "failed"
    assert p["reason"] == st.REASON_BUILD_ERROR
    status = runner._one("SELECT status FROM commands WHERE id = ?", (cmd["id"],))
    assert status["status"] == "failed"


def test_loop_continues_after_commanded_crash(stack):
    from runner.main import _process

    ws, runner, pipeline, tenant, _deploy, projection = stack
    real_dispatch = pipeline.dispatch

    def flaky(cmd):
        if cmd["deployment_id"] == "crash-b":
            raise RuntimeError("kaboom")
        return real_dispatch(cmd)

    for slug in ("crash-b", "ok-b"):
        ws.store.create_deployment(slug, tenant["id"], "demo", None)
        runner.set_projection(slug, status="submitted", phase="submitted")
        ws.store.enqueue_command("deploy", tenant["id"], slug, "tester", {"repo_url": "demo"})

    pipeline.dispatch = flaky
    c1 = runner.claim_command()
    _process(runner, pipeline, c1)
    c2 = runner.claim_command()
    _process(runner, pipeline, c2)

    assert projection("crash-b")["status"] == "failed"
    c1_row = runner._one("SELECT status FROM commands WHERE id = ?", (c1["id"],))
    assert c1_row["status"] == "failed"
    assert projection("ok-b")["status"] == "running"  # the loop moved on
    c2_row = runner._one("SELECT status FROM commands WHERE id = ?", (c2["id"],))
    assert c2_row["status"] == "done"


# --- halt / resume symmetry (bug #4) ---------------------------------------
#
# `POST /halt` stops the running instances (ops/RUNBOOK.md §5); `halt/clear`
# must therefore restart exactly those it stopped. Selection is by the
# `halted_by_operator` reason — a manual stop after a halt is not resumed, and
# an interrupted *build* lands failed/halted_by_operator and is not silently
# rebuilt.

def _events(ws, type_):
    """Newest-first platform-wide events of one type. Operator halt/resume
    events are not tenant-scoped (tenant_id 0), so they are read unfiltered."""
    return [e for e in ws.store.read_events(limit=200) if e["type"] == type_]


def _halt(stack, reason="maintenance"):
    """Enqueue + dispatch one platform halt, as the runner loop would."""
    ws, runner, pipeline, tenant, _deploy, _projection = stack
    ws.store.set_config("policy", {**ws.store.get_config("policy", {}),
                                   "halted": True, "halt_reason": reason})
    ws.store.enqueue_command("halt", tenant["id"], "", "ops@portal.test")
    while runner.pending_count():
        cmd = runner.claim_command()
        pipeline.dispatch(cmd)
        runner.finish_command(cmd["id"], "done")


def _resume(stack, tenant_id):
    """Enqueue + dispatch the resume command."""
    ws, runner, pipeline, _t, _d, _p = stack
    ws.store.set_config("policy", {**ws.store.get_config("policy", {}),
                                   "halted": False, "halt_reason": None})
    ws.store.enqueue_command("resume", tenant_id, "", "ops@portal.test")
    cmd = runner.claim_command()
    pipeline.dispatch(cmd)
    runner.finish_command(cmd["id"], "done")
    return cmd


def test_halt_stops_running_instances_with_the_halt_reason(stack):
    """The precondition resume relies on: halt drains, recording the reason."""
    ws, _runner, _pipeline, tenant, deploy, projection = stack
    deploy("rs-1", repo="demo")
    assert projection("rs-1")["status"] == "running"

    _halt(stack)

    p = projection("rs-1")
    assert p["status"] == "stopped"
    assert p["reason"] == st.REASON_OPERATOR_HALT
    effective = _events(ws, ev.OPERATOR_HALT_EFFECTIVE)
    assert len(effective) == 1  # exactly once per effective halt (RUNBOOK §5)


def test_resume_restarts_the_instances_the_halt_stopped(stack):
    ws, _runner, _pipeline, tenant, deploy, projection = stack
    deploy("rs-2", repo="demo")
    first_port = projection("rs-2")["mapped_port"]
    _halt(stack)
    assert projection("rs-2")["mapped_port"] is None  # the halt tore the run down

    _resume(stack, tenant["id"])

    p = projection("rs-2")
    assert p["status"] == "running"
    assert p["reason"] is None
    assert p["mapped_port"] and p["mapped_port"] != first_port  # a real new run
    resumed = _events(ws, ev.OPERATOR_RESUME_EFFECTIVE)
    assert len(resumed) == 1
    assert resumed[0]["meta"]["restarted"] == ["rs-2"]
    assert resumed[0]["meta"]["failed"] == []


def test_resume_selection_is_scoped_to_the_halt_reason(stack):
    """A manual stop, an idle stop and an interrupted build are all left alone."""
    ws, runner, _pipeline, tenant, deploy, projection = stack
    for slug in ("rs-manual", "rs-idle", "rs-build"):
        deploy(slug, repo="demo")
    runner.set_projection("rs-manual", status="stopped", reason=st.REASON_MANUAL,
                          phase="stopped")
    runner.set_projection("rs-idle", status="stopped", reason=st.REASON_IDLE,
                          phase="stopped")
    # what _interrupted() writes when a halt kills an in-flight build
    runner.set_projection("rs-build", status="failed", reason=st.REASON_OPERATOR_HALT,
                          phase="failed")
    _halt(stack)

    _resume(stack, tenant["id"])

    for slug in ("rs-manual", "rs-idle"):
        assert projection(slug)["status"] == "stopped", slug
    # the interrupted build stays failed — resume does not silently rebuild it
    assert projection("rs-build")["status"] == "failed"
    assert _events(ws, ev.OPERATOR_RESUME_EFFECTIVE)[0]["meta"]["restarted"] == []


def test_resume_preserves_the_stored_port_override(stack, monkeypatch):
    """PodmanExecutor publishes ports[0] as the container port, so a lost
    override would silently serve the wrong port inside the container."""
    from runner.pipeline import Pipeline

    ws, runner, _pipeline, tenant, _deploy, projection = stack
    ws.store.create_deployment("rs-port", tenant["id"], "demo", 4321)
    runner.set_projection("rs-port", status="stopped",
                          reason=st.REASON_OPERATOR_HALT, phase="stopped")

    seen: dict[str, dict] = {}
    real = Pipeline.start

    def spy(self, cmd):
        seen[cmd["deployment_id"]] = cmd
        return real(self, cmd)

    monkeypatch.setattr(Pipeline, "start", spy)

    _resume(stack, tenant["id"])

    assert seen["rs-port"]["payload"]["port_override"] == 4321
    assert projection("rs-port")["status"] == "running"


def test_resume_is_best_effort_and_reports_both_outcomes(stack, monkeypatch):
    """One instance failing to start must not abort the rest of the resume."""
    from runner.pipeline import Pipeline

    ws, runner, _pipeline, tenant, deploy, projection = stack
    deploy("rs-good", repo="demo")
    deploy("rs-bad", repo="demo")
    for slug in ("rs-good", "rs-bad"):
        runner.set_projection(slug, status="stopped", reason=st.REASON_OPERATOR_HALT,
                              phase="stopped")

    real = Pipeline.start

    def flaky(self, cmd):
        if cmd["deployment_id"] == "rs-bad":
            raise RuntimeError("no image")
        return real(self, cmd)

    monkeypatch.setattr(Pipeline, "start", flaky)

    _resume(stack, tenant["id"])

    assert projection("rs-good")["status"] == "running"
    assert projection("rs-bad")["status"] == "stopped"
    meta = _events(ws, ev.OPERATOR_RESUME_EFFECTIVE)[0]["meta"]
    assert meta["restarted"] == ["rs-good"]
    assert meta["failed"] == ["rs-bad"]


def test_second_resume_is_a_no_op(stack):
    ws, _runner, _pipeline, tenant, deploy, projection = stack
    deploy("rs-once", repo="demo")
    _halt(stack)
    _resume(stack, tenant["id"])
    _resume(stack, tenant["id"])  # idempotent

    assert projection("rs-once")["status"] == "running"
    assert _events(ws, ev.OPERATOR_RESUME_EFFECTIVE)[0]["meta"]["restarted"] == []


def test_start_while_halted_is_refused_without_mutating_status(stack):
    """Containment: a halted platform must not accept a cold start. The refusal
    must NOT mark the Deployment `denied` — that is terminal (no outgoing
    transitions), which would brick it after the halt clears."""
    ws, runner, pipeline, tenant, _deploy, projection = stack
    ws.store.create_deployment("rs-gate", tenant["id"], "demo", None)
    runner.set_projection("rs-gate", status="stopped", reason=st.REASON_MANUAL,
                          phase="stopped", image_ref="portal/rs-gate:latest")
    ws.store.set_config("policy", {**ws.store.get_config("policy", {}),
                                   "halted": True, "halt_reason": "maintenance"})

    ws.store.enqueue_command("start", tenant["id"], "rs-gate", "tester")
    cmd = runner.claim_command()
    pipeline.dispatch(cmd)
    runner.finish_command(cmd["id"], "done")

    p = projection("rs-gate")
    assert p["status"] == "stopped"
    assert p["reason"] == st.REASON_MANUAL
    started = [e for e in ws.store.read_events(tenant_id=tenant["id"], limit=50)
               if e["type"] == ev.INSTANCE_STARTED]
    assert not started
    assert cmd["id"]  # the command was consumed, not left pending


def test_start_is_allowed_again_after_the_halt_clears(stack):
    """The other half of the gate: clearing the halt unblocks a start."""
    ws, runner, pipeline, tenant, _deploy, projection = stack
    ws.store.create_deployment("rs-open", tenant["id"], "demo", None)
    runner.set_projection("rs-open", status="stopped", reason=st.REASON_MANUAL,
                          phase="stopped", image_ref="portal/rs-open:latest")
    ws.store.set_config("policy", {**ws.store.get_config("policy", {}),
                                   "halted": True, "halt_reason": "m"})
    ws.store.enqueue_command("start", tenant["id"], "rs-open", "tester")
    cmd = runner.claim_command()
    pipeline.dispatch(cmd)
    runner.finish_command(cmd["id"], "done")
    assert projection("rs-open")["status"] == "stopped"

    ws.store.set_config("policy", {**ws.store.get_config("policy", {}),
                                   "halted": False, "halt_reason": None})
    ws.store.enqueue_command("start", tenant["id"], "rs-open", "tester")
    cmd2 = runner.claim_command()
    pipeline.dispatch(cmd2)
    runner.finish_command(cmd2["id"], "done")

    assert projection("rs-open")["status"] == "running"


def test_enforce_halt_does_not_reflood_halt_effective(stack):
    """RUNBOOK §5: `operator.halt_effective` appears exactly once per effective
    halt. Once drained, the loop's enforce_halt must stay quiet."""
    ws, _runner, pipeline, tenant, deploy, _projection = stack
    deploy("rs-flood", repo="demo")
    _halt(stack)

    for _ in range(3):
        pipeline.enforce_halt()

    assert len(_events(ws, ev.OPERATOR_HALT_EFFECTIVE)) == 1


def test_halt_aborts_pending_commands_and_resume_does_not_revive_them(stack):
    """Decided contract: halted-away commands stay aborted (auditable), rather
    than being silently re-queued on resume."""
    ws, runner, pipeline, tenant, deploy, _projection = stack
    deploy("rs-abort", repo="demo")
    ws.store.set_config("policy", {**ws.store.get_config("policy", {}),
                                   "halted": True, "halt_reason": "maintenance"})
    # the halt command is claimed first (lower id) and drains the queue, taking
    # the still-pending delete with it
    ws.store.enqueue_command("halt", tenant["id"], "", "ops@portal.test")
    ws.store.enqueue_command("delete", tenant["id"], "rs-abort", "tester")
    cmd = runner.claim_command()
    assert cmd["type"] == "halt"
    pipeline.dispatch(cmd)
    runner.finish_command(cmd["id"], "done")

    row = runner._one("SELECT status FROM commands WHERE type = 'delete' "
                      "ORDER BY id DESC LIMIT 1")
    assert row["status"] == "aborted"
    _resume(stack, tenant["id"])
    row = runner._one("SELECT status FROM commands WHERE type = 'delete' "
                      "ORDER BY id DESC LIMIT 1")
    assert row["status"] == "aborted"