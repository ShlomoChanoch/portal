"""The lifecycle pipeline (AD-2/AD-3): what the runner actually does.

Every command is dispatchable inside a per-command loop. Stages for a Deploy:
    clone → detect → build → run → probe → advertise
Cold start skips clone/build (AD-12). Stop honours the phase it finds; halt is
the one control that stops the world.

Single-writer rule: only this module calls store.set_projection.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from adapters.crypt import decrypt_from_api
from adapters.logpersist import LogStore, register_secret_value
from adapters.proxy import resolve_public_url
from api.config import Settings
from core import events as ev
from core import state as st
from core.errors import UnsupportedStack
from core.url import candidate_ports
from runner.build import BuildFailed
from runner.detect import Detection, carrier_detection, detect, railpack_detect
from runner.executors.base import Instance
from runner.probe import probe_candidates, probe_port

log = logging.getLogger("runner")

DEMO_SOURCE = Path(__file__).resolve().parent.parent / "dev" / "demo-repo"


class BuildInterrupted(Exception):
    def __init__(self, category: str):
        self.category = category  # "stop" | "halt"


class AdmissionDenied(Exception):
    def __init__(self, reason: str, code: str):
        self.reason, self.code = reason, code


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class Pipeline:
    def __init__(self, settings: Settings, store, logs: LogStore,
                 runner_sec: bytes, runner_pub: bytes, executor, builder):
        self.settings = settings
        self.store = store
        self.logs = logs
        self.runner_sec = runner_sec
        self.runner_pub = runner_pub
        self.executor = executor
        self.builder = builder
        self.instances: dict[str, Instance] = {}

    # -- helpers ------------------------------------------------------------

    def log(self, slug: str, stream: str, text: str) -> None:
        row = self.store.get_projection(slug)
        tenant_id = row["tenant_id"] if row else 0
        self.logs.append(tenant_id, slug, stream, text)

    def emit(self, slug: str, type_: str, actor: str = "system",
             meta: dict | None = None) -> None:
        row = self.store.get_projection(slug)
        tenant_id = row["tenant_id"] if row else 0
        self.store.append_event(tenant_id, slug, type_, actor, meta or {})

    def set(self, slug: str, **kw) -> None:
        self.store.set_projection(slug, **kw)

    def _preempt(self, slug: str) -> None:
        if self.store.has_preemptive_signal(slug, "halt"):
            raise BuildInterrupted("halt")
        if self.store.has_preemptive_signal(slug, "stop"):
            raise BuildInterrupted("stop")

    def _admit(self, repo_url: str) -> None:
        """AD-8: runner re-checks the double gate before consuming a build
        (admission at the api is not trusted by the builder)."""
        cfg = self.store.get_config("policy", {}) or {}
        if cfg.get("halted"):
            raise AdmissionDenied("Portal is halted — operator paused new builds and runs.",
                                  "platform_halted")
        if repo_url == "demo":
            return
        from core import policy as pol

        try:
            pol.check_allowlist(cfg, repo_url)
        except Exception as exc:
            code = getattr(exc, "code", "denied")
            raise AdmissionDenied(getattr(exc, "message", "Not allowed by operator policy."),
                                  code) from exc

    # -- stage helpers ------------------------------------------------------

    def _clone(self, slug: str, repo_url: str) -> Path:
        # Resolve so `src` is absolute: railpack joins its cwd with a relative
        # DIRECTORY argument, which otherwise doubles the sandbox path.
        root = (Path(self.settings.data_dir) / "builds").resolve()
        root.mkdir(parents=True, exist_ok=True)
        sandbox = root / f"{slug}-{uuid.uuid4().hex[:8]}"
        src = sandbox / "src"
        if repo_url == "demo":
            self.log(slug, "build", "Using the bundled demo repository (dev).")
            shutil.copytree(DEMO_SOURCE, src)
            return src
        self.log(slug, "build", f"cloning {repo_url}")
        r = subprocess.run(
            ["git", "clone", "--depth", "1", "--quiet", repo_url, str(src)],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            detail = (r.stderr.strip() or "")[:200]
            raise BuildFailed(
                "Could not clone the repo — is the URL correct and public?" +
                (f" {detail}" if detail else "")
            )
        self.log(slug, "build", "clone complete.")
        return src

    def _secrets_env(self, tenant_id: int, slug: str) -> dict:
        env: dict[str, str] = {}
        for row in self.store.list_secret_payloads(tenant_id, slug):
            try:
                value = decrypt_from_api(self.runner_sec, {
                    "alg": row["alg"],
                    "sender_pk": row["sender_pk"],
                    "nonce": row["nonce"],
                    "ciphertext": row["ciphertext"],
                })
            except Exception:
                self.log(slug, "run", f"secret '{row['key']}' could not be decrypted — skipped.")
                continue
            env[row["key"]] = value
            register_secret_value(value)
        return env

    def _stop_instance(self, slug: str, reason: str, line: str) -> None:
        inst = self.instances.pop(slug, None)
        if inst:
            try:
                self.executor.stop(inst)
            except Exception as exc:
                self.log(slug, "run", f"stop errored: {exc}")
        self.set(slug, status=st.STOPPED, reason=reason, phase="stopped",
                 mapped_port=None, idle_stop_at=None)
        self.emit(slug, ev.INSTANCE_STOPPED, meta={"reason": reason})
        self.log(slug, "run", line)

    # -- command dispatch ----------------------------------------------------

    def dispatch(self, cmd: dict) -> None:
        type_ = cmd["type"]
        slug = cmd["deployment_id"]
        if type_ in ("deploy", "redeploy"):
            self.deploy(cmd)
        elif type_ == "start":
            self.start(cmd)
        elif type_ == "stop":
            self.stop(cmd, force=False)
        elif type_ == "force_stop":
            self.stop(cmd, force=True)
        elif type_ == "delete":
            self.delete(cmd)
        elif type_ == "halt":
            self.halt_all(actor=cmd.get("actor") or "operator")
        elif type_ == "resume":
            self.resume_all(actor=cmd.get("actor") or "operator")

    # -- deploy / redeploy ---------------------------------------------------

    def deploy(self, cmd: dict, *, reason: str | None = None) -> None:
        slug, tenant_id = cmd["deployment_id"], cmd["tenant_id"]
        row = self.store.get_projection(slug) or {}
        repo_url = (cmd.get("payload") or {}).get("repo_url") or row.get("repo_url")
        if not repo_url:
            self._fail(slug, st.REASON_BUILD_ERROR, "No repo URL for this Deployment.", "build")
            return
        was_running = row.get("status") == st.RUNNING
        try:
            self._admit(repo_url)
            port = self._build_and_start(cmd, slug, tenant_id, repo_url, was_running,
                                         reason=reason)
            if was_running:
                self.emit(slug, ev.DEPLOYMENT_REDEPLOYED, meta={"repo_url": repo_url})
        except BuildInterrupted as bi:
            self._interrupted(slug, bi.category)
        except AdmissionDenied as ad:
            self.log(slug, "run", f"Not started: {ad.reason}")
            self.set(slug, status=st.DENIED, reason=ad.code, phase="denied")
            self.emit(slug, ev.DEPLOY_DENIED, meta={"reason": ad.code})
        except BuildFailed as exc:
            self._fail(slug, st.REASON_BUILD_ERROR, str(exc), "build")
        except UnsupportedStack as exc:
            # Railpack owns detection in prod; the heuristic is the dev
            # fallback. A failed plan is an honest unsupported-stack result.
            self._fail(slug, st.REASON_UNKNOWN_STACK, str(exc), "build")
            detector = "railpack" if self.executor.name == "podman" else "heuristic"
            self.emit(slug, ev.UNSUPPORTED_APP, meta={"detector": detector})
        except Exception as exc:  # never let a crash kill the runner loop
            log.exception("deploy %s crashed", slug, exc_info=exc)
            self._fail(slug, st.REASON_BUILD_ERROR,
                       f"Build crashed internally: {exc}", "build")

    def _build_and_start(self, cmd, slug, tenant_id, repo_url, was_running,
                         reason: str | None = None) -> int:
        self.set(slug, status=st.BUILDING, reason=None, phase="building",
                 public_url=None, mapped_port=None)
        meta = {"repo_url": repo_url}
        if reason:
            meta["reason"] = reason
        self.emit(slug, ev.BUILD_STARTED, meta=meta)
        self.log(slug, "build", f"Building {repo_url}")
        src = self._clone(slug, repo_url)
        self._preempt(slug)
        if self.executor.name == "podman":
            try:
                det = railpack_detect(src, lambda l: self.log(slug, "build", l),
                                      plan_dir=src.parent / "plan")
            except UnsupportedStack as exc:
                # Dev-only escape hatch: no supported stack (the IV-1 Railpack
                # verdict). Build a generic carrier container carrying the
                # cloned source so build/run machinery is still provable; the
                # "repo → serving web app" contract is not changed.
                self.log(slug, "build", f"Railpack could not plan this repo: {exc}")
                self.log(slug, "build", "No supported stack detected — building a "
                                        "carrier container with the repo source.")
                det = carrier_detection(src.parent / "plan")
        else:
            det = detect(src, lambda l: self.log(slug, "build", l))
        self.log(slug, "build", f"Detected: {det.name}.")
        image = self.builder.build(source_dir=src, slug=slug, detect=det,
                              settings=self.settings,
                              log=lambda l: self.log(slug, "build", l))
        self.emit(slug, ev.BUILD_COMPLETED, meta={"stack": det.stack})
        self._preempt(slug)
        self.set(slug, status=st.STARTING, phase="starting")
        self.emit(slug, ev.INSTANCE_STARTING)
        return self._start_and_probe(slug, tenant_id, det, src, image, cmd)

    def start(self, cmd: dict) -> None:
        """Cold start (AD-12): reuse image; nothing persisted → audited
        re-deploy marked with REASON_IMAGE_MISSING (TODO 11)."""
        slug, tenant_id = cmd["deployment_id"], cmd["tenant_id"]
        row = self.store.get_projection(slug)
        if not row:
            self.set(slug, status=st.FAILED, reason=st.REASON_BUILD_ERROR, phase="failed")
            return
        if self.store.get_config("policy", {}).get("halted"):
            # Refuse without touching the projection. Going through _admit would
            # mark the Deployment `denied`, which is terminal (no outgoing
            # transitions, AD-3) and would brick it once the halt clears.
            self.log(slug, "run", "Not started: Portal is halted — "
                                  "operator paused new builds and runs.")
            return
        if row.get("status") in (st.RUNNING, st.BUILDING, st.STARTING):
            self.log(slug, "run", "Already running or starting.")
            return
        image = row.get("image_ref")
        if self.executor.requires_image and not image:
            self.log(slug, "run", "No persisted image found — rebuilding from source (AD-12).")
            self.emit(slug, ev.DEPLOYMENT_REDEPLOYED, actor="system",
                      meta={"repo_url": row.get("repo_url"),
                            "reason": st.REASON_IMAGE_MISSING})
            self.deploy({"type": "redeploy", "deployment_id": slug, "tenant_id": tenant_id,
                         "actor": "system",
                         "payload": {"repo_url": row.get("repo_url"),
                                     "port_override": row.get("port_override")}},
                        reason=st.REASON_IMAGE_MISSING)
            return
        self.set(slug, status=st.STARTING, phase="starting")
        self.emit(slug, ev.INSTANCE_STARTING)
        try:
            det = Detection(row.get("stack") or "static", "Cached", None, [])
            self._start_and_probe(slug, tenant_id, det, None, image, cmd)
        except Exception:
            log.exception("cold start %s failed", slug)
            self._fail(slug, st.REASON_BUILD_ERROR, "Cold start failed.", "run")

    def _start_and_probe(self, slug, tenant_id, det: Detection, src, image_ref, cmd) -> int:
        override = (cmd.get("payload") or {}).get("port_override")
        ports = candidate_ports(override, det.port_hint)
        env = self._secrets_env(tenant_id, slug)
        inst = self.executor.start(
            slug=slug, tenant_id=tenant_id, source_root=str(src) if src else None,
            repo=(self.store.get_projection(slug) or {}).get("repo_url"),
            static=(det.stack == "static"), env=env, ports=ports,
            image_ref=image_ref,
            log_line=lambda l: self.log(slug, "run", l),
        )
        self.instances[slug] = inst
        if det.stack == "carrier":
            # No web service by design — the container idles holding the repo
            # source. Expose it as running without a URL/port (nothing to probe).
            self.set(slug, status=st.RUNNING, reason=None, phase="running",
                     mapped_port=None, public_url=None, image_ref=inst.image_ref)
            self._mark_idle(slug)
            self.emit(slug, ev.INSTANCE_STARTED,
                      meta={"public_url": None, "mapped_port": None, "carrier": True})
            self.log(slug, "run", "Carrier container is up — no web stack yet; "
                                  "use it to run the repo source.")
            return None
        # AD-7: in podman mode only the published host port is routable to the
        # app; probing arbitrary host candidate-ports can false-positive on the
        # control plane itself (e.g. the API on 8000 answers "4xx = alive").
        # A single-shot probe is too early for a just-started container, so retry
        # the published port for a grace window before declaring probe_failed.
        if self.executor.name == "podman":
            order = [inst.port]
            deadline = time.monotonic() + 10
            mapped = None
            while time.monotonic() < deadline:
                if probe_port("127.0.0.1", inst.port):
                    mapped = inst.port
                    break
                time.sleep(1)
        else:
            order = [inst.port, *ports]
            mapped = next((p for p in order if probe_port("127.0.0.1", p)), None)
        if mapped is None:
            self._probe_failed(slug, order, det)
        url = resolve_public_url(slug, mapped, env=self.settings.env,
                                 domain=self.settings.portal_domain,
                                 prod_protocol=self.settings.prod_protocol,
                                 dev_protocol=self.settings.dev_protocol)
        self.advertise(slug, mapped, url, inst)
        return mapped

    def _probe_failed(self, slug, tried, det: Detection) -> None:
        tried = sorted(set(int(p) for p in tried if p and 0 < int(p) < 65536))
        self.set(slug, status=st.FAILED, reason=st.REASON_PROBE_FAILED, phase="failed")
        self.emit(slug, ev.INSTANCE_FAILED,
                  meta={"reason": st.REASON_PROBE_FAILED, "tried": tried})
        self.log(slug, "run",
                 f"No app responded on port(s): {', '.join(map(str, tried)) or 'none'} "
                 f"(expected {det.name}). Add a port override if the app uses one.")

    def advertise(self, slug, mapped_port, url, inst):
        self.set(slug, status=st.RUNNING, reason=None, phase="running",
                 mapped_port=mapped_port, public_url=url, image_ref=inst.image_ref)
        self._mark_idle(slug)
        self.emit(slug, ev.INSTANCE_STARTED, meta={"public_url": url,
                                                   "mapped_port": mapped_port})

    def _mark_idle(self, slug) -> None:
        row = self.store.get_projection(slug) or {}
        last = row.get("last_activity_at") or _now()
        try:
            ts = datetime.fromisoformat(last) + timedelta(seconds=self.settings.idle_stop_seconds)
            self.store.set_idle_marker(slug, ts.isoformat(timespec="milliseconds"))
        except ValueError:
            self.store.set_idle_marker(slug, None)

    # -- stop / halt ---------------------------------------------------------

    def stop(self, cmd: dict, force: bool = False) -> None:
        slug = cmd["deployment_id"]
        row = self.store.get_projection(slug) or {}
        cur = row.get("status")
        if cur in (None, st.STOPPED, st.FAILED, st.DENIED):
            self.log(slug, "run", "Instance was already stopped.")
            return
        if cur in (st.BUILDING, st.STARTING):
            # No build stage is in flight for this slug by the time a stop is
            # dispatched (the runner handles commands serially), so a stale
            # building/starting state here is always a zombie — either left by
            # a crashed command or orphaned by a prior deployment. Reap it
            # cleanly instead of raising (BuildInterrupted is only meaningful
            # inside deploy(), where a concurrent stage polls _preempt).
            self._interrupted(slug, "stop")
            return
        if force:
            self.emit(slug, ev.OPERATOR_FORCE_STOP, actor=cmd.get("actor") or "operator")
        self._stop_instance(slug,
                            st.REASON_OPERATOR_STOP if force else st.REASON_MANUAL,
                            "Instance stopped." if not force else "Instance stopped — forced.")

    def halt_all(self, actor="operator") -> None:
        cfg = self.store.get_config("policy", {}) or {}
        cfg["halted"] = True
        self.store.set_config("policy", cfg)
        self.store.abort_all_pending()
        running = [s["id"] for s in self.store.list_running_projections()]
        for slug in running:
            self._stop_instance(slug, st.REASON_OPERATOR_HALT, "Halted by operator.")
        self.emit("", ev.OPERATOR_HALT_EFFECTIVE, actor=actor, meta={"deployments": running})
        log.info("halt effective over %d instance(s)", len(running))

    def resume_all(self, actor: str = "operator") -> None:
        """The symmetric half of halt_all: restart the instances the halt
        stopped, and nothing else (ops/RUNBOOK.md §5).

        Selection is the `halted_by_operator` reason on a `stopped` projection,
        so a stop the operator or a tenant issued *after* the halt is not
        resurrected. A build the halt interrupted is left alone: it is `failed`,
        not `stopped`, and silently rebuilding it would run work the tenant
        never saw succeed.

        Each restart is an AD-12 cold start (persisted image; an audited rebuild
        only if the image is genuinely gone) and is best-effort — one instance
        failing must not abandon the rest of the resume.
        """
        candidates = self.store.list_projections_by_status_reason(
            st.STOPPED, st.REASON_OPERATOR_HALT)
        restarted, failed = [], []
        for row in candidates:
            slug = row["id"]
            try:
                self.start({
                    "type": "start",
                    "deployment_id": slug,
                    "tenant_id": row.get("tenant_id"),
                    "actor": actor,
                    "payload": {"port_override": row.get("port_override")},
                })
            except Exception as exc:
                log.exception("resume %s failed", slug, exc_info=exc)
                self.log(slug, "run", f"Resume failed: {exc}")
                failed.append(slug)
                continue
            after = self.store.get_projection(slug) or {}
            if after.get("status") == st.RUNNING:
                restarted.append(slug)
            else:
                failed.append(slug)
        self.emit("", ev.OPERATOR_RESUME_EFFECTIVE, actor=actor,
                  meta={"restarted": restarted, "failed": failed})
        log.info("resume effective: %d restarted, %d failed",
                 len(restarted), len(failed))

    def delete(self, cmd: dict) -> None:
        """Destroy a Deployment — the quota remedy: a stopped Deployment keeps
        its slot until the owner deletes it. Stop any tracked container, record
        the terminal event while the rows still exist, then tear the
        projection/secrets/deployments rows down (AD-3: the runner owns
        lifecycle)."""
        slug = cmd["deployment_id"]
        row = self.store.get_projection(slug)
        if row is None:
            self.log(slug, "run", "Deployment already gone.")
            return
        inst = self.instances.pop(slug, None)
        if inst:
            try:
                self.executor.stop(inst)
            except Exception as exc:
                self.log(slug, "run", f"stop errored: {exc}")
        self.log(slug, "run", "Deleting deployment — freeing its quota slot.")
        self.emit(slug, ev.DEPLOYMENT_DELETED, actor=cmd.get("actor") or "system",
                  meta={"repo_url": row.get("repo_url")})
        self.store.delete_deployment(slug)

    def sweep_orphans(self, grace_seconds: int = 60) -> int:
        """Delete phantom deployments: a row with no projection and no queued
        command will never be claimed, so it reads as `submitted` forever and
        holds a quota slot (TODO 16). The grace window absorbs the api's
        create → enqueue gap. Returns the number of phantom rows deleted."""
        swept = 0
        for row in self.store.list_orphan_candidates(grace_seconds=grace_seconds):
            slug = row["id"]
            tenant_id = row["tenant_id"]
            self.store.append_event(tenant_id, slug, ev.DEPLOYMENT_DELETED, "system",
                                    {"repo_url": row.get("repo_url"),
                                     "reason": "orphan_sweep"})
            self.logs.append(tenant_id, slug, "run",
                             "No workload was ever claimed — phantom Deployment deleted.")
            self.store.delete_deployment(slug)
            swept += 1
        if swept:
            log.info("orphan sweep: deleted %d phantom Deployment(s)", swept)
        return swept

    def enforce_halt(self) -> None:
        cfg = self.store.get_config("policy", {}) or {}
        if cfg.get("halted") and (
            self.store.list_running_projections()
            or self.store.pending_count() > 0
        ):
            self.halt_all(actor="system")

    # -- internal ------------------------------------------------------------

    def _interrupted(self, slug: str, category: str) -> None:
        reason = st.REASON_KILLED if category == "stop" else st.REASON_OPERATOR_HALT
        harness = self.instances.pop(slug, None)
        if harness:
            try:
                self.executor.stop(harness)
            except Exception:
                pass
        self.set(slug, status=st.FAILED, reason=reason, phase="failed")
        self.emit(slug, ev.BUILD_FAILED, meta={"reason": reason})
        self.emit(slug, ev.INSTANCE_FAILED, meta={"reason": reason})
        self.log(slug, "build" if category == "stop" else "run",
                 "Build stopped." if category == "stop" else "Halted by operator.")

    def _fail(self, slug: str, reason: str, message: str, stream: str) -> None:
        self.set(slug, status=st.FAILED, reason=reason, phase="failed")
        self.emit(slug, ev.BUILD_FAILED, meta={"reason": reason})
        self.emit(slug, ev.INSTANCE_FAILED, meta={"reason": reason})
        self.log(slug, stream, message)

    def reconcile(self) -> None:
        """Rehydrate executor-managed instances after a runner restart (prod)."""
        if self.executor.name == "podman":
            for inst in self.executor.reconcile(self.store):
                self.instances[inst.slug] = inst

    def refresh_idle_markers(self) -> None:
        for row in self.store.list_running_projections():
            self._mark_idle(row["id"])