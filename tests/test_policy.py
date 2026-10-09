"""Policy normalization, Allow-list matching, and the quota gate (AD-8, FR-12)."""
from __future__ import annotations

import pytest

from adapters.store import Store
from core import policy
from core.errors import QuotaExceeded


def test_normalize_config_heals_legacy_double_nested_quota():
    raw = {
        "quota": {
            "per_user": {
                "max_instances": 1,
                "max_concurrent_builds": 2,
                "cpu": 1.0,
                "mem_mb": 1024,
                "per_user": {"max_instances": 3, "max_concurrent_builds": 2},
            }
        },
        "allow_all": False,
        "egress": [],
        "idle_stop_seconds": 600,
    }
    cfg = policy.normalize_config(raw)
    per_user = cfg["quota"]["per_user"]
    assert "per_user" not in per_user
    assert per_user["max_instances"] == 1  # operator intent (outer) wins
    assert per_user["max_concurrent_builds"] == 2
    assert cfg["idle_stop_seconds"] == 600


def test_normalize_config_flat_quota_passthrough():
    cfg = policy.normalize_config({"quota": {"max_instances": 4, "cpu": 0.5}})
    assert cfg["quota"]["per_user"]["max_instances"] == 4
    assert cfg["quota"]["per_user"]["cpu"] == 0.5


@pytest.mark.parametrize(
    ("rules", "url", "expected"),
    [
        ([{"kind": "host", "value": "github.com"}], "https://github.com/a/b", True),
        ([{"kind": "host", "value": "gitlab.com"}], "https://github.com/a/b", False),
        ([{"kind": "host", "value": "github.com"}], "github.com.evil.io/a/b", False),
        ([{"kind": "prefix", "value": "https://internal.example/repos/"}],
         "https://internal.example/repos/x", True),
        ([{"kind": "pattern", "value": "*.corp.dev/*"}], "https://ci.corp.dev/job", True),
        ([], "https://anything.example/x", False),  # fail closed
    ],
)
def test_allowlist_matching(rules, url, expected):
    assert policy.allowlist_matches(rules, url) is expected


def test_quota_gate_respects_per_tenant_override(tmp_path):
    Store(str(tmp_path / "q.db"), mode="admin").close()
    store = Store(str(tmp_path / "q.db"), mode="control")
    cfg = policy.normalize_config({"quota": {"max_instances": 2, "max_concurrent_builds": 1}})
    store.set_config("tenant:quota:7", {"max_instances": 1})

    policy.check_quota(cfg, tenant_id=7, active={"count": 0, "running": 0, "building": 0}, store=store)
    policy.check_quota(cfg, tenant_id=8, active={"count": 1, "running": 1, "building": 0}, store=store)
    with pytest.raises(QuotaExceeded):
        policy.check_quota(cfg, tenant_id=7, active={"count": 1, "running": 1, "building": 0}, store=store)
    with pytest.raises(QuotaExceeded):
        policy.check_quota(cfg, tenant_id=8, active={"count": 2, "running": 1, "building": 0}, store=store)
    with pytest.raises(QuotaExceeded):
        policy.check_quota(cfg, tenant_id=9, active={"count": 0, "running": 0, "building": 1}, store=store)


def test_is_halted():
    assert policy.is_halted(policy.normalize_config({"halted": True}))
    assert not policy.is_halted(policy.normalize_config({"halted": False}))