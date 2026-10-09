"""Deployment lifecycle — the event-sourced state machine (AD-2, AD-3).

Statuses are exactly `building | running | stopped | failed | denied` with the
transient `starting`. `submitted` and the pipeline phases are finer-grained
facts the UI surfaces as progress, not badges. The UI renders projections and
never mints new states (UX-DR10).

The runner is the single writer of the status projection (AD-3). The api never
mutates a phase directly — it emits commands.
"""

from __future__ import annotations

# The exact badge set (AD-3).
SUBMITTED = "submitted"
BUILDING = "building"
RUNNING = "running"
STARTING = "starting"
STOPPED = "stopped"
FAILED = "failed"
DENIED = "denied"

# Statuses a user can see as badges.
DISPLAY_STATUSES = frozenset({"building", "running", "starting", "stopped", "failed", "denied"})

# Pipeline phases surfaced on the Deploy progress bar (clone → detect → build → start → probe).
PHASES = ("preparing", "clone", "detect", "build", "load", "start", "probe", "done")

TERMINAL = frozenset({DENIED, FAILED})


class InvalidTransition(Exception):
    """Raised when a transition would violate AD-3's state machine."""


# Allowed status transitions (AD-3 state diagram). Keys: current status.
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    SUBMITTED: {BUILDING, DENIED, FAILED},
    BUILDING: {RUNNING, FAILED},
    RUNNING: {STOPPED, FAILED},
    STOPPED: {STARTING, FAILED},
    STARTING: {RUNNING, FAILED},
    DENIED: set(),
    FAILED: set(),
}


def assert_transition(current: str, target: str) -> None:
    """Validate a status transition. Raises InvalidTransition on violation."""
    if current == target:
        return
    allowed = ALLOWED_TRANSITIONS.get(current, set())
    if target not in allowed:
        raise InvalidTransition(f"illegal deployment transition {current} -> {target}")


# Reasons the runner records (all snake_case, plain in the UI).
REASON_IDLE = "idle"
REASON_MANUAL = "manual"
REASON_OPERATOR_STOP = "operator_force_stop"
REASON_KILLED = "killed"
REASON_OPERATOR_HALT = "halted_by_operator"
REASON_HEALTH_LOST = "health_lost"
REASON_BUILD_ERROR = "build_error"
REASON_UNKNOWN_STACK = "unsupported_stack"
REASON_PROBE_FAILED = "probe_failed"
REASON_SECRET_FAILURE = "secret_failure"
REASON_IMAGE_MISSING = "image_missing"
REASON_QUOTA = "quota_exceeded"
REASON_ALLOWLIST = "allowlisted"