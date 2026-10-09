"""Executor protocol — how the runner starts/stops Instances (FR-10).

Two implementations:
  * simulated — dev default; a real loopback HTTP server, real probe, no
    container daemon required. Clearly dev-only (made explicit in logs).
  * podman    — prod: rootless Podman per tenant (AD-5). One daemon; the runner
    runs every command as the per-tenant unprivileged user (ops/ owns the
    user profile; nftables owns egress).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class Instance:
    slug: str
    tenant_id: int
    executor: str
    ref: str = ""          # podman container name / simulated server tag
    port: int = 0          # bound 127.0.0.1:<port>
    image_ref: str | None = None
    extra: dict = field(default_factory=dict)


class Executor(Protocol):
    name: str
    # Does a cold start require a persisted image to exist? Prod executors
    # (podman) run the persisted image (AD-12); dev executors start from
    # source and never consume one (TODO 9).
    requires_image: bool

    def start(self, *, slug: str, tenant_id: int, source_root,
              static: bool, env: dict, ports: list[int],
              image_ref: str | None,
              log_line) -> Instance: ...

    def stop(self, instance: Instance) -> None: ...

    def reconcile(self, store) -> list[Instance]: ...


def supports_image_ref(image_ref: str | None, executor: Executor) -> bool:
    """Whether a cold start can run with the given image ref.

    Image-requiring executors (prod) need the ref to exist — a missing image
    means an audited rebuild (AD-12). Source-based executors (dev) start from
    source and never consume an image, so a None ref is not an error for them.
    """
    if not executor.requires_image:
        return True
    return image_ref is not None