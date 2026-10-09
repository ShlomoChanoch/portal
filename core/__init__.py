"""Portal domain core — the hexagon.

This package imports no framework adapter (AD-2). Deployment lifecycle is an
append-only event stream; the status the UI renders is a projection. Only the
`runner` is allowed to advance a Deployment's lifecycle; the `api` emits
commands and reads projections.
"""

from .errors import (
    AllowListDenied,
    Conflict,
    InvalidRepoUrl,
    NotFound,
    PortalError,
    QuotaExceeded,
    UnsupportedStack,
    error_payload,
)

__all__ = [
    "PortalError",
    "NotFound",
    "Conflict",
    "InvalidRepoUrl",
    "QuotaExceeded",
    "AllowListDenied",
    "UnsupportedStack",
    "error_payload",
]