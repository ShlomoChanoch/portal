"""Health/port probe (AD-7, FR-4).

Probing completes before advertisement; a dead link is never presented as
success. Resolution order is user override → detected → candidate list.
"""

from __future__ import annotations

import socket
import time

import httpx

from core.url import candidate_ports


def _tcp_ok(host: str, port: int, timeout: float = 0.6) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def probe_port(host: str, port: int, timeout: float = 2.0) -> bool:
    """True when something answers HTTP on the port (2xx/3xx/4xx all count as
    alive — a 4xx is still a serving app)."""
    if not _tcp_ok(host, port, timeout=1.0):
        return False
    try:
        r = httpx.get(f"http://{host}:{port}/", timeout=timeout, follow_redirects=False)
        return True
    except (httpx.HTTPError, ValueError):
        return False


def probe_candidates(host: str, override: int | None, detected: int | None,
                     probe: callable = probe_port, round_timeout: float = 8.0) -> tuple[int | None, list[int]]:
    """Try candidate ports in AD-7 order. Returns (serving_port, probed)."""
    tried: list[int] = []
    deadline = time.monotonic() + round_timeout
    for port in candidate_ports(override, detected):
        if time.monotonic() > deadline:
            break
        tried.append(port)
        if probe(host, port):
            return port, tried
    return None, tried