"""Commands — the api → runner queue (AD-2, AD-3).

The api emits commands and never mutates a phase directly. `stop`/`halt` are
preemptive: a `stop` is honored even mid-build (the killed build marks the
Deployment failed, reason `killed`); a platform `halt` stops the runner from
consuming further commands immediately.
"""

from __future__ import annotations

from dataclasses import dataclass, field

CMD_DEPLOY = "deploy"
CMD_REDEPLOY = "redeploy"
CMD_START = "start"
CMD_STOP = "stop"
CMD_FORCE_STOP = "force_stop"
CMD_HALT = "halt"

COMMAND_TYPES = frozenset({CMD_DEPLOY, CMD_REDEPLOY, CMD_START, CMD_STOP, CMD_FORCE_STOP, CMD_HALT})

# Command statuses
CMD_PENDING = "pending"
CMD_RUNNING = "running"
CMD_DONE = "done"
CMD_FAILED = "failed"
CMD_ABORTED = "aborted"


@dataclass
class Command:
    type: str
    tenant_id: int
    deployment_id: str
    actor: str
    payload: dict = field(default_factory=dict)
    id: int | None = None
    status: str = CMD_PENDING
    created_at: str | None = None

    def to_row(self) -> dict:
        return {
            "tenant_id": self.tenant_id,
            "deployment_id": self.deployment_id,
            "type": self.type,
            "actor": self.actor,
            "payload": self.payload,
            "status": self.status,
        }