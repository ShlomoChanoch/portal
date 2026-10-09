"""Stack detection (FR-2) — Railpack owns it in the real pipeline.

Prod detection is one `railpack prepare` call: it emits the immutable build
plan (plan.json) and the metadata parsed here (info.json), and the plan
directory flows into the build stage unchanged (AD-13 / IV-1). The hand-written
heuristic is kept only as the simulated fallback so an offline dev loop runs
without tooling.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from core.errors import UnsupportedStack

# Simulated-only heuristic tables (dev fallback — Railpack owns prod detection).
STACK_META = {
    "node": {"name": "Node.js", "port": 3000},
    "python": {"name": "Python", "port": 8000},
    "go": {"name": "Go", "port": 8080},
    "static": {"name": "Static site", "port": 80},
    "ruby": {"name": "Ruby", "port": 3000},
    "php": {"name": "PHP", "port": 8080},
}

# Railpack provider id (info.json.detectedProviders) → label and port hint.
RAILPACK_META = {
    "node": {"name": "Node.js", "port": 3000},
    "bun": {"name": "Bun", "port": 3000},
    "deno": {"name": "Deno", "port": 8000},
    "go": {"name": "Go", "port": 8080},
    "python": {"name": "Python", "port": 8000},
    "ruby": {"name": "Ruby", "port": 3000},
    "php": {"name": "PHP", "port": 8080},
    "static": {"name": "Static site", "port": 80},
}


@dataclass
class Detection:
    stack: str
    name: str
    port_hint: int | None
    source_notes: list[str]
    plan_dir: Path | None = None  # immutable railpack plan (IV-1); None on the simulated path
    railpack_version: str | None = None  # pins the BuildKit frontend (AD-13); set by railpack_detect

# Dev-only escape hatch: when no supported stack is detected (the IV-1 Railpack
# verdict), a generic container that carries the cloned source lets the
# build/run machinery be exercised end-to-end. It never serves a web app — the
# product contract ("repo → running web app") is unchanged. Not a prod default.
CARRIER_DOCKERFILE = """FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git \\
    && rm -rf /var/lib/apt/lists/*
WORKDIR /repo
COPY . /repo
ENV PYTHONUNBUFFERED=1
CMD ["sh", "-c", "while true; do sleep 3600; done"]
"""


def carrier_detection(plan_dir: Path) -> Detection:
    """Detection for the carrier fallback: writes a plain Dockerfile that copies
    the cloned source into a generic Python + git image (no Railpack plan, so
    no probe — nothing listens)."""
    plan_dir.mkdir(parents=True, exist_ok=True)
    (plan_dir / "Dockerfile").write_text(CARRIER_DOCKERFILE)
    return Detection(
        stack="carrier", name="Generic (Python + git)", port_hint=None,
        source_notes=["No supported stack detected — carrier container with the repo source."],
        plan_dir=plan_dir,
    )


def _has(root: Path, *names: str) -> bool:
    return any((root / n).exists() for n in names)


def detect(src: Path, log) -> Detection:
    notes: list[str] = []
    if _has(src, "package.json"):
        ports = []
        pkg = (src / "package.json").read_text(errors="replace") if (src / "package.json").exists() else "{}"
        if '"start"' in pkg or '"dev"' in pkg or '"serve"' in pkg:
            notes.append("package.json defines a start script.")
        return Detection("node", STACK_META["node"]["name"], STACK_META["node"]["port"], notes)
    if _has(src, "requirements.txt", "pyproject.toml", "Pipfile", "setup.py", "main.py", "app.py"):
        return Detection("python", STACK_META["python"]["name"], STACK_META["python"]["port"], notes)
    if _has(src, "go.mod"):
        return Detection("go", STACK_META["go"]["name"], STACK_META["go"]["port"], notes)
    if _has(src, "Gemfile"):
        return Detection("ruby", STACK_META["ruby"]["name"], STACK_META["ruby"]["port"], notes)
    if _has(src, "composer.json", "index.php"):
        return Detection("php", STACK_META["php"]["name"], STACK_META["php"]["port"], notes)
    if _has(src, "index.html") or any(src.iterdir()) is False:
        notes.append("Found static content.")
        return Detection("static", STACK_META["static"]["name"], None, notes)
    if _has(src, "Dockerfile"):
        notes.append("We saw a Dockerfile — Portal builds from it, so the URL is all you need.")
    raise UnsupportedStack(
        "Could not detect a supported stack. We saw "
        + ("a Dockerfile — Portal builds from it with Railpack; the URL is all you need."
           if notes else "no familiar project files — is the repo URL a real project?")
        + " If your app listens on a non-standard port, set it on the Deploy form.",
    )


def railpack_detect(src: Path, log, *, plan_dir: Path) -> Detection:
    """Detect with Railpack (AD-13) and pre-generate the immutable build plan.

    One `railpack prepare` call writes `plan_dir/plan.json` (the plan the build
    stage consumes unchanged, IV-1) and `plan_dir/info.json` (the metadata
    parsed here for stack + port hint). Raises BuildFailed when Railpack is
    missing and UnsupportedStack when it can't plan the repo.
    """
    railpack = os.environ.get("RAILPACK_BIN") or shutil.which("railpack")
    if not railpack:
        from runner.build import BuildFailed  # local import; build.py imports Detection

        raise BuildFailed(
            "Production tooling (railpack) is not installed. ops/ provisions it; "
            "the simulated executor is dev-only."
        )
    plan_dir.mkdir(parents=True, exist_ok=True)
    log("Detecting with Railpack.")
    r = subprocess.run(
        [railpack, "prepare", str(src),
         "--plan-out", str(plan_dir / "plan.json"),
         "--info-out", str(plan_dir / "info.json")],
        cwd=str(src), capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise UnsupportedStack(
            "Railpack could not produce a build plan for this repo. "
            + (r.stderr.strip() or "This project doesn't look buildable yet.")
        )
    try:
        info = json.loads((plan_dir / "info.json").read_text(errors="replace"))
    except (OSError, json.JSONDecodeError) as exc:
        raise UnsupportedStack("Railpack returned unusable detection metadata.") from exc
    if not info.get("success"):
        raise UnsupportedStack("Railpack could not build this repo.")
    providers = [p for p in (info.get("detectedProviders") or []) if p in RAILPACK_META]
    if not providers:
        raise UnsupportedStack("Railpack planned the repo but detected no supported provider.")
    stack = providers[0]
    meta = RAILPACK_META[stack]
    notes = [f"Railpack detected {', '.join(providers)}."]
    return Detection(stack=stack, name=meta["name"], port_hint=meta["port"],
                     source_notes=notes, plan_dir=plan_dir,
                     railpack_version=info.get("railpackVersion"))