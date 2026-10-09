#!/usr/bin/env bash
# Portal provisioning. Idempotent. Run as root on a fresh Debian/Ubuntu host.
#
#   ./provision.sh [--domain portal.example.com] [--skip-build]
#
# Provisions:
#   - users: portal (api/control), runner (build+run fabric, owns decrypt key)
#   - /srv/portal + Python venv + production deps
#   - frontend production bundle (frontend/dist, skipped with --skip-build)
#   - key material (api.key[.pub], runner.key[.pub]) — created ONLY if missing
#   - /etc/portal/{api,runner}.env from templates
#   - systemd units + nftables rules + Caddy
set -euo pipefail

DOMAIN="${1:-portal.example.com}"
SKIP_BUILD=0
for a in "$@"; do [ "$a" = "--skip-build" ] && SKIP_BUILD=1; done
APP=/srv/portal
DATA="$APP/.portal-data"
GIT_URL="${PORTAL_SOURCE_URL:-https://github.com/anomalyco/portal}"

[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }

echo "== users =="
id -u portal >/dev/null 2>&1 || useradd --system --create-home --shell /usr/sbin/nologin portal
id -u runner  >/dev/null 2>&1 || useradd --system --create-home --shell /usr/sbin/nologin runner

echo "== source + venv =="
if [ ! -d "$APP/.git" ]; then
  install -d -o portal -g portal "$APP"
  sudo -u portal git clone --depth=1 "$GIT_URL" "$APP/."
fi
[ -d "$APP/.venv" ] || python3 -m venv "$APP/.venv"
"$APP/.venv/bin/pip" install --upgrade pip >/dev/null
"$APP/.venv/bin/pip" install -r <(grep -vE '^(git\+|#)' "$APP/requirements.txt") >/dev/null

echo "== frontend bundle =="
if [ "$SKIP_BUILD" -eq 0 ]; then
  ( cd "$APP/frontend" && npm ci --silent && npm run build --silent )
  chown -R portal:portal "$APP/frontend/dist" 2>/dev/null || true
fi

echo "== data dirs =="
install -d -o portal -g portal -m 0750 "$DATA"
install -d -o portal -g portal -m 0750 "$DATA/logs"
install -d -o portal -g portal -m 0750 "$DATA/keys"
install -d -o runner -g runner -m 0750 "$APP/.runtime"

echo "== key material (idempotent) =="
KEYGEN="$APP/.venv/bin/python"
# api.key.pub + runner.key.pub must NOT be reused across installs in prod;
# generate a fresh set on the first provision only.
if [ ! -f "$DATA/keys/api.key" ]; then
  sudo -u portal "$KEYGEN" -c 'from api.config import Settings, ensure_crypto; ensure_crypto(Settings())'
fi
chmod 0600 "$DATA/keys/api.key"
chmod 0644 "$DATA/keys/api.key.pub"
chmod 0600 "$DATA/keys/runner.key"
chmod 0644 "$DATA/keys/runner.key.pub"
chown portal:portal "$DATA/keys"
chown runner:runner "$DATA/keys/runner.key" "$DATA/keys/runner.key.pub"

echo "== /etc/portal env =="
install -d -m 0750 /etc/portal
umask 077
cat > /etc/portal/api.env <<EOF
PORTAL_ENV=prod
PORTAL_PORTAL_DOMAIN=$DOMAIN
PORTAL_BASE_PROTOCOL=https
PORTAL_DATA_DIR=$DATA
EOF
cat > /etc/portal/runner.env <<EOF
PORTAL_ENV=prod
PORTAL_DATA_DIR=$DATA
PORTAL_RUNNER_EXECUTOR=podman
EOF
chown root:root /etc/portal/api.env /etc/portal/runner.env
chmod 0600 /etc/portal/api.env /etc/portal/runner.env

echo "== systemd units =="
install -d /etc/systemd/system
cp "$APP/ops/systemd/portal-api.service"    /etc/systemd/system/
cp "$APP/ops/systemd/portal-runner.service" /etc/systemd/system/
install -m 0644 "$APP/ops/nftables/portal.nft" /etc/nftables.d/portal.nft 2>/dev/null \
  || install -d /etc/nftables.d  # distribution without nftables.d; see RUNBOOK

systemctl daemon-reload
systemctl enable --now portal-api portal-runner

echo "== caddy =="
if ! command -v caddy >/dev/null; then
  # https://caddyserver.com/docs/install — Debian:
  apt-get install -y debian-keyring debian-archive-keyring apt-transport-https >/dev/null
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  echo 'deb [signed-by=/usr/share/keyrings/caddy-stable-archive-keyring.gpg] https://dl.cloudsmith.io/public/caddy/stable/deb/debian any-version main' \
    > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -qq
  apt-get install -y caddy >/dev/null
fi
install -d /etc/caddy
cp "$APP/ops/caddy/Caddyfile" /etc/caddy/Caddyfile
# Patch the hostname placeholder to the real domain.
sed -i "s/portal\.example\.com/$DOMAIN/" /etc/caddy/Caddyfile
systemctl enable --now caddy

echo
echo "== summary =="
systemctl --no-pager status portal-api --no-pager | head -4
systemctl --no-pager status portal-runner --no-pager | head -4
echo "Done. Endpoints: https://$DOMAIN (app), https://$DOMAIN/api/operator/health"
echo "Rotate runner.key pair via ops/RUNBOOK.md before every new prod deploy."