"""Executor contract semantics (TODO 9): a cold start's image requirement is a
declared executor capability (`requires_image`), not an inference from the
executor name. The podman executor fails loudly (AD-12) before any podman
binary call, so these tests are fully offline.
"""
from __future__ import annotations

import pytest

from runner.executors.base import supports_image_ref
from runner.executors.podman import PodmanError, PodmanExecutor


class _ImageExecutor:
    """Podman-like: a cold start requires a persisted image."""

    name = "img"
    requires_image = True


class _SourceExecutor:
    """Dev-like: starts from source; the image ref is irrelevant."""

    name = "src"
    requires_image = False


def test_supports_image_ref_requires_image_for_image_executors():
    assert supports_image_ref("portal/x:latest", _ImageExecutor()) is True
    assert supports_image_ref(None, _ImageExecutor()) is False


def test_supports_image_ref_ignores_ref_for_source_executors():
    assert supports_image_ref(None, _SourceExecutor()) is True
    assert supports_image_ref("ignored-ref", _SourceExecutor()) is True


def test_podman_executor_declares_image_requirement():
    assert PodmanExecutor().requires_image is True
    assert PodmanExecutor().name == "podman"


def test_simulated_executor_does_not_require_image():
    from runner.executors.simulated import SimulatedExecutor

    assert SimulatedExecutor().requires_image is False


def test_podman_start_without_image_raises_before_touching_podman():
    """The AD-12 guard fires before `podman run`, so it is offline-safe."""
    ex = PodmanExecutor()
    with pytest.raises(PodmanError, match="rebuild is required"):
        ex.start(slug="x", tenant_id=1, source_root=None, repo="demo",
                 static=False, env={}, ports=[3000], image_ref=None, log_line=print)


def test_simulated_landing_page_is_dev_facade_banner(tmp_path):
    """TODO 10: the non-static fallback page must read as a dev-only facade,
    never as canonical product behavior (banner copy + X-Portal-Dev-Facade)."""
    import httpx

    from runner.executors.simulated import SimulatedExecutor

    ex = SimulatedExecutor()
    instance = ex.start(slug="dev-facade-1", tenant_id=1,
                        source_root=str(tmp_path / "missing"),
                        repo="demo", static=False, env={}, ports=[0], log_line=None)
    try:
        r = httpx.get(f"http://127.0.0.1:{instance.port}/", timeout=5)
        assert r.status_code == 200
        assert r.headers.get("x-portal-dev-facade") == "1"
        assert "text/html" in r.headers["content-type"]
        assert "Portal dev facade" in r.text
        assert "simulated executor" in r.text
        assert "This instance is served by" not in r.text
    finally:
        ex.stop(instance)


def test_simulated_static_serves_real_content_without_banner(tmp_path):
    """A static stack keeps serving the real files: no facade banner/header."""
    import httpx

    from runner.executors.simulated import SimulatedExecutor

    (tmp_path / "index.html").write_text("<h1>real static app</h1>")
    ex = SimulatedExecutor()
    instance = ex.start(slug="static-1", tenant_id=1, source_root=str(tmp_path),
                        repo="demo", static=True, env={}, ports=[0], log_line=None)
    try:
        r = httpx.get(f"http://127.0.0.1:{instance.port}/", timeout=5)
        assert r.status_code == 200
        assert r.headers.get("x-portal-dev-facade") is None
        assert "<h1>real static app</h1>" in r.text
    finally:
        ex.stop(instance)