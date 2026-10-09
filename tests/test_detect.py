"""Detection tests — Railpack metadata parsing (AD-13/IV-1).

`railpack prepare` emits plan.json + info.json; railpack_detect is the single
reader of info.json (stack, port hint, and the railpackVersion that pins the
BuildKit frontend per AD-13). The railpack binary is mocked by writing the
info-out/plan-out files it would produce.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import runner.detect as detect_mod


def _fake_railpack(info_payload: dict):
    def _run(args, cwd, capture_output, text):
        info_out = Path(args[args.index("--info-out") + 1])
        info_out.parent.mkdir(parents=True, exist_ok=True)
        info_out.write_text(json.dumps(info_payload))
        plan_out = Path(args[args.index("--plan-out") + 1])
        plan_out.parent.mkdir(parents=True, exist_ok=True)
        plan_out.write_text("{}")
        return subprocess.CompletedProcess(args, 0, "", "")

    return _run


def _detect(tmp_path, info_payload, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.py").write_text("print('hi')")
    plan_dir = tmp_path / "plan"
    plan_dir.mkdir()
    monkeypatch.setattr("shutil.which",
                        lambda name: "/fake/railpack" if name == "railpack" else None)
    monkeypatch.setattr(detect_mod.subprocess, "run",
                        _fake_railpack(info_payload))
    lines: list[str] = []
    return detect_mod.railpack_detect(src, lines.append, plan_dir=plan_dir)


def test_railpack_detect_carries_version(tmp_path, monkeypatch):
    """info.json railpackVersion flows onto Detection for the AD-13 pin."""
    det = _detect(tmp_path, {
        "success": True,
        "detectedProviders": ["python"],
        "railpackVersion": "0.39.0",
    }, monkeypatch)
    assert det.stack == "python"
    assert det.port_hint == 8000
    assert det.plan_dir is not None
    assert det.railpack_version == "0.39.0"
    assert (det.plan_dir / "plan.json").exists()


def test_railpack_detect_missing_version_is_none(tmp_path, monkeypatch):
    """A stale toolchain emitting no railpackVersion leaves it None — the
    builder backstops with a fail-loud BuildFailed (AD-13), never `latest`."""
    det = _detect(tmp_path, {
        "success": True,
        "detectedProviders": ["python"],
    }, monkeypatch)
    assert det.railpack_version is None