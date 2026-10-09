"""Build stage — shared helpers and exception (AD-4, IV-1).

The actual builder implementations live in runner/builders/:
  * real.py    — Railpack → BuildKit → Podman load (prod, ops/ owns toolchain)
  * simulated.py — progress lines, no image (dev facade)

The Builder port is defined in core/ports.py; the runner composes it via
make_builder in runner/main.py.
"""

from __future__ import annotations

import subprocess


class BuildFailed(Exception):
    pass


def _sh(cmd, cwd, log) -> subprocess.CompletedProcess:
    log("+ " + " ".join(cmd))
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
