"""Event stream vocabulary (AD-2, AD-10).

Events are append-only; the stream doubles as the audit log. snake_case,
past tense when terminal. An event is a dict for storage:

    {"id", "tenant_id", "deployment_id", "type", "actor", "meta", "created_at"}
"""

from __future__ import annotations

from dataclasses import dataclass, field


# --- Event types (frozen vocabulary; introducing a new one is an AD-10 change) ---
DEPLOY_SUBMITTED = "deploy.submitted"
DEPLOY_DENIED = "deploy.denied"
UNSUPPORTED_APP = "unsupported_app"
BUILD_STARTED = "build.started"
BUILD_COMPLETED = "build.completed"
BUILD_FAILED = "build.failed"
INSTANCE_STARTING = "instance.starting"
INSTANCE_STARTED = "instance.started"
INSTANCE_STOPPED = "instance.stopped"
INSTANCE_FAILED = "instance.failed"
INSTANCE_HEALTH_LOST = "instance.health_lost"
DEPLOYMENT_REDEPLOYED = "deployment.redeployed"
ADMISSION_QUOTA_HIT = "admission.quota_hit"
ADMISSION_ALLOWLIST_DENIED = "admission.allowlist_denied"
OPERATOR_FORCE_STOP = "operator.force_stop"
OPERATOR_HALT = "operator.halt"
OPERATOR_HALT_EFFECTIVE = "operator.halt_effective"
OPERATOR_RESUME = "operator.resume"
OPERATOR_RESUME_EFFECTIVE = "operator.resume_effective"
OPERATOR_CONFIG_CHANGED = "operator.config_changed"
OPERATOR_CONFIG_ROLLBACK = "operator.config_rollback"
INSTANCE_START_REQUESTED = "instance.start_requested"
INSTANCE_STOP_REQUESTED = "instance.stop_requested"
DEPLOYMENT_SETTINGS_CHANGED = "deployment.settings_changed"
DEPLOYMENT_DELETE_REQUESTED = "deployment.delete_requested"
DEPLOYMENT_DELETED = "deployment.deleted"

# Events quite distinct across tenants/actor — all go through same channel.
ALL_EVENT_TYPES = frozenset({
    DEPLOY_SUBMITTED,
    DEPLOY_DENIED,
    UNSUPPORTED_APP,
    BUILD_STARTED,
    BUILD_COMPLETED,
    BUILD_FAILED,
    INSTANCE_STARTING,
    INSTANCE_STARTED,
    INSTANCE_STOPPED,
    INSTANCE_FAILED,
    INSTANCE_HEALTH_LOST,
    DEPLOYMENT_REDEPLOYED,
    ADMISSION_QUOTA_HIT,
    ADMISSION_ALLOWLIST_DENIED,
    OPERATOR_FORCE_STOP,
    OPERATOR_HALT,
    OPERATOR_HALT_EFFECTIVE,
    OPERATOR_RESUME,
    OPERATOR_RESUME_EFFECTIVE,
    OPERATOR_CONFIG_CHANGED,
    OPERATOR_CONFIG_ROLLBACK,
    INSTANCE_START_REQUESTED,
    INSTANCE_STOP_REQUESTED,
    DEPLOYMENT_SETTINGS_CHANGED,
    DEPLOYMENT_DELETE_REQUESTED,
    DEPLOYMENT_DELETED,
})


@dataclass
class Event:
    tenant_id: int
    deployment_id: str
    type: str
    actor: str
    meta: dict = field(default_factory=dict)
    id: int | None = None
    created_at: str | None = None

    def to_row(self) -> dict:
        row = {
            "tenant_id": self.tenant_id,
            "deployment_id": self.deployment_id,
            "type": self.type,
            "actor": self.actor,
            "meta": self.meta,
        }
        if self.id is not None:
            row["id"] = self.id
        if self.created_at is not None:
            row["created_at"] = self.created_at
        return row