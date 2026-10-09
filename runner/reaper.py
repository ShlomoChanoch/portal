"""Idle reaper (FR-15, AD-12: stop — never rebuild).

Instances whose idle window (last activity + operator idle_stop_seconds, or the
deployment's overridden port cron) expires are stopped, never torn down.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from core import events as ev
from core import state as st
from runner.pipeline import Pipeline

log = logging.getLogger("runner.reaper")


class Reaper:
    def __init__(self, store, pipeline: Pipeline, idle_cadence: float = 20.0):
        self.store = store
        self.pipeline = pipeline
        self.idle_cadence = idle_cadence

    def tick(self) -> int:
        """Stop one expired instance (defensive: at most one per tick so a long
        stop never starves new builds). Returns number stopped."""
        stopped = self._tick_health()
        for row in self.store.list_running_projections():
            marker = row.get("idle_stop_at")
            if not marker:
                continue
            try:
                due = datetime.fromisoformat(marker)
            except ValueError:
                continue
            if due.tzinfo is None:
                due = due.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) < due:
                continue
            slug = row["id"]
            self.pipeline.emit(slug, ev.INSTANCE_STOP_REQUESTED, actor="system",
                               meta={"reason": st.REASON_IDLE})
            self.pipeline.emit(slug, ev.INSTANCE_STOPPED, meta={"reason": st.REASON_IDLE})
            self.pipeline.set(slug, status=st.STOPPED, reason=st.REASON_IDLE,
                              phase="stopped", mapped_port=None, idle_stop_at=None)
            inst = self.pipeline.instances.pop(slug, None)
            if inst:
                try:
                    self.pipeline.executor.stop(inst)
                except Exception:
                    pass
            self.pipeline.log(slug, "run", "Stopped after idle timeout.")
            log.info("idle stop: %s", slug)
            stopped += 1
            break
        return stopped

    def _tick_health(self) -> int:
        """Podman only: a projection marked running whose container is gone is a
        dead link — stop advertising it instead of waiting for the idle window."""
        if getattr(getattr(self.pipeline, "executor", None), "name", None) != "podman":
            return 0
        for row in self.store.list_running_projections():
            slug = row["id"]
            inst = self.pipeline.instances.get(slug)
            if not inst or self.pipeline.executor.is_running(inst):
                continue
            self.pipeline.emit(slug, ev.INSTANCE_STOPPED, meta={"reason": st.REASON_HEALTH_LOST})
            self.pipeline.set(slug, status=st.STOPPED, reason=st.REASON_HEALTH_LOST,
                              phase="stopped", mapped_port=None, idle_stop_at=None)
            self.pipeline.instances.pop(slug, None)
            try:
                self.pipeline.executor.stop(inst)
            except Exception:
                pass
            self.pipeline.log(slug, "run", "Container exited — instance stopped (health lost).")
            log.info("health lost: %s", slug)
            return 1
        return 0