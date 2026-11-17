"""Persistência em SQLite e consultas de estatística."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL,
    type          TEXT NOT NULL,          -- prompt | upload | visit | network
    source        TEXT NOT NULL,          -- extension | network
    provider      TEXT NOT NULL,
    url           TEXT,
    account_email TEXT,
    account_type  TEXT NOT NULL,
    machine_user  TEXT,
    hostname      TEXT,
    browser       TEXT,
    prompt_text   TEXT,
    prompt_hash   TEXT,
    prompt_chars  INTEGER DEFAULT 0,
    file_name     TEXT,
    file_size     INTEGER,
    process_name  TEXT,
    remote_addr   TEXT,
    score         INTEGER DEFAULT 0,
    level         TEXT DEFAULT 'nenhum',
    categories    TEXT DEFAULT '[]',
    findings      TEXT DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_provider ON events(provider);
CREATE INDEX IF NOT EXISTS idx_events_account ON events(account_email);

CREATE TABLE IF NOT EXISTS alerts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id      INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    ts            TEXT NOT NULL,
    provider      TEXT NOT NULL,
    account_email TEXT,
    account_type  TEXT NOT NULL,
    machine_user  TEXT,
    level         TEXT NOT NULL,
    score         INTEGER NOT NULL,
    categories    TEXT NOT NULL,
    summary       TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'novo',  -- novo | em_analise | resolvido | falso_positivo
    notes         TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_alerts_ts ON alerts(ts);
CREATE INDEX IF NOT EXISTS idx_alerts_status ON alerts(status);
"""

ALERT_STATUSES = ("novo", "em_analise", "resolvido", "falso_positivo")

