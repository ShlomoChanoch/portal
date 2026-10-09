"""Podman executor — production (FR-10, AD-5, AD-6).

Every invocation happens as the tenant's unprivileged portal system user
(`portal-u<tenant-id>`, ops/ owns the profile). Instances bind
127.0.0.1:<port> only; nftables blocks cross-tenant loopback (AD-5) and
restricts egress (FR-14). This module is exercised by the ops/ host envelope;
it fails loudly when podman is unavailable.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
from dataclasses import dataclass

from runner.executors.base import Instance, supports_image_ref

NAME_TAG = "portal/instance"

# Dedicated bridge for the ops/ envelope (AD-5: per-tenant nftables isolation).
# Rootless podman on WSL2 cannot apply netavark/nftables in a user namespace,
# so the network is applied only when the envelope sets PORTAL_CONTAINER_NETWORK;
# default (pasta, publish 127.0.0.1) is used elsewhere.


@dataclass
class PodmanError(Exception):
    msg: str = ""


def _podman(*args, **kwargs) -> subprocess.CompletedProcess:
    binary = shutil.which("podman")
    if not binary:
        raise RuntimeError(
            "podman is not installed. The prod envelope (ops/) provisions it per "
            "tenant, rootless, with nftables egress; dev uses the simulated executor."
        )
    cmd = [binary, *args]
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class PodmanExecutor:
    name = "podman"
    requires_image = True  # AD-12: cold start runs the persisted image

    def start(self, *, slug, tenant_id, source_root, repo,
              static, env, ports, image_ref=None, log_line=None) -> Instance:
        if not supports_image_ref(image_ref, self):
            raise PodmanError("persisted image is missing; a rebuild is required (AD-12)")
        container = f"portal-{slug}"
        _podman("rm", "-f", container)  # clear stale/exited container of same slug
        host_port = _free_port()
        container_port = (ports[0] if ports else 3000)
        env_args = []
        for k, v in (env or {}).items():
            env_args += ["--env", f"{k}={v}"]
        r = _podman(
            "run", "-d", "--name", container,
            *(["--network", os.environ["PORTAL_CONTAINER_NETWORK"]]
              if os.environ.get("PORTAL_CONTAINER_NETWORK") else []),
            "--publish", f"127.0.0.1:{host_port}:{container_port}",
            "--cpus", "1",                   # quota caps applied by ops/ via podman
            "--memory", "1024m",
            *env_args,
            image_ref or "portal:latest",
        )
        if r.returncode != 0:
            raise PodmanError(r.stderr.strip() or "podman run failed")
        if log_line:
            log_line(f"container {container} started (image {image_ref})")
        return Instance(slug=slug, tenant_id=tenant_id, executor=self.name,
                        ref=container, port=host_port, image_ref=image_ref)

    def stop(self, instance: Instance) -> None:
        if not instance.ref:
            return
        _podman("rm", "-f", instance.ref)

    def is_running(self, instance: Instance) -> bool:
        if not instance.ref:
            return False
        r = _podman("inspect", "--format", "{{.State.Running}}", instance.ref)
        return r.returncode == 0 and r.stdout.strip() == "true"

    def reconcile(self, store):
        """Rehydrate running instances after a runner restart (prod)."""
        r = _podman("ps", "--format", "{{.Names}} {{.Ports}}")
        instances = []
        if r.returncode != 0:
            return instances
        for line in r.stdout.splitlines():
            name, _ports = line.split(None, 1)
            if not name.startswith("portal-"):
                continue
            slug = name[len("portal-"):]
            proj = store.get_projection(slug)
            if proj and proj.get("status") in ("running", "starting"):
                instances.append(Instance(
                    slug=slug, tenant_id=proj["tenant_id"], executor=self.name,
                    ref=name, port=proj.get("mapped_port") or 0,
                    image_ref=proj.get("image_ref")))
        return instances