"""Tests for the real builder (Railpack → BuildKit → Podman load, AD-4/IV-1).

The three binaries (buildctl, podman load, podman tag) are mocked by
replacing `_sh` with a scripted fake; the toolchain probe is mocked via
`shutil.which`. Nothing here touches the simulated facade.
"""
from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from api.config import Settings
from runner.build import BuildFailed
from runner.builders.real import RealBuilder
from runner.detect import Detection


def _which(name: str) -> str | None:
    return f"/bin/{name}" if name in ("buildctl", "podman") else None


def make_fake_sh(*results):
    results = list(results)
    calls = []

    def _sh(cmd, cwd, log):
        log("+ " + " ".join(cmd))
        calls.append((cmd, cwd))
        result = results.pop(0)
        return subprocess.CompletedProcess(
            cmd, result.get("rc", 0), result.get("out", ""), result.get("err", "")
        )

    return _sh, calls


@pytest.fixture()
def stack(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "index.js").write_text("console.log('hi')")
    plan_dir = tmp_path / "plan"
    plan_dir.mkdir()
    (plan_dir / "plan.json").write_text("{}")
    settings = Settings(data_dir=str(tmp_path / "data"), runner_executor="podman")
    det = Detection("node", "Node.js", 3000, ["Railpack detected node."], plan_dir,
                    "0.39.0")
    lines: list[str] = []
    builder = RealBuilder()
    return src, det, settings, lines, builder


def test_requires_plan_dir(tmp_path, stack):
    _src, _det, settings, lines, builder = stack
    no_plan = Detection("node", "Node.js", 3000, [], None)
    with pytest.raises(BuildFailed, match="IV-1"):
        builder.build(source_dir=tmp_path / "src", slug="appslide",
                      detect=no_plan, log=lines.append, settings=settings)


@pytest.mark.parametrize("missing", ["buildctl", "podman"])
def test_toolchain_missing_fails(tmp_path, stack, missing):
    src, det, settings, lines, builder = stack

    def _which_partial(name):
        return None if name == missing else f"/bin/{name}"

    with patch("shutil.which", side_effect=_which_partial), \
         patch("runner.builders.real._sh"):
        with pytest.raises(BuildFailed) as ei:
            builder.build(source_dir=src, slug="s", detect=det,
                          log=lines.append, settings=settings)
    assert "not installed" in str(ei.value)


def test_missing_plan_file_fails(tmp_path, stack, monkeypatch):
    src, det, settings, lines, builder = stack
    bare = tmp_path / "empty-plan"
    bare.mkdir()
    det.plan_dir = bare
    monkeypatch.setattr("shutil.which", lambda name: _which(name))
    with patch("runner.builders.real._sh"):
        with pytest.raises(BuildFailed, match="plan.json.*is missing"):
            builder.build(source_dir=src, slug="s", detect=det,
                          log=lines.append, settings=settings)


def test_success_returns_image_and_uses_plan(tmp_path, stack):
    src, det, settings, lines, builder = stack
    fake_sh, calls = make_fake_sh(
        {},
        {"out": "Loaded image: localhost/railsapp:latest\n"},
        {},
    )
    with patch("shutil.which", side_effect=_which), \
         patch("runner.builders.real._sh", side_effect=fake_sh):
        image = builder.build(source_dir=src, slug="appslug", detect=det,
                              log=lines.append, settings=settings)

    assert image == "portal/appslug:latest"
    buildctl_cmd, buildctl_cwd = calls[0]
    assert buildctl_cmd == [
        "/bin/buildctl", "build",
        "--frontend=dockerfile.v0",
        "--local", f"context={src.resolve()}",
        "--local", f"dockerfile={det.plan_dir.resolve()}",
        "--opt", "filename=plan.json",
        "--opt", "build-arg:BUILDKIT_SYNTAX=ghcr.io/railwayapp/railpack-frontend:v0.39.0",
        "--output", f"type=oci,dest={det.plan_dir.resolve()}/appslug.oci",
    ]
    assert str(buildctl_cwd) == str(src.resolve())
    assert calls[1] == (["/bin/podman", "load", "-i",
                         str(det.plan_dir.resolve() / "appslug.oci")],
                        str(settings.data_dir))
    assert calls[2] == (["/bin/podman", "tag", "latest",
                         "portal/appslug:latest"], str(settings.data_dir))
    assert "Plan: Node.js (Railpack)" in lines


def test_railpack_branch_keeps_existing_v_prefix(stack):
    """AD-13: a railpackVersion already v-prefixed (future toolchains) is not
    double-prefixed — the published tag is consumed as-is."""
    src, det, settings, lines, builder = stack
    det.railpack_version = "v0.40.0"
    fake_sh, calls = make_fake_sh(
        {},
        {"out": "Loaded image: localhost/railsapp:latest\n"},
        {},
    )
    with patch("shutil.which", side_effect=_which), \
         patch("runner.builders.real._sh", side_effect=fake_sh):
        builder.build(source_dir=src, slug="s", detect=det,
                      log=lines.append, settings=settings)
    cmd = calls[0][0]
    assert any(a.endswith("railpack-frontend:v0.40.0") for a in cmd)
    assert not any("railpack-frontend:v:v0.40.0" in a for a in cmd)


def test_railpack_branch_requires_pinned_frontend(stack):
    """AD-13: without a railpackVersion the railpack build must fail loudly —
    the untagged `latest` frontend is never used."""
    src, det, settings, lines, builder = stack
    det.railpack_version = None
    with patch("shutil.which", side_effect=_which), \
         patch("runner.builders.real._sh") as sh_mock:
        with pytest.raises(BuildFailed) as ei:
            builder.build(source_dir=src, slug="s", detect=det,
                          log=lines.append, settings=settings)
    assert "AD-13" in str(ei.value)
    sh_mock.assert_not_called()


