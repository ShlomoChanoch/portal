# Portal ops runbook

Runbook for operating a Portal deployment on a single host (control plane +
runner fabric + Caddy). Assumes `ops/provision.sh` has been run.

## Layout

| Path | Owner | Purpose |
|---|---|---|
| `/srv/portal` | portal | source checkout, venv |
| `/srv/portal/.portal-data` | portal | sqlite `portal.db`, keys, log store |
| `/srv/portal/.portal-data/keys` | portal | `api.key`(+pub), `runner.key.pub` |
| `/srv/portal/.portal-data/keys/runner.key` | runner | runner decrypt key (AD-9; `0600 runner:runner`) |
| `/etc/portal/{api,runner}.env` | root | runtime env (secrets) |
| `/etc/caddy/Caddyfile` | root | TLS + reverse proxy |
| `/etc/nftables.d/portal.nft` | root | egress/ingress policy |

Two service users exist on purpose: `portal` (api, can encrypt secrets) and
`runner` (build+run fabric, sole holder of the decrypting key). The api process
must never be able to read `runner.key`.

## 1. First boot & verification

```sh
systemctl status portal-api portal-runner caddy
curl -fsS https://<domain>/api/operator/health | jq .healthy         # true
curl -fsS https://<domain>/api/auth/me -i                           # 401 anonymous
```

`api/operator/health` returns `components.runner: ok` only when a fresh
heartbeat exists. A `degraded` runner within 15s of boot is expected.

## 2. Deploy flow smoke test

```sh
curl -fsS -c /tmp/c -X POST https://<domain>/api/auth/dev \
  -H 'content-type: application/json' -d '{"email":"ops@portal.local"}'
curl -fsS -b /tmp/c -X POST https://<domain>/api/deployments \
  -H 'content-type: application/json' -d '{"repo_url":"demo"}'
curl -fsS -b /tmp/c https://<domain>/api/deployments | jq '.deployments[0].status'
```

(Dev sign-in is disabled in prod — this is a proof-only shortcut; real prod uses
GitHub OAuth.)

## 2a. Rootless executor on a dev box (WSL2/Arch, verified)

The real build+run path works rootless on a single user account. Steps that
survive reboot/environment change:

```sh
# Arch (produces /usr/sbin/{podman,rootlesskit,slirp4netns} and buildkit tools)
sudo pacman -S --needed podman fuse-overlayfs buildkit rootlesskit slirp4netns
# one-time user namespace ranges + persistent session
PORTAL_USER="$(id -un)"
# NOTE: `sh -c` must be double-quoted so $PORTAL_USER expands here, as the
# invoking user. Single-quoting it (the natural way to "protect" the pattern)
# would expand it inside `sh -c` *as root* and write a useless root mapping.
# The inner single quotes keep the grep pattern literal.
sudo sh -c "grep -q '^$PORTAL_USER:' /etc/subuid || echo '$PORTAL_USER:100000:65536' >> /etc/subuid; \
            grep -q '^$PORTAL_USER:' /etc/subgid || echo '$PORTAL_USER:100000:65536' >> /etc/subgid"
sudo loginctl enable-linger "$PORTAL_USER"
grep -q "^$PORTAL_USER:" /etc/subuid || echo "WARN: no subuid range for $PORTAL_USER"

# BuildKit daemon, as packaged for systemd --user (rootlesskit + proxy socket)
systemctl --user enable --now buildkit-proxy.socket buildkit.service buildkit-proxy.service
export BUILDKIT_HOST=unix://$XDG_RUNTIME_DIR/buildkit/buildkitd.sock
```

Runner contract (see `runner/build.py` and `runner/executors/podman.py`):

- `BUILDKIT_ADDR` or `BUILDKIT_HOST` → buildctl socket. Default (`BUILDKIT_HOST`
  as above) targets the user daemon; the prod envelope sets the system one.
- `PORTAL_CONTAINER_NETWORK` → optional dedicated bridge added via
  `--network <name>` for the egress/isolation envelope. **Do not set on WSL2**:
  rootless netavark cannot apply nftables inside a user namespace
  ("netavark: nftables error"), so containers default to the pasta network with
  `127.0.0.1:<port>` publishing. `reconcile` matches by the `portal-` name
  prefix, so it is network-agnostic.
- `podman load` + `podman tag <digest> portal/<slug>:latest` give the run stage
  a stable image ref per deployment.

Harness (this repo):

- `make runner-podman` boots the runner on the real pipeline —
  `PORTAL_RUNNER_EXECUTOR=podman` plus `BUILDKIT_ADDR` defaulting to the
  §2a socket; an exported `BUILDKIT_ADDR` overrides it. `make dev-podman`
  boots api + that runner + the SPA in one go.
- End-to-end verification: boot (`make dev-podman` or
  api + `make runner-podman` + SPA), sign in as `dev@portal.local`, paste
  `demo` as the repo URL. Expect the deployment to reach `running` with a
  non-null `image_ref` and a `public_url` that serves HTTP 200 (Node on
  3000). `GET /api/operator/health` reports
  `runtime.executor == "podman"` and `resource_usage.running_with_image >= 1`.
- A repo Railpack cannot build (no manifest, no app server) falls back to the
  dev-only carrier container (plain Dockerfile, no web probe) —
  session-summary-2026-09-17 §8. Supported stacks build from Railpack's
  `plan.json` with the BuildKit frontend pinned to
  `railpack-frontend:v<railpack-version>` (§10, AD-13).

