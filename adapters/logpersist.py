"""Build/Run log persistence (FR-3, FR-7).

Log lines are persisted per Deployment as `[ts] text`, bounded by quota policy.
Streams survive page refresh and connection loss: SSE resumes from an offset the
view re-uses on reconnect (UX-DR11).
"""

from __future__ import annotations

import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


_TS_RE = re.compile(r"^\[(.+?)\] (.*)$", re.DOTALL)


class LogStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        os.makedirs(self.root, exist_ok=True)
        self._lock = threading.Lock()

    def path_for(self, tenant_id: int, slug: str, stream: str) -> Path:
        p = self.root / str(tenant_id) / slug / f"{stream}.log"
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def append(self, tenant_id: int, slug: str, stream: str, text: str) -> None:
        """Append one line (runner side). Secrets are masked before they reach
        this boundary; masking is enforced in the caller and re-checked here."""
        text = mask_secrets(text)
        with self._lock:
            with open(self.path_for(tenant_id, slug, stream), "a", encoding="utf-8") as fh:
                fh.write(f"[{_now()}] {text}\n")

    def append_many(self, tenant_id: int, slug: str, stream: str, lines: list[str]) -> None:
        for line in lines:
            self.append(tenant_id, slug, stream, line)

    def read(self, tenant_id: int, slug: str, stream: str,
             offset: int = 0, limit: int = 500) -> dict:
        """Return {"lines": [...], "next_offset": int, "total": int}."""
        path = self.path_for(tenant_id, slug, stream)
        if not path.exists():
            return {"lines": [], "next_offset": 0, "total": 0}
        with self._lock:
            raw = path.read_text(encoding="utf-8", errors="replace")
        total = raw.count("\n")
        lines = [(ts, text) for ts, text in (_TS_RE.match(l).groups() if _TS_RE.match(l) else ("", l) for l in raw.splitlines())]
        selected = lines[offset: offset + limit]
        return {
            "lines": selected,
            "next_offset": offset + len(selected),
            "total": total,
        }

    def tail(self, tenant_id: int, slug: str, stream: str, offset: int = 0) -> list[tuple[str, str]]:
        path = self.path_for(tenant_id, slug, stream)
        if not path.exists():
            return []
        with self._lock:
            raw = path.read_text(encoding="utf-8", errors="replace")
        lines = [_TS_RE.match(l).groups() if _TS_RE.match(l) else ("", l) for l in raw.splitlines()]
        return lines[offset:]


# ---- secret masking (FR-13: masked in logs/UI, never in plain) --------------

_MASK_CACHE: dict[str, str] = {}


def register_secret_value(value: str) -> None:
    """Purposely held here so the runner can mask values before appending."""
    if len(value) >= 4:
        _MASK_CACHE[value] = "••••••••"


def mask_secrets(text: str) -> str:
    masked = text
    for value in _MASK_CACHE:
        if value and value in masked:
            masked = masked.replace(value, "••••••••")
    return masked