"""Simulated executor — dev mode (no container daemon required).

Runs a real HTTP server bound to 127.0.0.1 inside a thread; the pipeline probes
it with real HTTP. Serves the cloned static content when the stack is static,
otherwise a minimal landing page. Every request is written to the run log so the
whole paste → build → run → URL path works end-to-end in dev. Disabled by the
ops/ production envelope; the prefix "dev:" marks these instances.
"""

from __future__ import annotations

import http.server
import os
import socketserver
import threading

from runner.executors.base import Instance

LANDING_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{slug} — Portal dev facade</title>
<style>body{{font-family:system-ui,sans-serif;background:#120b05;color:#e8dcc8;display:grid;place-items:center;min-height:100vh;margin:0}}
main{{max-width:560px;padding:40px;border:1px solid #5c4310;border-radius:12px;background:#1a1208}}
.banner{{background:#f59e0b;color:#1a1208;font-weight:700;letter-spacing:.08em;text-transform:uppercase;font-size:12px;padding:6px 12px;border-radius:6px;display:inline-block;margin-bottom:16px}}
.chip{{display:inline-flex;align-items:center;gap:8px;color:#f59e0b;font-size:12px;font-weight:600;letter-spacing:.06em;border:1px solid #f59e0b;padding:2px 8px;border-radius:999px}}
h1{{font-size:26px;font-weight:600;color:#fde68a;margin:12px 0 8px}}
p{{line-height:1.6}} code{{background:#2a1d0a;padding:2px 6px;border-radius:4px;color:#fbbf24}}</style></head>
<body><main>
<div class="banner">Portal dev facade</div><span class="chip">DEV — not a real container</span><h1>{slug}</h1>
<p>This URL is served by Portal's <strong>simulated executor</strong>, a dev-only
stand-in. No OCI image is built and no Podman container is running behind this page.</p>
<p>In production the same pipeline runs Railpack → rootless BuildKit → rootless
Podman, and this URL serves whatever your app really renders.</p>
<p>Source repo: <code>{repo}</code></p></main></body></html>"""


def _handler_type(*, slug, repo, log_line, static_root):
    """Build a request handler closing over this instance's own view of the
    world (slug/repo/log sink/static root). No class-attribute sharing: log
    sinks stay plain functions (a class attribute would bind self and the line
    as two positional arguments)."""

    class InstanceHandler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, fmt, *args):
            if log_line:
                msg = fmt % args
                log_line(" ".join(self.address_string().split()) + "  " + msg)

        def do_GET(self):  # noqa: N802
            path = self.path.split("?", 1)[0]
            if static_root:
                self.directory = static_root
                base = os.path.realpath(static_root)
                candidate = os.path.realpath(os.path.join(static_root, path.lstrip("/") or "index.html"))
                if os.path.isfile(candidate) and os.path.commonpath([base, candidate]) == base:
                    return super().do_GET()
                self.path = "/index.html"
                return super().do_GET()
            body = LANDING_HTML.format(slug=slug, repo=repo)
            self.send_response(200)
            self.send_header("X-Portal-Dev-Facade", "1")
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body.encode())

    return InstanceHandler


class _ThreadingServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class SimulatedExecutor:
    name = "simulated"
    requires_image = False  # dev facade: starts from source, never consumes an image

    def __init__(self):
        self._running: dict[str, _ThreadingServer] = {}

    def start(self, *, slug, tenant_id, source_root, repo,
              static, env, ports, image_ref=None, log_line=None) -> Instance:
        host = "127.0.0.1"
        root = source_root if static and source_root and os.path.isdir(source_root) else None
        server = None
        for candidate in ports:
            try:
                server = _ThreadingServer((host, candidate),
                                          _handler_type(slug=slug, repo=repo or "",
                                                        log_line=log_line, static_root=root))
                break
            except OSError:
                continue
        if server is None:
            server = _ThreadingServer((host, 0),
                                      _handler_type(slug=slug, repo=repo or "",
                                                    log_line=log_line, static_root=root))
        port = server.server_address[1]

        threading.Thread(target=server.serve_forever, daemon=True).start()
        self._running[slug] = server
        if log_line:
            log_line(f"listening on {host}:{port} (simulated executor)")
        return Instance(slug=slug, tenant_id=tenant_id, executor=self.name,
                        ref=f"dev:{slug}", port=port, image_ref=None, extra={"server": server})

    def stop(self, instance: Instance) -> None:
        server = self._running.pop(instance.slug, None)
        if server:
            server.shutdown()
            server.server_close()

    def reconcile(self, store):
        return []