WSL2 known-good (some hard-won): storage driver `overlay` works rootless on the
WSL kernel; short-name pulls resolve through `registries.conf` shortnames;
base image/RUN pulls go through the rootlesskit slirp4netns stack. If podman
reports netavark/nft errors, the fix is *not* `firewall_driver = iptables` in
`~/.config/containers/containers.conf` — it is dropping the named network.

## 3. Software update

```sh
cd /srv/portal
sudo -u portal git pull --ff-only
sudo -u portal .venv/bin/pip install -r requirements.txt   # if changed
( cd frontend && npm ci && npm run build )                  # rebuild SPA
chown -R portal:portal frontend/dist
systemctl restart portal-api portal-runner
```

Deploy one environment at a time; verify health before touching the next.

## 4. Operator policy changes

Made live via the Operator page (config revisions, audit trail) or:
`PUT /api/operator/config` then `POST /api/operator/config/rollback`. Rollback
is revision-based and last-known-good; the event stream never rewrites history.

Troubleshooting policy: all denials are appended to `/api/operator/audit`
(`deploy.denied`, `admission.quota_hit`, `platform_halted`). Check
`GET /api/operator/audit?limit=50`.

## 5. Halt / resume

```sh
curl -fsS -b /tmp/c -X POST https://<domain>/api/operator/halt \
  -H 'content-type: application/json' -d '{"confirm":true,"reason":"maintenance"}'
curl -fsS -b /tmp/c -X POST https://<domain>/api/operator/halt/clear
```

Halt pauses new builds and runs; already-running instances are stopped by the
runner. `operator.halt_effective` appears exactly once per effective halt.

Clearing the halt is symmetric: the api enqueues a `resume` command and the
runner restarts the instances the halt stopped (`stopped` +
`halted_by_operator`), as cold starts from the persisted image. It does **not**
touch instances that were stopped by an operator, a tenant, or the idle reaper,
and it does **not** rebuild a build the halt interrupted — that stays `failed`.
`operator.resume_effective` carries `{restarted, failed}` slug lists, so a
partial resume is auditable in `/api/operator/audit`.

Two lossy details worth knowing before halting:

- **Halt discards queued work.** `halt_all` aborts every pending command
  platform-wide (other tenants' deploy/stop/delete included). Those rows stay in
  the command log as `aborted`; resume does not revive them — the tenant
  re-issues.
- **Halting is platform-wide and not scoped to a reason.** There is no dry run;
  the reason is recorded for the audit trail only.

A start requested during a halt is refused with `409 platform_halted`, both at
the api (no command is queued) and again in the runner.

## 6. Key rotation (runner + api pair)

Rotation invalidates secrets at rest; schedules a maintenance window.

```sh
systemctl stop portal-runner portal-api
# regenerate BOTH keypairs together, or decryption breaks:
rm /srv/portal/.portal-data/keys/{api,runner}.key{,.pub}
sudo -u portal .venv/bin/python -c \
  'from api.config import Settings, ensure_crypto; ensure_crypto(Settings())'
chown runner:runner /srv/portal/.portal-data/keys/runner.key{, .pub}
chmod 0600 /srv/portal/.portal-data/keys/runner.key
systemctl start portal-api portal-runner
```

Any secret rows encrypted under the old runner key will fail decode at run time
— re-save affected secrets via the Deployment settings, or back up + restore
secrets as part of the rotation (see §7).

## 7. Backup & restore

Control-plane `portal.db` is a single WAL-mode sqlite file at
`/srv/portal/.portal-data/portal.db` (plus `-wal`/`-shm` during writes). Use
`sqlite3` online backup rather than a raw `cp`:

```sh
sudo -u portal sqlite3 /srv/portal/.portal-data/portal.db ".backup /var/backups/portal/portal-$(date +%F).db"
```

Back up per-deployment logs under `.portal-data/logs` as flat files. Restore:
stop both units, replace `portal.db`, start, verify `GET /api/operator/health`.

## 8. Firewall

`ops/nftables/portal.nft` ships a default-deny posture for the runner's egress
and host ingress. Apply or reload:

```sh
nft -f /etc/nftables.d/portal.nft
```

On Debian, `nftables.service` includes `/etc/nftables.d/*.nft`; if the unit is
present, just `systemctl reload nftables`. Before tightening, keep the
ingress `tcp dport 22` (or move SSH off-host) and your VCS/registry IP sets in
sync with upstream `ipinfo.io/AS<num>/json` — ranges drift.

## 9. Incident notes

- **`binascii.Error … base64` in api log** — envelope keys double-encoded at a
  source level; regenerate keypairs (§6) — ship fixed code first.
- **`runner: degraded`** — no heartbeat ≤15s. Check `portal-runner` unit log;
  common: podman daemon not started, `PODMAN_RUNTIME_DIR` unwritable.
- **Secret round-trips fine but a deployment dies at start** — likely a
  decrypt failure from a mismatched keypair after partial rotation; re-save the
  secret.
- **SPA serves 404 on `/` paths** — frontend/dist missing or stale; run the
  build step in §3.
- **Events flooded (e.g. repeated `operator.halt_effective`)** — stale runner
  from before the halt-gating fix; upgrade and restart the runner (§3).