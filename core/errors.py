"""Domain errors — the one error shape past the API boundary.

Spine Consistency Conventions: `{ "error": { "code", "message" } }`, never a
stack trace. Codes are the machine-readable set; the UI restates each code in
plain words (UX-DR17) and the operator surfaces keep the machine reason.
"""

from __future__ import annotations


class PortalError(Exception):
    status = 400
    code = "error"

    def __init__(self, message: str, *, status: int | None = None, code: str | None = None):
        super().__init__(message)
        self.message = message
        if status is not None:
            self.status = status
        if code is not None:
            self.code = code

    def payload(self) -> dict:
        return {"error": {"code": self.code, "message": self.message}}


class NotFound(PortalError):
    status = 404
    code = "not_found"


class Conflict(PortalError):
    status = 409
    code = "conflict"


class InvalidRepoUrl(PortalError):
    status = 422
    code = "invalid_repo_url"


class QuotaExceeded(PortalError):
    status = 409
    code = "quota_exceeded"


class AllowListDenied(PortalError):
    status = 403
    code = "allowlisted"


class UnsupportedStack(PortalError):
    status = 422
    code = "unsupported_stack"


def error_payload(message: str, code: str, status: int = 400) -> tuple[dict, int]:
    """A bare error payload in the convention shape; used by generic handlers."""
    return {"error": {"code": code, "message": message}}, status