"""SQLite state store (WAL) shared by api and runner on the single host.

Schema grants (AD-3):
  * `events`      — append-only to everyone; a TRIGGER makes this true at the
                    schema level (FR-15 / AD-10).
  * `deployments` — api-owned metadata (repo_url, port override, secret refs).
  * `projection`  — runner-owned lifecycle projection. The api reads it through
                    the read-only `deployment_status` VIEW and holds no write
                    grant to it; the runner writes it and holds no write grant
                    to `deployments`/`secrets`.
  * `commands`    — api → runner queue. The api enqueues; the runner consumes.
  * `config`      — operator policy (quotas, allow-list, halt): adjustable
                    without a redeploy (AD-8). Revisions enable last-known-good
                    rollback.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tenants (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT NOT NULL UNIQUE,
  role TEXT NOT NULL DEFAULT 'user',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS deployments (
  id TEXT PRIMARY KEY,
  tenant_id INTEGER NOT NULL REFERENCES tenants(id),
  repo_url TEXT NOT NULL,
  port_override INTEGER,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id INTEGER NOT NULL,
  deployment_id TEXT NOT NULL DEFAULT '',
  type TEXT NOT NULL,
  actor TEXT NOT NULL DEFAULT '',
  meta TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS commands (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id INTEGER NOT NULL,
  deployment_id TEXT NOT NULL,
  type TEXT NOT NULL,
  actor TEXT NOT NULL DEFAULT '',
  payload TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'pending',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS projection (
  id TEXT PRIMARY KEY,
  tenant_id INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'submitted',
  reason TEXT,
  phase TEXT NOT NULL DEFAULT 'preparing',
  mapped_port INTEGER,
  public_url TEXT,
  image_ref TEXT,
  idle_stop_at TEXT,
  last_activity_at TEXT,
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS secrets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id INTEGER NOT NULL,
  deployment_id TEXT NOT NULL REFERENCES deployments(id),
  key TEXT NOT NULL,
  ciphertext TEXT NOT NULL,
  alg TEXT NOT NULL,
  sender_pk TEXT NOT NULL,
  nonce TEXT NOT NULL DEFAULT '',
  expires_at TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(deployment_id, key)
);

CREATE TABLE IF NOT EXISTS config (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL DEFAULT '{}',
  revision INTEGER NOT NULL DEFAULT 1,
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS config_history (
  key TEXT NOT NULL,
  revision INTEGER NOT NULL,
  value TEXT NOT NULL,
  stored_at TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (key, revision)
);

DROP TABLE IF EXISTS api_tokens;

DROP VIEW IF EXISTS deployment_status;
CREATE VIEW IF NOT EXISTS deployment_status AS
  SELECT d.id, d.tenant_id, d.repo_url, d.port_override, d.created_at,
         COALESCE(p.status, 'submitted') AS status, p.reason, p.phase,
         p.mapped_port, p.public_url,
         p.image_ref, p.idle_stop_at, p.last_activity_at, p.updated_at
  FROM deployments d LEFT JOIN projection p ON p.id = d.id;
"""

