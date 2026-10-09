"""Application context assembly for the control plane.

One `Workspace` bundles the settings, the sqlite store (control side), the log
store, and the envelope-encryption material (AD-9: the api encrypts; the runner
alone decrypts). Built once at process start.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from adapters.crypt import encrypt_for_holder
from adapters.logpersist import LogStore
from adapters.store import Store
from api.config import Settings, ensure_crypto
from core import policy

log = logging.getLogger("portal")

# The app has no login: every request is this one user (see api/deps.py). The
# store still partitions by tenant_id, so the pair is pinned here and the row is
# seeded at boot.
SINGLE_TENANT = {"id": 2, "email": "dev@portal.local"}

DEFAULT_POLICY = {
    "allow_all": False,
    "allow_list": policy.DEFAULT_ALLOWLIST,
    "quota": {"per_user": policy.DEFAULT_QUOTA},
    "egress": [],
    "idle_stop_seconds": 30 * 60,
    "halted": False,
    "halt_reason": None,
}


def _parse(kind: str, s: str):
    if kind == "float":
        return float(s)
    if kind == "int":
        return int(s)
    return s


@dataclass
class Workspace:
    settings: Settings
    store: Store
    logs: LogStore
    envelope: dict | None = field(default_factory=dict)  # api pub/sec + runner pub

    # -- helpers ------------------------------------------------------------

    def policy_config(self) -> dict:
        raw = self.store.get_config("policy")
        return policy.normalize_config(raw)

    def policy_defaults_from_env(self) -> dict:
        s = self.settings
        return {
            "allow_all": False,
            "allow_list": policy.DEFAULT_ALLOWLIST,
            "quota": {
                "max_instances": s.quota_max_instances,
                "max_concurrent_builds": s.quota_concurrency,
                "cpu": s.quota_cpu,
                "mem_mb": s.quota_mem_mb,
            },
            "egress": s.egress_rules,
            "idle_stop_seconds": s.idle_stop_seconds,
            "halted": False,
            "halt_reason": None,
        }

    def seed_policy_once(self) -> None:
        if self.store.get_config("policy") is None:
            self.store.set_config("policy", self.policy_defaults_from_env())

    def seed_single_tenant(self) -> None:
        """Make the pinned tenant row exist. A fresh database has no sign-in to
        create one, and `deployments.tenant_id REFERENCES tenants(id)` would
        reject the very first deploy."""
        if self.store.get_tenant(SINGLE_TENANT["id"]) is None:
            self.store.ensure_tenant(SINGLE_TENANT["id"], SINGLE_TENANT["email"])

    def compose_deployment(self, proj: dict | None, *, slug: str | None = None) -> dict:
        """Shape a projection into the API contract; fills safe defaults for a
        deployment the runner has not yet claimed (no fake statuses)."""
        if proj is None:
            proj = {}
        d = dict(proj)
        d.setdefault("id", slug or "")
        d.setdefault("status", "submitted" if d.get("phase") else "submitted")
        d.setdefault("phase", "preparing")
        d.setdefault("public_url", None)
        d.setdefault("secrets", [])
        d.setdefault("already_deployed", False)
        return d


def create_workspace(settings: Settings | None = None, *, migrate: bool = True) -> Workspace:
    settings = settings or Settings()
    if migrate:
        admin = Store(settings.db_path, mode="admin")
        admin.close()
    store = Store(settings.db_path, mode="control")
    logs = LogStore(settings.log_root)
    envelope = ensure_crypto(settings)
    ws = Workspace(
        settings=settings,
        store=store,
        logs=logs,
        envelope=envelope or {},
    )
    ws.seed_policy_once()
    ws.seed_single_tenant()
    return ws


def encrypt_secret(ws: Workspace, value: str) -> dict:
    """Encrypt a secret for the runner (AD-9). The api holds no runner secret."""
    pub = ws.envelope["runner_pub"]
    api_pub = ws.envelope["api_pub"]
    api_sec = ws.envelope["api_sec"]
    return encrypt_for_holder(api_pub, api_sec, pub, value)