"""Process configuration.

All values come from the environment with dev-first defaults. Secrets never
appear here. The ops/ package owns prod file provisioning (systemd, Caddy,
nftables) which reads these same names.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field


@dataclass
class Settings:
    env: str = "dev"
    data_dir: str = ".portal-data"
    portal_domain: str = "portal.dev"
    prod_protocol: str = "https"
    dev_protocol: str = "http"

    runner_executor: str = "podman"
    simulate_build_delay: float = 1.5

    quota_max_instances: int = 3
    quota_concurrency: int = 2
    quota_cpu: float = 1.0
    quota_mem_mb: int = 1024

    egress_rules: list = field(default_factory=list)
    idle_stop_seconds: int = 1800

    runner_key_path: str = ""
    api_key_path: str = ""

    def __post_init__(self) -> None:
        self._load_env()

    def _load_env(self) -> None:
        from dotenv import load_dotenv

        load_dotenv()
        g = os.environ.get
        self.env = g("PORTAL_ENV", self.env)
        self.data_dir = g("PORTAL_DATA_DIR", self.data_dir)
        self.portal_domain = g("PORTAL_PORTAL_DOMAIN", self.portal_domain)
        self.prod_protocol = g("PORTAL_BASE_PROTOCOL", self.prod_protocol)
        self.dev_protocol = g("PORTAL_DEV_BASE_PROTOCOL", self.dev_protocol)
        self.runner_executor = g("PORTAL_RUNNER_EXECUTOR", self.runner_executor)
        self.simulate_build_delay = float(g("PORTAL_SIMULATE_BUILD_DELAY", self.simulate_build_delay))
        raw_egress = g("PORTAL_EGRESS_RULES_JSON")
        if raw_egress:
            self.egress_rules = json.loads(raw_egress)
        self.idle_stop_seconds = int(g("PORTAL_IDLE_STOP_SECONDS", self.idle_stop_seconds))
        self.runner_key_path = g("PORTAL_RUNNER_KEY_PATH", "")
        self.api_key_path = g("PORTAL_API_KEY_PATH", "")

    # -- derived ------------------------------------------------------------

    @property
    def is_prod(self) -> bool:
        return self.env == "prod"

    @property
    def db_path(self) -> str:
        return os.path.join(self.data_dir, "portal.db")

    @property
    def log_root(self) -> str:
        return os.path.join(self.data_dir, "logs")

    @property
    def key_dir(self) -> str:
        return os.path.join(self.data_dir, "keys")

    @property
    def runner_pub_path(self) -> str:
        base = self.runner_key_path or os.path.join(self.key_dir, "runner.key")
        return base  # runner.key + runner.key.pub written by RunnerCrypto

    @property
    def cpu_quota_defaults(self) -> list:
        return self.egress_rules or []


def ensure_crypto(cfg: Settings):
    """Write per-role key material into the data dir. api=encryptor, runner=sole
    decryptor (AD-9). Files are chmod 0600 for the private halves."""
    from adapters import crypt as c    # noqa: PLC0415

    keys = cfg.key_dir
    if cfg.is_prod:
        # ops/ provisions these with restricted ownership; do not auto-create.
        runner_pub, _ = c.load_existing_keypair(os.path.join(keys, "runner.key"))
        api_pub, api_sec = c.load_existing_keypair(os.path.join(keys, "api.key"))
        return {
            "runner_pub": runner_pub,
            "api_pub": api_pub,
            "api_sec": api_sec,
        }
    # dev: provision idempotently so secrets/runner crypto work out of the box.
    runner_pub, _ = c.load_or_create_keypair(os.path.join(keys, "runner.key"))
    api_pub, api_sec = c.load_or_create_keypair(os.path.join(keys, "api.key"))
    return {
        "runner_pub": runner_pub,
        "api_pub": api_pub,
        "api_sec": api_sec,
    }