"""Hexagon ports (AD-2). The domain core defines these interfaces; adapters
implement them. The api composes the ports it needs; the runner composes its
own. Nothing in `core` imports a framework adapter.
"""

from __future__ import annotations

from typing import Protocol


class Builder(Protocol):
    """Build stage: takes detection + source and produces an OCI image reference.

    The real implementation runs Railpack → BuildKit → Podman load (AD-4, IV-1);
    the simulated implementation logs progress and returns None (dev facade).
    """

    name: str

    def build(self, *, source_dir, slug: str, detect, log,
              settings) -> str | None: ...


class StateStore(Protocol):
    """The shared state store: append-only events, commands queue, config,
    tenant identity, deployment metadata, runner-owned projections."""

    # --- events (AD-2/AD-10: append-only) ---
    def append_event(self, tenant_id: int, deployment_id: str, type_: str,
                     actor: str, meta: dict | None = None) -> int: ...

    def read_events(self, *, tenant_id: int | None = None,
                    deployment_id: str | None = None,
                    actor: str | None = None,
                    since_ts: str | None = None,
                    until_ts: str | None = None,
                    limit: int = 200) -> list[dict]: ...

    # --- commands ---
    def enqueue_command(self, type_: str, tenant_id: int, deployment_id: str,
                        actor: str, payload: dict | None = None) -> int: ...

    def claim_command(self) -> dict | None: ...

    def finish_command(self, command_id: int, status: str) -> None: ...

    def has_preemptive_signal(self, deployment_id: str, type_: str) -> bool: ...

    # --- identity ---
    def upsert_tenant(self, email: str, role: str = "user") -> dict: ...

    def get_tenant_by_email(self, email: str) -> dict | None: ...

    def get_tenant(self, tenant_id: int) -> dict | None: ...

    def list_tenants(self) -> list[dict]: ...

    # --- deployment metadata (api-owned) ---
    def create_deployment(self, slug: str, tenant_id: int, repo_url: str,
                          port_override: int | None) -> None: ...

    def get_deployment(self, slug: str) -> dict | None: ...

    def update_deployment_meta(self, slug: str, *, port_override: int | None = None) -> None: ...

    # --- projection (runner-owned; api reads through a read-only view) ---
    def get_projection(self, slug: str) -> dict | None: ...

    def list_projections(self, tenant_id: int | None = None,
                         *, limit: int = 50, offset: int = 0) -> list[dict]: ...

    def count_projections(self, tenant_id: int | None = None) -> int: ...

    def set_projection(self, slug: str, *, status: str, reason: str | None = None,
                       phase: str | None = None, mapped_port: int | None = None,
                       public_url: str | None = None, image_ref: str | None = None,
                       idle_stop_at: str | None = None) -> None: ...

    def touch_last_activity(self, slug: str) -> None: ...

    def list_running_projections(self) -> list[dict]: ...

    # --- secrets (AD-9) ---
    def upsert_secret(self, tenant_id: int, slug: str, key: str, ciphertext: str) -> None: ...

    def list_secrets(self, tenant_id: int, slug: str) -> list[dict]: ...

    def delete_secret(self, tenant_id: int, slug: str, key: str) -> bool: ...

    # --- config (AD-8: operator policy, adjustable without redeploy) ---
    def get_config(self, key: str, default=None): ...

    def set_config(self, key: str, value) -> int: ...

    def get_previous_config_revision(self, key: str, revision: int): ...