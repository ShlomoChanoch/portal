"""The runner loop: poll the command queue, dispatch to the pipeline,
reconcile instances, reap idle, and beat a health heartbeat into the config.

Run as `python -m runner.main`. Fails fast when it can't reach the store; then
retries. The api never talks to the runtime directly (AD-1); this process is
the only bridge.
"""

from __future__ import annotations

import logging
import time

from adapters.crypt import load_existing_keypair
from adapters.logpersist import LogStore
from adapters.store import Store
from api.config import Settings
from core import events as ev
from core import state as st
from runner.keys import load_runner_keypair
from runner.pipeline import Pipeline
from runner.reaper import Reaper

log = logging.getLogger("runner")


def make_executor(settings: Settings):
    if settings.runner_executor == "podman":
        from runner.executors.podman import PodmanExecutor

        return PodmanExecutor()
    if settings.runner_executor == "simulated":
        if settings.is_prod:
            raise SystemExit(
                "PORTAL_ENV=prod requires runner executor 'podman'; "
                "'simulated' is dev-only."
            )
        from runner.executors.simulated import SimulatedExecutor

        return SimulatedExecutor()
    raise SystemExit(f"unknown runner executor: {settings.runner_executor}")


def make_builder(settings: Settings):
    if settings.runner_executor == "podman":
        from runner.builders.real import RealBuilder

        return RealBuilder()
    if settings.runner_executor == "simulated":
        if settings.is_prod:
            raise SystemExit(
                "PORTAL_ENV=prod requires runner executor 'podman'; "
                "'simulated' is dev-only."
            )
        from runner.builders.simulated import SimulatedBuilder

        return SimulatedBuilder()
    raise SystemExit(f"unknown runner executor: {settings.runner_executor}")


def _heartbeat(store, executor_name: str, builder_name: str | None = None) -> None:
    from datetime import datetime, timezone

    store.set_config("runner:heartbeat", {
        "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "executor": executor_name,
        "builder": builder_name,
        "pid": str(__import__("os").getpid()),
    })


def _mark_crashed(pipeline, store, cmd) -> None:
    """TODO 15: an unexpected dispatch crash can leave the projection stuck in
    a transient state — a quota-consuming zombie. Mark it failed unless it is
    still genuinely running (never falsely kill a live instance)."""
    slug = cmd.get("deployment_id") or ""
    if not slug:
        return
    try:
        row = store.get_projection(slug)
    except Exception:
        return
    if not row or row.get("status") not in (st.SUBMITTED, st.BUILDING, st.STARTING):
        return
    try:
        pipeline.set(slug, status=st.FAILED, reason=st.REASON_BUILD_ERROR, phase="failed")
        pipeline.emit(slug, ev.BUILD_FAILED, meta={"reason": st.REASON_BUILD_ERROR})
        pipeline.emit(slug, ev.INSTANCE_FAILED, meta={"reason": st.REASON_BUILD_ERROR})
        log.warning("marked %s failed after command %s crashed", slug, cmd.get("id"))
    except Exception:
        log.exception("could not mark %s failed after crash", slug)


def _process(store, pipeline, cmd) -> None:
    """Claim a command, dispatch it, and record the outcome. An unexpected
    crash finishes the command failed and reaps the projection (no zombie)."""
    log.info("command %s %s (%s)", cmd["id"], cmd["type"], cmd["deployment_id"])
    try:
        pipeline.dispatch(cmd)
        store.finish_command(cmd["id"], "done")
    except Exception:
        log.exception("command %s crashed", cmd["id"], exc_info=True)
        store.finish_command(cmd["id"], "failed")
        _mark_crashed(pipeline, store, cmd)


def run(single_shot: bool = False, poll_seconds: float = 1.0) -> None:
    settings = Settings()
    logging.basicConfig(level=logging.INFO)
    if settings.is_prod and settings.runner_executor != "podman":
        raise SystemExit(
            f"PORTAL_ENV=prod requires runner executor 'podman', "
            f"got {settings.runner_executor!r}"
        )

    Store(settings.db_path, mode="admin").close()
    store = Store(settings.db_path, mode="executor")
    logs = LogStore(settings.log_root)

    if settings.is_prod:
        # ops/ provisions keys with restricted ownership; do not auto-generate.
        _, runner_sec = load_existing_keypair(settings.runner_pub_path)
    else:
        from api.config import ensure_crypto

        ensure_crypto(settings)
        runner_sec = load_runner_keypair(settings)[1]
    runner_pub = b""  # runner needs only its private half to decrypt (AD-9)

    executor = make_executor(settings)
    builder = make_builder(settings)
    pipeline = Pipeline(settings, store, logs, runner_sec, runner_pub, executor, builder)
    reaper = Reaper(store, pipeline)

    log.info("runner up (executor=%s, data=%s)", executor.name, settings.data_dir)

    while True:
        try:
            pipeline.reconcile()
            reaper.tick()
            pipeline.sweep_orphans()
            if store.get_config("policy", {}).get("halted"):
                pipeline.enforce_halt()
            pipeline.refresh_idle_markers()
            cmd = store.claim_command()
            if cmd is not None:
                _process(store, pipeline, cmd)
            else:
                time.sleep(poll_seconds)
        except KeyboardInterrupt:
            log.info("runner stopped.")
            break
        except Exception:
            log.exception("runner loop error; sleeping", exc_info=True)
            time.sleep(poll_seconds * 4)
        finally:
            try:
                _heartbeat(store, executor.name, builder.name)
            except Exception:
                pass
        if single_shot:
            break


if __name__ == "__main__":
    run()