# AD-10: the event stream is the audit; nothing edits or deletes it.
_APPEND_ONLY_TRIGGERS = """
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _fmt_like(text: str) -> str:
    return re.sub(r"[%_\\]", "", text)


class Store:
    """A transactional facade over the shared SQLite file.

    `mode` selects the schema grant side:
      "control"  — the api: owns deployments/commands upstream, reads only the
                   deployment_status VIEW, cannot write projections or secrets.
      "executor" — the runner: owns the projection, consumes commands.
      "admin"    — migration/ops tooling (creates the schema first).
    """

    def __init__(self, path: str, mode: str = "control"):
        self.path = path
        self.mode = mode
        if mode == "admin":
            self._admin_init()
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.commit()

    def _admin_init(self) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.executescript(_SCHEMA)
        conn.executescript(_APPEND_ONLY_TRIGGERS)
        conn.commit()
        conn.close()

    def close(self) -> None:
        self._conn.close()

    # -- low-level ------------------------------------------------------------

    def _one(self, sql: str, params=()):
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    def _all(self, sql: str, params=()):
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def _exec(self, sql: str, params=()):
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    # -- events (AD-10) -------------------------------------------------------

    def append_event(self, tenant_id: int, deployment_id: str, type_: str,
                     actor: str, meta: dict | None = None) -> int:
        cur = self._exec(
            "INSERT INTO events (tenant_id, deployment_id, type, actor, meta, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (tenant_id, deployment_id, type_, actor, json.dumps(meta or {}), _now()),
        )
        return cur.lastrowid

    def read_events(self, *, tenant_id: int | None = None,
                    deployment_id: str | None = None,
                    actor: str | None = None,
                    since_ts: str | None = None,
                    until_ts: str | None = None,
                    limit: int = 200) -> list[dict]:
        clauses, params = [], []
        if tenant_id is not None:
            clauses.append("tenant_id = ?")
            params.append(tenant_id)
        if deployment_id:
            clauses.append("deployment_id = ?")
            params.append(deployment_id)
        if actor:
            clauses.append("actor = ?")
            params.append(actor)
        if since_ts:
            clauses.append("created_at >= ?")
            params.append(since_ts)
        if until_ts:
            clauses.append("created_at <= ?")
            params.append(until_ts)
        where = " AND ".join(clauses) or "1=1"
        rows = self._all(
            f"SELECT * FROM events WHERE {where} ORDER BY id DESC LIMIT ?",
            (*params, limit),
        )
        for r in rows:
            r["meta"] = json.loads(r.get("meta") or "{}")
        return rows

    # -- commands (AD-3 queue) ------------------------------------------------ 

    def enqueue_command(self, type_: str, tenant_id: int, deployment_id: str,
                        actor: str, payload: dict | None = None) -> int:
        cur = self._exec(
            "INSERT INTO commands (tenant_id, deployment_id, type, actor, payload) "
            "VALUES (?, ?, ?, ?, ?)",
            (tenant_id, deployment_id, type_, actor, json.dumps(payload or {})),
        )
        return cur.lastrowid

    def claim_command(self) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM commands WHERE status = 'pending' "
                "ORDER BY id ASC LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            self._conn.execute(
                "UPDATE commands SET status = 'running' WHERE id = ?", (row["id"],)
            )
            self._conn.commit()
        cmd = dict(row)
        cmd["payload"] = json.loads(cmd.get("payload") or "{}")
        return cmd

    def finish_command(self, command_id: int, status: str) -> None:
        self._exec("UPDATE commands SET status = ? WHERE id = ?", (status, command_id))

    def pending_count(self) -> int:
        return self._one(
            "SELECT COUNT(*) AS c FROM commands WHERE status = 'pending'"
        )["c"]

    def has_preemptive_signal(self, deployment_id: str, type_: str) -> bool:
        """True when a preemptive stop/halt signal is queued for a deployment
        (AD-3: stop/halt are honored even mid-build)."""
        if type_ == "halt":
            cfg = self.get_config("policy", {})
            return bool(cfg.get("halted"))
        return self._one(
            "SELECT 1 FROM commands WHERE deployment_id = ? AND type = ? "
            "AND status IN ('pending','running') LIMIT 1",
            (deployment_id, type_),
        ) is not None

    def abort_commands_for(self, deployment_id: str, status: str = "aborted") -> None:
        self._exec(
            "UPDATE commands SET status = ? WHERE deployment_id = ? AND status = 'pending'",
            (status, deployment_id),
        )

    def abort_all_pending(self, status: str = "aborted") -> None:
        self._exec("UPDATE commands SET status = ? WHERE status = 'pending'", (status,))

    # -- identity -------------------------------------------------------------

    def upsert_tenant(self, email: str, role: str = "user") -> dict:
        email = email.lower().strip()
        row = self._one("SELECT * FROM tenants WHERE email = ?", (email,))
        if row:
            return dict(row)
        cur = self._exec(
            "INSERT INTO tenants (email, role) VALUES (?, ?)", (email, role)
        )
        return {"id": cur.lastrowid, "email": email, "role": role}

    def get_tenant_by_email(self, email: str) -> dict | None:
        row = self._one("SELECT * FROM tenants WHERE email = ?", (email.lower().strip(),))
        return dict(row) if row else None

    def get_tenant(self, tenant_id: int) -> dict | None:
        row = self._one("SELECT * FROM tenants WHERE id = ?", (tenant_id,))
        return dict(row) if row else None

    def ensure_tenant(self, tenant_id: int, email: str) -> dict:
        """Create the pinned single-user tenant with an explicit id. There is no
        sign-in to upsert it, and Deployments carry a foreign key to tenants(id)."""
        existing = self.get_tenant(tenant_id)
        if existing:
            return existing
        email = email.lower().strip()
        self._exec(
            "INSERT INTO tenants (id, email, role) VALUES (?, ?, 'user')",
            (tenant_id, email),
        )
        return {"id": tenant_id, "email": email, "role": "user"}

    def list_tenants(self) -> list[dict]:
        return self._all("SELECT * FROM tenants ORDER BY id ASC")

    def set_role(self, tenant_id: int, role: str) -> None:
        self._exec("UPDATE tenants SET role = ? WHERE id = ?", (role, tenant_id))

    # -- deployment metadata (api-owned) --------------------------------------

    def create_deployment(self, slug: str, tenant_id: int, repo_url: str,
                          port_override: int | None) -> None:
        self._exec(
            "INSERT INTO deployments (id, tenant_id, repo_url, port_override) "
            "VALUES (?, ?, ?, ?)",
            (slug, tenant_id, repo_url, port_override),
        )

    def get_deployment(self, slug: str) -> dict | None:
        row = self._one("SELECT * FROM deployments WHERE id = ?", (slug,))
        return dict(row) if row else None

    def update_deployment_meta(self, slug: str, *, port_override: int | None = None) -> None:
        self._exec("UPDATE deployments SET port_override = ? WHERE id = ?",
                   (port_override, slug))

    # -- projection (runner-owned) --------------------------------------------

    def _projection_select(self):
        if self.mode == "executor":
            return (
                "SELECT d.id, d.tenant_id, d.repo_url, d.port_override, d.created_at, "
                "COALESCE(p.status, 'submitted') AS status, p.reason, p.phase, "
                "p.mapped_port, p.public_url, "
                "p.image_ref, p.idle_stop_at, p.last_activity_at, p.updated_at "
                "FROM deployments d LEFT JOIN projection p ON p.id = d.id "
            )
        return "SELECT * FROM deployment_status "

    def get_projection(self, slug: str) -> dict | None:
        # Executor selects from the aliased join (WHERE d.id = ?); control mode
        # reads the VIEW (WHERE id = ?) — the alias d exists only inside it.
        alias = "d." if self.mode == "executor" else ""
        row = self._one(self._projection_select() + f"WHERE {alias}id = ?", (slug,))
        return dict(row) if row else None

    def list_projections(self, tenant_id: int | None = None,
                         *, limit: int = 50, offset: int = 0) -> list[dict]:
        alias = "d." if self.mode == "executor" else ""
        where, params = "", []
        if tenant_id is not None:
            where, params = f"WHERE {alias}tenant_id = ? ", [tenant_id]
        rows = self._all(
            self._projection_select() + f"{where}ORDER BY COALESCE(created_at,'') DESC "
            "LIMIT ? OFFSET ?",
            (*params, limit, offset),
        )
        return rows

    def count_projections(self, tenant_id: int | None = None) -> int:
        if tenant_id is None:
            return self._one("SELECT COUNT(*) AS c FROM deployment_status")["c"]
        return self._one(
            "SELECT COUNT(*) AS c FROM deployment_status WHERE tenant_id = ?",
            (tenant_id,),
        )["c"]

    def set_projection(self, slug: str, *, status: str, reason: str | None = None,
                       phase: str | None = None, mapped_port: int | None = None,
                       public_url: str | None = None, image_ref: str | None = None,
                       idle_stop_at: str | None = None) -> None:
        if self.mode != "executor":
            raise PermissionError("projection writes are runner-owned (AD-3)")
        now = _now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO projection (id, tenant_id, status, reason, phase, mapped_port, "
                "public_url, image_ref, idle_stop_at, updated_at) "
                "VALUES (?, (SELECT tenant_id FROM deployments WHERE id = ?), "
                "?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET status=excluded.status, "
                "reason=excluded.reason, phase=excluded.phase, "
                "mapped_port=excluded.mapped_port, public_url=excluded.public_url, "
                "image_ref=excluded.image_ref, idle_stop_at=excluded.idle_stop_at, "
                "updated_at=excluded.updated_at",
                (slug, slug, status, reason, phase, mapped_port, public_url, image_ref,
                 idle_stop_at, now),
            )
            self._conn.commit()

    def touch_last_activity(self, slug: str) -> None:
        """Idle signal source is Caddy in prod (AD-12); api touch is dev/read."""
        self._exec("UPDATE projection SET last_activity_at = ? WHERE id = ?",
                   (_now(), slug))

    def set_idle_marker(self, slug: str, idle_stop_at: str | None) -> None:
        self._exec("UPDATE projection SET idle_stop_at = ? WHERE id = ?",
                   (idle_stop_at, slug))

    def list_running_projections(self) -> list[dict]:
        alias = "p." if self.mode == "executor" else ""
        return self._all(
            self._projection_select() + f"WHERE {alias}status IN ('running', 'starting')"
        )

    def list_projections_by_status_reason(self, status: str, reason: str) -> list[dict]:
        """Projections in `status` whose runner-recorded `reason` matches. Used
        by resume to find exactly the instances a halt stopped, without
        re-reading the event stream."""
        alias = "p." if self.mode == "executor" else ""
        return self._all(
            self._projection_select() +
            f"WHERE {alias}status = ? AND {alias}reason = ? ORDER BY {alias}id ASC",
            (status, reason),
        )

    # -- secrets (AD-9) -------------------------------------------------------

    def upsert_secret(self, tenant_id: int, slug: str, key: str,
                      ciphertext: str, alg: str, sender_pk: str, nonce: str) -> None:
        self._exec(
            "INSERT INTO secrets (tenant_id, deployment_id, key, ciphertext, alg, sender_pk, nonce) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(deployment_id, key) DO UPDATE SET "
            "ciphertext=excluded.ciphertext, alg=excluded.alg, sender_pk=excluded.sender_pk, "
            "nonce=excluded.nonce, updated_at=datetime('now')",
            (tenant_id, slug, key, ciphertext, alg, sender_pk, nonce),
        )

    def list_secrets(self, tenant_id: int, slug: str) -> list[dict]:
        rows = self._all(
            "SELECT id, key, alg, created_at, updated_at FROM secrets "
            "WHERE tenant_id = ? AND deployment_id = ? ORDER BY key",
            (tenant_id, slug),
        )
        return rows

    def list_secret_payloads(self, tenant_id: int, slug: str) -> list[dict]:
        """Full envelope rows — the runner is the only reader (AD-9)."""
        return self._all(
            "SELECT * FROM secrets WHERE tenant_id = ? AND deployment_id = ?",
            (tenant_id, slug),
        )

    def get_secret(self, tenant_id: int, slug: str, key: str) -> dict | None:
        row = self._one(
            "SELECT * FROM secrets WHERE tenant_id = ? AND deployment_id = ? AND key = ?",
            (tenant_id, slug, key),
        )
        return dict(row) if row else None

    def delete_secret(self, tenant_id: int, slug: str, key: str) -> bool:
        cur = self._exec(
            "DELETE FROM secrets WHERE tenant_id = ? AND deployment_id = ? AND key = ?",
            (tenant_id, slug, key),
        )
        return cur.rowcount > 0

    # -- teardown (delete feature, quota remedy) ------------------------------

    def list_orphan_candidates(self, grace_seconds: int = 60) -> list[dict]:
        """Deployments that can never be claimed: no projection row and no
        pending/running command, created outside the grace window.

        Their `deployment_status` view position coalesces to `submitted`
        forever (a phantom quota slot). The runner sweeps them: the api could
        not remove the phantom intent, and AD-3 gives the runner the lifecycle
        write. The grace window absorbs the api's create → enqueue gap so a
        real in-flight deploy is never mis-swept.
        """
        rows = self._all(
            "SELECT d.id, d.tenant_id, d.repo_url, d.created_at "
            "FROM deployments d "
            "WHERE NOT EXISTS (SELECT 1 FROM projection p WHERE p.id = d.id) "
            "AND NOT EXISTS (SELECT 1 FROM commands c "
            "                 WHERE c.deployment_id = d.id "
            "                 AND c.status IN ('pending','running')) "
            "AND d.created_at < datetime('now', ?)",
            (f"-{max(0, int(grace_seconds))} seconds",),
        )
        return rows

    def delete_deployment(self, slug: str) -> bool:
        """Destroy a Deployment and its lifecycle state: projection, secrets,
        queued commands, and the `deployments` row itself. Events stay —
        append-only audit (AD-10). Runs on either side; the runner executes it
        on a DELETE command and on the orphan sweep."""
        with self._lock:
            self._conn.execute(
                "UPDATE commands SET status = 'aborted' "
                "WHERE deployment_id = ? AND status IN ('pending','running')",
                (slug,),
            )
            self._conn.execute(
                "DELETE FROM projection WHERE id = ?", (slug,)
            )
            self._conn.execute(
                "DELETE FROM secrets WHERE deployment_id = ?", (slug,)
            )
            cur = self._conn.execute(
                "DELETE FROM deployments WHERE id = ?", (slug,)
            )
            self._conn.commit()
        return cur.rowcount > 0

    # -- config (AD-8) --------------------------------------------------------

    def get_config(self, key: str, default=None):
        row = self._one("SELECT value, revision FROM config WHERE key = ?", (key,))
        if row is None:
            return default
        value = json.loads(row["value"])
        if isinstance(value, dict) and "revision" in value and "data" in value:
            return value["data"]
        return value

    def _config_row(self, key: str) -> dict | None:
        r = self._one("SELECT * FROM config WHERE key = ?", (key,))
        if not r:
            return None
        d = dict(r)
        try:
            d["value"] = json.loads(d["value"])
        except json.JSONDecodeError:
            pass
        return d

    def set_config(self, key: str, value) -> int:
        """Write config, bumping revision. Archives the previous revision so
        last-known-good restore is one read away (Story 5.2). Returns the
        previous revision (0 if first write)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT revision FROM config WHERE key = ?", (key,)
            ).fetchone()
            prev = row["revision"] if row else 0
            if row:
                self._conn.execute(
                    "INSERT OR IGNORE INTO config_history (key, revision, value) "
                    "SELECT key, revision, value FROM config WHERE key = ?",
                    (key,),
                )
            self._conn.execute(
                "INSERT INTO config (key, value, revision) VALUES (?, ?, 1) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
                "revision=config.revision+1, updated_at=datetime('now')",
                (key, json.dumps(value)),
            )
            self._conn.commit()
        return prev

    def get_config_snapshot(self, key: str) -> dict | None:
        """Full config row (value + revision) — for the operator rollback path."""
        d = self._config_row(key)
        if not d:
            return None
        d["revision"] = d["revision"]
        return d

    def restore_config(self, key: str, revision: int) -> bool:
        """Roll config back to a recorded revision (last-known-good)."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT value, revision FROM config WHERE key = ?", (key,)
            ).fetchone()
            if cur and cur["revision"] == revision:
                return True  # already at the requested revision
            rows = self._conn.execute(
                "SELECT value FROM config_history WHERE key = ? AND revision = ?",
                (key, revision),
            ).fetchall()
            if not rows:
                return False
            self._conn.execute(
                "UPDATE config SET value = ?, updated_at=datetime('now') WHERE key = ?",
                (rows[0]["value"], key),
            )
            self._conn.commit()
        return True
