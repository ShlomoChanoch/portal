"""Real builder — Railpack → BuildKit → Podman load (AD-4, IV-1).

ops/ owns the toolchain. The immutable plan (plan.json + info.json) is
produced once in the detect stage and consumed here unchanged.
"""

from __future__ import annotations

import os
import shutil

from runner.build import BuildFailed, _sh


def _frontend_tag(version: str) -> str:
    """Map info.json railpackVersion to the published BuildKit frontend tag
    (AD-13): Railway publishes `ghcr.io/railwayapp/railpack-frontend:v<ver>`
    (e.g. v0.39.0) while info.json reports it as `0.39.0`."""
    tag = version.strip()
    return tag if tag.startswith("v") else f"v{tag}"


class RealBuilder:
    name = "podman"

    def build(self, *, source_dir, slug: str, detect, log, settings) -> str:
        source_dir = source_dir.resolve()
        if detect.plan_dir is None:
            raise BuildFailed(
                "The podman path requires a Railpack plan (Detection.plan_dir) — "
                "run railpack_detect before the build stage (IV-1)."
            )
        plan_dir = detect.plan_dir.resolve()
        buildctl = shutil.which("buildctl")
        podman = shutil.which("podman")
        if not (buildctl and podman):
            raise BuildFailed(
                "Production tooling is not installed here (buildctl, podman). "
                "ops/ provisions them; the simulated executor is dev-only."
            )
        if detect.stack == "carrier":
            # Dev-only carrier fallback: plain Dockerfile (written by
            # carrier_detection), no Railpack plan — no BUILDKIT_SYNTAX pin.
            dockerfile = plan_dir / "Dockerfile"
            if not dockerfile.exists():
                raise BuildFailed(
                    "Carrier Dockerfile is missing — cannot build (IV-1)."
                )
            log("Plan: Generic (carrier) — plain Dockerfile build, no Railpack.")
            frontend_args = ["--opt", "filename=Dockerfile"]
        else:
            plan = plan_dir / "plan.json"
            if not plan.exists():
                raise BuildFailed("Railpack plan (plan.json) is missing — cannot build (IV-1).")
            log(f"Plan: {detect.name} (Railpack)")
            if not (detect.railpack_version or "").strip():
                raise BuildFailed(
                    "Missing railpackVersion in info.json — cannot pin the BuildKit "
                    "frontend (AD-13). Upgrade the railpack toolchain."
                )
            frontend_args = [
                "--opt", "filename=plan.json",
                "--opt",
                f"build-arg:BUILDKIT_SYNTAX=ghcr.io/railwayapp/railpack-frontend:{_frontend_tag(detect.railpack_version)}",
            ]
        oci = plan_dir / f"{slug}.oci"
        bk_addr = os.environ.get("BUILDKIT_ADDR") or os.environ.get("BUILDKIT_HOST")
        r = _sh([
            buildctl, "build",
            *(["--addr", bk_addr] if bk_addr else []),
            "--frontend=dockerfile.v0",
            "--local", "context=" + str(source_dir),
            "--local", f"dockerfile={plan_dir}",
            *frontend_args,
            "--output", f"type=oci,dest={oci}",
        ], cwd=str(source_dir), log=log)
        if r.returncode != 0:
            raise BuildFailed(r.stderr.strip() or "buildctl build failed")
        r = _sh([podman, "load", "-i", str(oci)], cwd=settings.data_dir, log=log)
        if r.returncode != 0:
            raise BuildFailed(r.stderr.strip() or "podman load failed")
        image = f"portal/{slug}:latest"
        r = _sh([podman, "tag",
                 r.stdout.strip().splitlines()[-1].split(":")[-1],
                 image], cwd=settings.data_dir, log=log)
        if r.returncode != 0:
            raise BuildFailed(r.stderr.strip() or "podman tag failed")
        return image