def test_railpack_branch_whitespace_version_fails(stack):
    """AD-13: a blank railpackVersion is as unusable as a missing one."""
    src, det, settings, lines, builder = stack
    det.railpack_version = "   "
    with patch("shutil.which", side_effect=_which), \
         patch("runner.builders.real._sh") as sh_mock:
        with pytest.raises(BuildFailed) as ei:
            builder.build(source_dir=src, slug="s", detect=det,
                          log=lines.append, settings=settings)
    assert "AD-13" in str(ei.value)
    sh_mock.assert_not_called()


@pytest.mark.parametrize("env_name", ["BUILDKIT_ADDR", "BUILDKIT_HOST"])
def test_buildkit_addr_forwarded(monkeypatch, stack, env_name):
    src, det, settings, lines, builder = stack
    monkeypatch.setenv(env_name, "unix:///tmp/buildkit.sock")
    fake_sh, calls = make_fake_sh({}, {"out": "Loaded image(s): abc123\n"}, {})
    with patch("shutil.which", side_effect=_which), \
         patch("runner.builders.real._sh", side_effect=fake_sh):
        builder.build(source_dir=src, slug="s", detect=det,
                      log=lines.append, settings=settings)
    cmd = calls[0][0]
    assert cmd[cmd.index("--addr") + 1] == "unix:///tmp/buildkit.sock"


def test_buildctl_failure_reports_stderr(stack):
    src, det, settings, lines, builder = stack
    fake_sh, _calls = make_fake_sh({"rc": 1, "err": "syntax error in plan.json\n"})
    with patch("shutil.which", side_effect=_which), \
         patch("runner.builders.real._sh", side_effect=fake_sh):
        with pytest.raises(BuildFailed) as ei:
            builder.build(source_dir=src, slug="s", detect=det,
                          log=lines.append, settings=settings)
    assert "syntax error in plan.json" in str(ei.value)


def test_podman_load_failure_reports_stderr(stack):
    src, det, settings, lines, builder = stack
    fake_sh, _calls = make_fake_sh({}, {"rc": 1, "err": "load failed"})
    with patch("shutil.which", side_effect=_which), \
         patch("runner.builders.real._sh", side_effect=fake_sh):
        with pytest.raises(BuildFailed) as ei:
            builder.build(source_dir=src, slug="s", detect=det,
                          log=lines.append, settings=settings)
    assert "load failed" in str(ei.value)


def test_podman_tag_failure_reports_stderr(stack):
    src, det, settings, lines, builder = stack
    fake_sh, _calls = make_fake_sh({}, {"out": "Loaded image: localhost/railsapp:latest\n"},
                                   {"rc": 1, "err": "tag failed"})
    with patch("shutil.which", side_effect=_which), \
         patch("runner.builders.real._sh", side_effect=fake_sh):
        with pytest.raises(BuildFailed) as ei:
            builder.build(source_dir=src, slug="s", detect=det,
                          log=lines.append, settings=settings)
    assert "tag failed" in str(ei.value)


def test_failures_fall_back_to_generic_message(stack):
    src, det, settings, lines, builder = stack
    fake_sh, _calls = make_fake_sh({"rc": 1})
    with patch("shutil.which", side_effect=_which), \
         patch("runner.builders.real._sh", side_effect=fake_sh):
        with pytest.raises(BuildFailed, match="buildctl build failed"):
            builder.build(source_dir=src, slug="s", detect=det,
                          log=lines.append, settings=settings)


# --- carrier fallback (dev-only; generic container holding the repo source) ---

def _carrier_detection(tmp_path) -> Detection:
    from runner.detect import carrier_detection

    plan_dir = tmp_path / "carrier-plan"
    plan_dir.mkdir()
    return carrier_detection(plan_dir)


def test_carrier_missing_dockerfile_fails(tmp_path, stack, monkeypatch):
    src, _det, settings, lines, builder = stack
    det = Detection("carrier", "Generic", None, [], tmp_path / "bare-plan")
    det.plan_dir.mkdir()
    monkeypatch.setattr("shutil.which", lambda name: _which(name))
    with patch("runner.builders.real._sh"):
        with pytest.raises(BuildFailed, match="Dockerfile.*missing"):
            builder.build(source_dir=src, slug="s", detect=det,
                          log=lines.append, settings=settings)


def test_carrier_build_uses_plain_dockerfile(tmp_path, stack):
    src, _det, settings, lines, builder = stack
    det = _carrier_detection(tmp_path)
    fake_sh, calls = make_fake_sh(
        {},
        {"out": "Loaded image: localhost/carrier:latest\n"},
        {},
    )
    with patch("shutil.which", side_effect=_which), \
         patch("runner.builders.real._sh", side_effect=fake_sh):
        image = builder.build(source_dir=src, slug="carrierapp", detect=det,
                              log=lines.append, settings=settings)

    assert image == "portal/carrierapp:latest"
    buildctl_cmd, buildctl_cwd = calls[0]
    assert "filename=Dockerfile" in buildctl_cmd
    assert "filename=plan.json" not in buildctl_cmd
    assert not any("BUILDKIT_SYNTAX" in arg for arg in buildctl_cmd)
    assert "--frontend=dockerfile.v0" in buildctl_cmd
    assert str(buildctl_cwd) == str(src.resolve())
    assert calls[2] == (["/bin/podman", "tag", "latest",
                         "portal/carrierapp:latest"], str(settings.data_dir))