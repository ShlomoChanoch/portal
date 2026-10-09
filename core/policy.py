"""Policy evaluation — quota and Allow-list as a config-driven double gate (AD-8).

The api checks at admission and the runner re-checks before consuming a build
or run command. All policy lives in the store (operator-adjustable without a
code change); these functions are pure and operate on config values.
"""

from __future__ import annotations

import fnmatch
import re
from urllib.parse import urlparse

# Sensible cohort starting values (PRD OQ-5). The operator panel can change
# these via the store; nothing here is hardcoded in the UI.
DEFAULT_QUOTA = {
    "max_instances": 3,
    "max_concurrent_builds": 2,
    "cpu": 1.0,
    "mem_mb": 1024,
}

DEFAULT_ALLOWLIST = [
    {"kind": "host", "value": "github.com"},
    {"kind": "host", "value": "gitlab.com"},
    {"kind": "host", "value": "bitbucket.org"},
    {"kind": "host", "value": "codeberg.org"},
]


def normalize_config(raw: dict | None) -> dict:
    """Shape the raw config dict into the structured policy used by the gates."""
    raw = raw or {}
    quota = dict(DEFAULT_QUOTA)
    raw_quota = raw.get("quota") or {}
    while isinstance(raw_quota, dict) and isinstance(raw_quota.get("per_user"), dict):
        inner = raw_quota.pop("per_user")
        raw_quota = {**inner, **raw_quota}
    quota.update(raw_quota)
    allow_list = raw.get("allow_list")
    if allow_list is None:
        allow_list = DEFAULT_ALLOWLIST
    allow_all = bool(raw.get("allow_all"))
    egress = raw.get("egress") or []
    idle_seconds = raw.get("idle_stop_seconds") or 30 * 60
    halted = bool(raw.get("halted"))
    halt_reason = raw.get("halt_reason")
    return {
        "quota": {"per_user": quota},
        "allow_list": allow_list,
        "allow_all": allow_all,
        "egress": egress,
        "idle_stop_seconds": int(idle_seconds),
        "halted": halted,
        "halt_reason": halt_reason,
    }


def allowlist_matches(allow_list: list[dict], repo_url: str) -> bool:
    """True if repo_url passes the configured Allow-list (FR-12).

    Rule kinds:
      host      — repository host (github.com) allowed when the domain matches,
                  exact or a suffix of the host.
      prefix    — repository URL starts with the given prefix.
      pattern   — a gitignore-style glob applied to the full URL.
    An empty Allow-list denies everything (fail closed).
    """
    if not allow_list:
        return False
    host = (urlparse(repo_url).hostname or "").lower()
    lowered = repo_url.lower()
    for rule in allow_list:
        kind = (rule.get("kind") or "host").lower()
        value = (rule.get("value") or "").strip().lower()
        if not value:
            continue
        if kind == "host":
            if host == value or host.endswith("." + value):
                return True
        elif kind == "prefix":
            if lowered.startswith(value):
                return True
        elif kind == "pattern":
            if re.fullmatch(fnmatch.translate(value), lowered):
                return True
    return False


def check_allowlist(config_policy: dict, repo_url: str) -> None:
    """Raise AllowListDenied when the Allow-list rejects the URL (FR-12)."""
    if config_policy["allow_all"]:
        return
    if not allowlist_matches(config_policy["allow_list"], repo_url):
        from .errors import AllowListDenied

        raise AllowListDenied("Denied by Allow-list.")


def quota_message(quota: dict, what: str) -> str:
    """Actionable plain-words message for a cap (UX-DR17 / FR-11)."""
    max_instances = quota.get("max_instances")
    if what == "instances" and max_instances is not None:
        return f"Quota reached — {max_instances} instances max. Stop one to deploy more."
    if what == "builds":
        return "Quota reached — too many builds running. Wait for one to finish."
    return "Quota reached — a cap is at its limit. Stop one to deploy more."


def check_quota(config_policy: dict, tenant_id: int, active: dict,
                store) -> None:
    """Raise QuotaExceeded when a tenant would exceed its caps (FR-11).

    `active` is a callable-friendly view produced by the caller:
      { "count": int, "running": int, "building": int }
    `store` is the StateStore used to read per-tenant policy overrides.
    """
    from .errors import QuotaExceeded

    quota = config_policy["quota"]["per_user"]
    # Per-tenant override lives in config key tenant:quota:<id> (AD-8).
    override = store.get_config(f"tenant:quota:{tenant_id}", {})
    quota = {**quota, **(override or {})}

    if active["count"] >= quota.get("max_instances", 10**9):
        raise QuotaExceeded(quota_message(quota, "instances"))
    if active["building"] >= quota.get("max_concurrent_builds", 10**9):
        raise QuotaExceeded(quota_message(quota, "builds"))


def is_halted(config_policy: dict) -> bool:
    return bool(config_policy["halted"])