"""Simulated builder — dev facade (no tooling required).

Produces progress lines and returns no image reference. Disabled by the
ops/ production envelope; the real path (real.py) owns prod.
"""

from __future__ import annotations

import time


class SimulatedBuilder:
    name = "simulated"

    def build(self, *, source_dir, slug: str, detect, log,
              settings) -> None:
        log(f"{detect.name} image (simulated) — no container daemon needed in dev.")
        delay = settings.simulate_build_delay if settings.simulate_build_delay is not None else 2.0
        total = max(0, int(delay))
        for i in range(total):
            log(f"… preparing layers {i + 1}/{total}")
            if total:
                time.sleep(delay / total)
        log(f"Build complete ({detect.name}).")
        return None