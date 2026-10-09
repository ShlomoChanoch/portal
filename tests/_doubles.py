"""Standalone test doubles for the runner ports — independent of the runtime
simulated facade (runner/executors/simulated.py, runner/builders/simulated.py).

The Build stage is a no-op returning image_ref=None; the Executor runs a real
loopback HTTP server so probing and the full deploy → running transition work
offline, without any container tooling.
"""

from __future__ import annotations

import http.server
import socketserver
import threading

from runner.executors.base import Instance


class _ThreadingServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class _OKHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):  # noqa: D102
        pass


class FakeBuilder:
    name = "fake"

    def build(self, *, source_dir, slug: str, detect, log, settings) -> None:
        log(f"{detect.name} image (fake build) — no container daemon.")
        return None


class LoopbackExecutor:
    """A real loopback HTTP server so probe/advertise behave like production.
    Dev-like: starts from source, never consumes a persisted image."""

    name = "fake"
    requires_image = False

    def __init__(self):
        self._servers: dict[str, _ThreadingServer] = {}

    def start(self, *, slug, tenant_id, source_root, repo,
              static, env, ports, image_ref=None, log_line=None) -> Instance:
        host = "127.0.0.1"
        server = None
        for candidate in ports:
            try:
                server = _ThreadingServer((host, candidate), _OKHandler)
                break
            except OSError:
                continue
        if server is None:
            server = _ThreadingServer((host, 0), _OKHandler)
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self._servers[slug] = server
        if log_line:
            log_line(f"listening on {host}:{port} (fake executor)")
        return Instance(slug=slug, tenant_id=tenant_id, executor=self.name,
                        ref=f"fake:{slug}", port=port, image_ref=None,
                        extra={"server": server})

    def stop(self, instance: Instance) -> None:
        server = self._servers.pop(instance.slug, None)
        if server:
            server.shutdown()
            server.server_close()

    def reconcile(self, store):
        return []


class ImageRequiringExecutor(LoopbackExecutor):
    """Podman-like double for lifecycle tests: a cold start requires a persisted
    image (missing → audited rebuild, AD-12), and the ref survives
    start/advertise exactly like the real PodmanExecutor."""

    requires_image = True

    def start(self, *, slug, tenant_id, source_root, repo,
              static, env, ports, image_ref=None, log_line=None) -> Instance:
        inst = super().start(slug=slug, tenant_id=tenant_id, source_root=source_root,
                             repo=repo, static=static, env=env, ports=ports,
                             image_ref=image_ref, log_line=log_line)
        inst.image_ref = image_ref  # podman preserves the persisted ref
        return inst