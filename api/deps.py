"""FastAPI dependencies — the single implicit tenant on every route.

There is no login: no session cookie, no Bearer token, no roles. Every request
is the one user, pinned here. The store still partitions by tenant_id, so the
identity stays a fixed pair rather than disappearing (create_workspace seeds the
row so a fresh database can actually create Deployments — `deployments.tenant_id
REFERENCES tenants(id)`).
"""

from __future__ import annotations

from fastapi import Depends, Request

from api.bootstrap import SINGLE_TENANT, Workspace


def get_ws(request: Request) -> Workspace:
    return request.app.state.ws


def get_tenant() -> dict:
    """The one tenant every route acts as."""
    return dict(SINGLE_TENANT)