EVENT_COLUMNS = frozenset({
    "ts", "type", "source", "provider", "url", "account_email", "account_type", "machine_user",
    "hostname", "browser", "prompt_text", "prompt_hash", "prompt_chars", "file_name", "file_size",
    "process_name", "remote_addr", "score", "level", "categories", "findings",
})
ALERT_COLUMNS = frozenset({
    "event_id", "ts", "provider", "account_email", "account_type", "machine_user", "level",
    "score", "categories", "summary", "status", "notes",
})


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path | str):
        self.path = str(path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------ #
    def _query(self, sql: str, params: tuple | list = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def _one(self, sql: str, params: tuple | list = ()) -> dict | None:
        rows = self._query(sql, params)
        return rows[0] if rows else None

    # ------------------------------------------------------------------ #
    def insert_event(self, ev: dict) -> int:
        ev = {k: v for k, v in ev.items() if k in EVENT_COLUMNS}
        ev.setdefault("ts", utcnow())
        for k in ("categories", "findings"):
            if not isinstance(ev.get(k, "[]"), str):
                ev[k] = json.dumps(ev[k], ensure_ascii=False)
        cols = ", ".join(ev)
        marks = ", ".join("?" for _ in ev)
        with self._lock:
            cur = self._conn.execute(f"INSERT INTO events ({cols}) VALUES ({marks})", list(ev.values()))
            self._conn.commit()
            return int(cur.lastrowid)

    def insert_alert(self, alert: dict) -> int:
        alert = {k: v for k, v in alert.items() if k in ALERT_COLUMNS}
        alert.setdefault("ts", utcnow())
        if not isinstance(alert.get("categories"), str):
            alert["categories"] = json.dumps(alert.get("categories", []), ensure_ascii=False)
        cols = ", ".join(alert)
        marks = ", ".join("?" for _ in alert)
        with self._lock:
            cur = self._conn.execute(f"INSERT INTO alerts ({cols}) VALUES ({marks})", list(alert.values()))
            self._conn.commit()
            return int(cur.lastrowid)

    def recent_network_event(self, provider: str, process_name: str, since_iso: str) -> bool:
        row = self._one("SELECT 1 FROM events WHERE type='network' AND provider=? AND process_name=? AND ts>=? LIMIT 1",
                        (provider, process_name, since_iso))
        return row is not None

    def purge(self, retention_days: int) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat(timespec="seconds")
        with self._lock:
            cur = self._conn.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
            self._conn.commit()
            return cur.rowcount

    # ------------------------------------------------------------------ #
    @staticmethod
    def _decode(row: dict) -> dict:
        for k in ("categories", "findings"):
            if k in row and isinstance(row[k], str):
                try:
                    row[k] = json.loads(row[k])
                except json.JSONDecodeError:
                    row[k] = []
        return row

    def list_events(self, limit: int = 100, offset: int = 0, provider: str | None = None,
                    account_type: str | None = None, min_score: int = 0, type_: str | None = None,
                    search: str | None = None, days: int | None = None) -> list[dict]:
        where, params = ["1=1"], []
        if provider:
            where.append("provider = ?"); params.append(provider)
        if account_type:
            where.append("account_type = ?"); params.append(account_type)
        if min_score:
            where.append("score >= ?"); params.append(min_score)
        if type_:
            where.append("type = ?"); params.append(type_)
        if search:
            where.append("(prompt_text LIKE ? OR account_email LIKE ? OR file_name LIKE ?)")
            params += [f"%{search}%"] * 3
        if days:
            where.append("ts >= ?"); params.append(_since(days))
        sql = f"SELECT * FROM events WHERE {' AND '.join(where)} ORDER BY ts DESC, id DESC LIMIT ? OFFSET ?"
        return [self._decode(r) for r in self._query(sql, params + [limit, offset])]

    def get_event(self, event_id: int) -> dict | None:
        row = self._one("SELECT * FROM events WHERE id = ?", (event_id,))
        return self._decode(row) if row else None

    def list_alerts(self, status: str | None = None, limit: int = 200, days: int | None = None) -> list[dict]:
        where, params = ["1=1"], []
        if status:
            where.append("a.status = ?"); params.append(status)
        if days:
            where.append("a.ts >= ?"); params.append(_since(days))
        sql = (f"SELECT a.*, e.prompt_text, e.file_name, e.url, e.findings FROM alerts a "
               f"JOIN events e ON e.id = a.event_id WHERE {' AND '.join(where)} ORDER BY a.ts DESC, a.id DESC LIMIT ?")
        return [self._decode(r) for r in self._query(sql, params + [limit])]

    def update_alert(self, alert_id: int, status: str | None = None, notes: str | None = None) -> bool:
        sets, params = [], []
        if status is not None:
            if status not in ALERT_STATUSES:
                raise ValueError(f"status inválido: {status}")
            sets.append("status = ?"); params.append(status)
        if notes is not None:
            sets.append("notes = ?"); params.append(notes[:2000])
        if not sets:
            return False
        with self._lock:
            cur = self._conn.execute(f"UPDATE alerts SET {', '.join(sets)} WHERE id = ?", params + [alert_id])
            self._conn.commit()
            return cur.rowcount > 0

    # ------------------------------------------------------------------ #
    def stats(self, days: int = 30, tz_offset_min: int = 0) -> dict:
        since = _since(days)
        shift = f"{-tz_offset_min:+d} minutes"   # offset do navegador (Date.getTimezoneOffset)

        totals = self._one(
            """SELECT
                 COUNT(*) AS events,
                 SUM(type='prompt') AS prompts,
                 SUM(type='upload') AS uploads,
                 SUM(type='network') AS network,
                 SUM(score > 0) AS risky_events,
                 COUNT(DISTINCT CASE WHEN account_email IS NOT NULL THEN account_email END) AS accounts,
                 COUNT(DISTINCT CASE WHEN account_type='pessoal' THEN account_email END) AS personal_accounts,
                 SUM(account_type='pessoal' AND type IN ('prompt','upload')) AS personal_interactions,
                 SUM(type IN ('prompt','upload')) AS interactions,
                 COUNT(DISTINCT provider) AS providers,
                 COUNT(DISTINCT machine_user) AS machine_users
               FROM events WHERE ts >= ?""", (since,)) or {}
        totals = {k: (v or 0) for k, v in totals.items()}
        totals["personal_share"] = (round(100 * totals["personal_interactions"] / totals["interactions"], 1)
                                    if totals["interactions"] else 0.0)
        alerts = self._one(
            """SELECT COUNT(*) AS total, SUM(status='novo') AS open,
                      SUM(level='critico') AS critical, SUM(level='alto') AS high
               FROM alerts WHERE ts >= ?""", (since,)) or {}
        totals.update({f"alerts_{k}": (v or 0) for k, v in alerts.items()})

        by_provider = self._query(
            """SELECT provider,
                      SUM(type='prompt') AS prompts, SUM(type='upload') AS uploads,
                      SUM(type='network') AS network,
                      SUM(account_type='pessoal' AND type IN ('prompt','upload')) AS personal,
                      SUM(account_type='corporativa' AND type IN ('prompt','upload')) AS corporate,
                      SUM(score >= 50) AS high_risk, MAX(score) AS max_score,
                      COUNT(DISTINCT account_email) AS accounts
               FROM events WHERE ts >= ? GROUP BY provider ORDER BY prompts DESC, network DESC""", (since,))

        by_account = self._query(
            """SELECT account_email, account_type,
                      GROUP_CONCAT(DISTINCT provider) AS providers,
                      GROUP_CONCAT(DISTINCT machine_user) AS machine_users,
                      SUM(type='prompt') AS prompts, SUM(type='upload') AS uploads,
                      SUM(score >= 50) AS high_risk, MAX(score) AS max_score,
                      ROUND(AVG(CASE WHEN type='prompt' THEN score END), 1) AS avg_score,
                      MAX(ts) AS last_seen
               FROM events WHERE ts >= ? AND type IN ('prompt','upload')
               GROUP BY account_email, account_type ORDER BY high_risk DESC, prompts DESC LIMIT 100""", (since,))

        by_account_type = self._query(
            """SELECT account_type, COUNT(*) AS interactions FROM events
               WHERE ts >= ? AND type IN ('prompt','upload') GROUP BY account_type""", (since,))

        timeline = self._query(
            f"""SELECT date(ts, '{shift}') AS day,
                       SUM(type='prompt') AS prompts, SUM(type='upload') AS uploads,
                       SUM(score >= 50) AS high_risk,
                       SUM(account_type='pessoal' AND type IN ('prompt','upload')) AS personal,
                       SUM(account_type='corporativa' AND type IN ('prompt','upload')) AS corporate,
                       SUM(account_type='externa' AND type IN ('prompt','upload')) AS external,
                       SUM(account_type='desconhecida' AND type IN ('prompt','upload')) AS unknown
                FROM events WHERE ts >= ? GROUP BY day ORDER BY day""", (since,))
        timeline = _fill_days(timeline, days, tz_offset_min)

        hours = self._query(
            f"""SELECT CAST(strftime('%H', ts, '{shift}') AS INTEGER) AS hour, COUNT(*) AS prompts
                FROM events WHERE ts >= ? AND type='prompt' GROUP BY hour""", (since,))
        hour_map = {r["hour"]: r["prompts"] for r in hours}
        by_hour = [{"hour": h, "prompts": hour_map.get(h, 0)} for h in range(24)]

        cat_counts: dict[str, int] = {}
        level_counts = {"baixo": 0, "medio": 0, "alto": 0, "critico": 0}
        for r in self._query("SELECT categories, level FROM events WHERE ts >= ? AND score > 0", (since,)):
            for c in json.loads(r["categories"] or "[]"):
                cat_counts[c] = cat_counts.get(c, 0) + 1
            if r["level"] in level_counts:
                level_counts[r["level"]] += 1
        by_category = sorted(({"category": k, "count": v} for k, v in cat_counts.items()),
                             key=lambda x: -x["count"])

        by_machine_user = self._query(
            """SELECT machine_user, hostname, COUNT(*) AS interactions,
                      COUNT(DISTINCT account_email) AS accounts,
                      SUM(account_type='pessoal') AS personal, SUM(score >= 50) AS high_risk
               FROM events WHERE ts >= ? AND type IN ('prompt','upload')
               GROUP BY machine_user, hostname ORDER BY high_risk DESC, interactions DESC LIMIT 50""", (since,))

        network_processes = self._query(
            """SELECT provider, process_name, COUNT(*) AS sightings, MAX(ts) AS last_seen
               FROM events WHERE ts >= ? AND type='network'
               GROUP BY provider, process_name ORDER BY sightings DESC LIMIT 50""", (since,))

        top_risky = [self._decode(r) for r in self._query(
            """SELECT id, ts, provider, account_email, account_type, machine_user, score, level,
                      categories, prompt_text, file_name, type
               FROM events WHERE ts >= ? AND score > 0 ORDER BY score DESC, id DESC LIMIT 10""", (since,))]

        return {
            "days": days,
            "totals": totals,
            "by_provider": by_provider,
            "by_account": by_account,
            "by_account_type": by_account_type,
            "by_machine_user": by_machine_user,
            "timeline": timeline,
            "by_hour": by_hour,
            "by_category": by_category,
            "by_level": level_counts,
            "network_processes": network_processes,
            "top_risky": top_risky,
        }


def _since(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")


def _fill_days(rows: list[dict], days: int, tz_offset_min: int) -> list[dict]:
    by_day = {r["day"]: r for r in rows}
    today = (datetime.now(timezone.utc) - timedelta(minutes=tz_offset_min)).date()
    out = []
    for i in range(days - 1, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        r = by_day.get(d, {})
        out.append({"day": d, **{k: r.get(k) or 0 for k in
                                 ("prompts", "uploads", "high_risk", "personal", "corporate", "external", "unknown")}})
    return out
