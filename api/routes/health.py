"""Liveness/health (Story 1.0).

`/api/live` is a fast no-data probe (no authorization; returns the HTTP status
only — it holds no business information, so AD-11's identity floor is not
weakened). Full operator health lives under /api/operator/health behind the
operator role.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from api.deps import get_ws
from api.bootstrap import Workspace

router = APIRouter(tags=["health"])


@router.get("/api/live")
def live(ws: Workspace = Depends(get_ws)) -> dict:
    return {"status": "ok"}