"""Repo URL intake helpers (FR-1) and slug conventions (Consistency Conventions).

The unique user-facing input unit is a git Repo URL. Validation mirrors the
server side in the UI so a malformed paste fails before a build is wasted.
"""

from __future__ import annotations

import re
import secrets
from urllib.parse import urlparse

from .errors import InvalidRepoUrl

# A recognizable HTTPS git URL: scheme + host + non-root path. `.git` optional.
_HTTPS_RE = re.compile(
    r"^https://(?:[a-zA-Z0-9_-]+\.)+[a-zA-Z]{2,}/[^\s]+$"
)

SHA_KEBAB = re.compile(r"[^a-z0-9]+")


def validate_repo_url(url: str) -> str:
    """Validate and normalize a Repo URL for HTTPS git intake (FR-1).

    Accepts HTTPS git URLs; rejects malformed or non-git schemes with a clear
    message. Never returns a stack trace.
    """
    if not url or not isinstance(url, str):
        raise InvalidRepoUrl("Paste a repo URL to run it.")
    url = url.strip()
    if not url:
        raise InvalidRepoUrl("Paste a repo URL to run it.")
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise InvalidRepoUrl("That doesn't look like a git repo URL — use an https:// link.")
    if not parsed.hostname or "." not in parsed.hostname:
        raise InvalidRepoUrl("That doesn't look like a git repo URL.")
    if not _HTTPS_RE.match(url):
        raise InvalidRepoUrl("That doesn't look like a git repo URL — it needs a host and a path.")
    return url


def slug_for(repo_url: str, tenant_id: int, existing: set[str]) -> str:
    """Derive a lowercase-kebab slug unique per tenant (Consistency Conventions).

    Uses the repo's final path component plus a short random suffix, then
    guarantees tenant uniqueness by suffixing further on collision.
    """
    parsed = urlparse(repo_url)
    leaf = (parsed.path.rstrip("/").split("/")[-1] or "repo")
    leaf = leaf.removesuffix(".git")
    base = SHA_KEBAB.sub("-", leaf.lower()).strip("-") or "repo"
    base = base[:24]
    for _ in range(8):
        slug = f"{base}-{secrets.token_hex(2)}"
        if slug not in existing:
            return slug
        base = base[:20]
    return f"repo-{secrets.token_hex(3)}"


def normalize_port(port) -> int | None:
    """Normalize an optional port override; None when absent."""
    if port in (None, "", "auto"):
        return None
    try:
        port = int(port)
    except (TypeError, ValueError):
        raise InvalidRepoUrl("Port must be a number, or blank for automatic.")
    if not (1 <= port <= 65535):
        raise InvalidRepoUrl("Port must be between 1 and 65535.")
    return port


CANDIDATE_PORTS = (3000, 8080, 8000, 80, 5000, 5173, 8081, 7860, 4000, 9000)


def candidate_ports(override: int | None, detected: int | None) -> list[int]:
    """Port resolution order (AD-7): override → detected → candidate list."""
    order: list[int] = []
    for p in (override, detected):
        if p and p not in order:
            order.append(p)
    for p in CANDIDATE_PORTS:
        if p not in order:
            order.append(p)
    # Keep the list bounded for a single probe round.
    return order[:12]