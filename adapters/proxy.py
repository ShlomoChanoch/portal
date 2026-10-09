"""Public URL resolution (FR-4 / AD-6).

  prod: https://<slug>.<portal-domain>  — Caddy terminaifies TLS on the one
        managed domain; every instance binds 127.0.0.1:<mapped-port> only.
  dev:  http://localhost:<mapped-port>  — no Caddy, no TLS (dev envelope).

A Public URL is advertised only when the Instance is running/healthy (AD-6);
the api and UI never mint a URL for any other status.
"""

from __future__ import annotations


def resolve_public_url(slug: str, mapped_port: int, *, env: str,
                       domain: str, prod_protocol: str = "https",
                       dev_protocol: str = "http") -> str:
    if env == "prod":
        return f"{prod_protocol}://{slug}.{domain}"
    return f"{dev_protocol}://127.0.0.1:{mapped_port}"


def instance_bind() -> tuple[str, int]:
    """All instances bind loopback only (AD-6). Port assigned by the executor."""
    return ("127.0.0.1", 0)