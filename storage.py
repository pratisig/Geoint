#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Persistence layer for HUMAN-OSINT.

Two backends are supported behind one interface:

* **SQLite** (default) — zero configuration, ideal for local development and
  for the Android/desktop builds.
* **PostgreSQL** — used automatically as soon as ``DATABASE_URL`` is present,
  which is what Render injects when a database is attached to the service.

Why PostgreSQL matters on Render: the free tier has an **ephemeral filesystem
and cannot attach a persistent disk**, so a SQLite file is wiped on every
spin-down, restart or redeploy. Attaching a Postgres instance is the only way
to keep the event archive across deployments there.

Every public function in this module works identically on both backends; SQL
is written with ``?`` placeholders and translated to ``%s`` for psycopg2.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger("HUMAN-OSINT-STORAGE")

# ---------------------------------------------------------------------------
# Backend selection
# ---------------------------------------------------------------------------
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
#: ``"postgres"`` when a Render/managed database is configured, else ``"sqlite"``.
BACKEND = "postgres" if DATABASE_URL.startswith(("postgres://", "postgresql://")) else "sqlite"
SQLITE_PATH = os.getenv("OSINT_DB_PATH", "osint_database.db")
#: How long archived events are kept before :func:`prune` removes them.
RETENTION_DAYS = int(os.getenv("OSINT_RETENTION_DAYS", "365"))
#: When set, an unreachable PostgreSQL is an error instead of a silent
#: downgrade. The integration test suite sets this so a PostgreSQL run can
#: never quietly turn into a SQLite run and still report a pass.
STRICT = os.getenv("OSINT_DB_STRICT", "").strip().lower() in ("1", "true", "yes")

_PLACEHOLDER_RE = re.compile(r"(?<!%)\?")


def backend_name() -> str:
    """Return the active storage backend name.

    Returns:
        ``"postgres"`` or ``"sqlite"``.
    """
    return BACKEND


def _adapt(sql: str) -> str:
    """Translate ``?`` placeholders into psycopg2's ``%s`` and apply DDL fixes.

    Args:
        sql: SQL written in the SQLite dialect.

    Returns:
        The same statement adapted for the active backend. On SQLite the
        statement is returned unchanged.
    """
    if BACKEND != "postgres":
        return sql
    out = _PLACEHOLDER_RE.sub("%s", sql)
    # SQLite-specific DDL that PostgreSQL spells differently.
    out = out.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
    out = out.replace("REAL NOT NULL", "DOUBLE PRECISION NOT NULL")
    out = out.replace("REAL", "DOUBLE PRECISION")
    out = out.replace("BOOLEAN DEFAULT 0", "BOOLEAN DEFAULT FALSE")
    out = out.replace("INSERT OR IGNORE", "INSERT INTO")
    return out


def connect():
    """Open a connection to the active backend.

    An unreachable PostgreSQL never takes the API down. The whole process is
    downgraded to SQLite once, at the module level, so every later read and
    write stays on the same engine instead of splitting state across two.
    Render's free PostgreSQL expires 30 days after creation, so this path is
    expected in production rather than exceptional.

    Set ``OSINT_DB_STRICT=1`` to re-raise instead, which is what the
    integration tests use to prove a PostgreSQL run really ran on PostgreSQL.

    Raises:
        Exception: The driver's own error, only in strict mode.

    Returns:
        A DB-API connection. Rows are returned as mappings on both backends
        (``sqlite3.Row`` for SQLite, ``RealDictCursor`` for PostgreSQL), so
        callers can always use ``dict(row)``.
    """
    global BACKEND
    if BACKEND == "postgres":
        try:
            import psycopg2
            import psycopg2.extras
            # RealDictCursor is what makes dict(row) work the same way it does
            # with sqlite3.Row; the default cursor returns plain tuples.
            conn = psycopg2.connect(
                DATABASE_URL,
                connect_timeout=10,
                cursor_factory=psycopg2.extras.RealDictCursor,
            )
            conn.autocommit = False
            return conn
        except Exception as exc:  # noqa: BLE001 - the app must still boot
            if STRICT:
                raise
            logger.error(
                "PostgreSQL injoignable (%s). Bascule sur SQLite (%s) : "
                "l'archive de cette instance ne survivra pas a un redemarrage. "
                "Verifiez DATABASE_URL ou l'etat de votre instance Render "
                "(le niveau gratuit expire 30 jours apres sa creation).",
                exc, SQLITE_PATH,
            )
            BACKEND = "sqlite"
    conn = sqlite3.connect(SQLITE_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def query(sql: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
    """Run a SELECT and return every row as a dictionary.

    Args:
        sql: Statement with ``?`` placeholders.
        params: Bound parameters.

    Returns:
        List of row dictionaries. Empty list when nothing matches, and on any
        database error (logged, never raised, so one bad query cannot take the
        API down).
    """
    try:
        conn = connect()
        try:
            cur = conn.cursor()
            cur.execute(_adapt(sql), tuple(params))
            rows = [dict(r) for r in cur.fetchall()]
        finally:
            conn.close()
        return rows
    except Exception as exc:  # noqa: BLE001 - storage must never break the API
        logger.error("query failed on %s: %s", BACKEND, exc)
        return []


def execute(sql: str, params: Sequence[Any] = ()) -> int:
    """Run an INSERT/UPDATE/DELETE and commit.

    Args:
        sql: Statement with ``?`` placeholders.
        params: Bound parameters.

    Returns:
        Number of affected rows, or ``-1`` when the statement failed.
    """
    try:
        conn = connect()
        try:
            cur = conn.cursor()
            cur.execute(_adapt(sql), tuple(params))
            affected = cur.rowcount
            conn.commit()
        finally:
            conn.close()
        return affected
    except Exception as exc:  # noqa: BLE001
        logger.error("execute failed on %s: %s", BACKEND, exc)
        return -1


def execute_returning(sql: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
    """Run a mutating statement with ``RETURNING`` and commit.

    :func:`query` never commits, so it must not be used for INSERT/UPDATE:
    the statement would be rolled back when the connection closes even though
    ``RETURNING`` appeared to succeed.

    Args:
        sql: Mutating statement with ``?`` placeholders and a RETURNING clause.
        params: Bound parameters.

    Returns:
        The returned rows as dictionaries, or an empty list on failure.
    """
    try:
        conn = connect()
        try:
            cur = conn.cursor()
            cur.execute(_adapt(sql), tuple(params))
            rows = [dict(r) for r in cur.fetchall()]
            conn.commit()
        finally:
            conn.close()
        return rows
    except Exception as exc:  # noqa: BLE001
        logger.error("execute_returning failed on %s: %s", BACKEND, exc)
        return []


def executemany(sql: str, seq: Iterable[Sequence[Any]]) -> int:
    """Run the same statement for many parameter tuples in one transaction.

    Args:
        sql: Statement with ``?`` placeholders.
        seq: Iterable of parameter tuples.

    Returns:
        Number of affected rows, or ``-1`` on failure.
    """
    rows = list(seq)
    if not rows:
        return 0
    try:
        conn = connect()
        try:
            cur = conn.cursor()
            cur.executemany(_adapt(sql), rows)
            affected = cur.rowcount
            conn.commit()
        finally:
            conn.close()
        return affected if affected is not None and affected >= 0 else len(rows)
    except Exception as exc:  # noqa: BLE001
        logger.error("executemany failed on %s: %s", BACKEND, exc)
        return -1


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
#: Columns of the ``incidents`` archive table, in insertion order.
INCIDENT_COLUMNS = (
    "title", "link", "source", "source_type", "category", "region", "country",
    "latitude", "longitude", "published_at", "published_ts", "summary",
    "severity", "actors", "needs", "risk_level", "first_seen_at", "last_seen_at",
)


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def to_epoch(value: Optional[str]) -> Optional[float]:
    """Convert an ISO-8601 timestamp to epoch seconds.

    Storing a numeric timestamp alongside the text one is what makes date-range
    queries reliable: ISO strings are only comparable lexicographically when
    every producer uses the same format and timezone offset, which 60+ scraped
    sources do not guarantee.

    Args:
        value: ISO-8601 timestamp, possibly naive.

    Returns:
        Seconds since the epoch as a float, or ``None`` when unparseable.
    """
    if not value:
        return None
    try:
        import dateutil.parser
        dt = dateutil.parser.parse(str(value))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:  # noqa: BLE001 - malformed dates are common in feeds
        return None


def init_schema() -> Dict[str, Any]:
    """Create the archive schema if missing and migrate older tables.

    Idempotent: safe on every boot. Adds the columns introduced by the archive
    feature (``published_ts``, ``first_seen_at``, ``last_seen_at``) to
    pre-existing installations instead of failing on a duplicate column.

    Returns:
        Diagnostic mapping with the backend, the SQLite path and the archived
        row count.
    """
    logger.info("Init storage schema (backend=%s)", BACKEND)
    conn = connect()
    try:
        cur = conn.cursor()
        cur.execute(_adapt("""
            CREATE TABLE IF NOT EXISTS incidents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                link TEXT UNIQUE NOT NULL,
                source TEXT NOT NULL,
                source_type TEXT DEFAULT 'PRESSE',
                category TEXT NOT NULL,
                region TEXT DEFAULT 'Global',
                country TEXT DEFAULT 'International',
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                published_at TEXT NOT NULL,
                summary TEXT DEFAULT '',
                severity TEXT DEFAULT 'medium',
                actors TEXT DEFAULT '[]',
                needs TEXT DEFAULT '[]',
                risk_level INTEGER DEFAULT 2,
                language TEXT DEFAULT 'fr',
                verified BOOLEAN DEFAULT 0,
                created_at TEXT NOT NULL
            )
        """))
        cur.execute(_adapt("""
            CREATE TABLE IF NOT EXISTS geozones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                geometry_type TEXT NOT NULL,
                geojson_data TEXT NOT NULL,
                area_sqkm REAL NOT NULL,
                created_at TEXT NOT NULL
            )
        """))
        conn.commit()

        # Lightweight migration: add the archive columns when absent.
        if BACKEND == "sqlite":
            cur.execute("PRAGMA table_info(incidents)")
            # sqlite3.Row supports both positional and named access.
            existing = {row["name"] for row in cur.fetchall()}
        else:
            cur.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
                ("incidents",),
            )
            # RealDictRow only supports named access, never row[0].
            existing = {row["column_name"] for row in cur.fetchall()}
        migrations = {
            "published_ts": "ALTER TABLE incidents ADD COLUMN published_ts REAL",
            "first_seen_at": "ALTER TABLE incidents ADD COLUMN first_seen_at TEXT",
            "last_seen_at": "ALTER TABLE incidents ADD COLUMN last_seen_at TEXT",
        }
        for column, ddl in migrations.items():
            if column not in existing:
                cur.execute(_adapt(ddl))
                logger.info("Migrated incidents: added column %s", column)
        conn.commit()

        for index_sql in (
            "CREATE INDEX IF NOT EXISTS idx_incidents_published_ts ON incidents(published_ts)",
            "CREATE INDEX IF NOT EXISTS idx_incidents_category ON incidents(category)",
            "CREATE INDEX IF NOT EXISTS idx_incidents_region ON incidents(region)",
            "CREATE INDEX IF NOT EXISTS idx_incidents_country ON incidents(country)",
        ):
            cur.execute(_adapt(index_sql))
        conn.commit()

        cur.execute("SELECT COUNT(*) AS n FROM incidents")
        total = cur.fetchone()["n"]
    finally:
        conn.close()
    return {"backend": BACKEND, "path": None if BACKEND == "postgres" else SQLITE_PATH, "archived_incidents": int(total)}


# ---------------------------------------------------------------------------
# Archive writes
# ---------------------------------------------------------------------------
def archive_incidents(incidents: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    """Persist a batch of incidents, preserving when each was first seen.

    Uses ``ON CONFLICT(link) DO UPDATE`` rather than ``INSERT OR REPLACE``: the
    latter deletes and re-creates the row, which would reset ``first_seen_at``
    on every cycle and destroy the notion of "when did this event appear".

    Args:
        incidents: Normalised incident dictionaries as produced by the
            collectors in ``main.py``.

    Returns:
        Mapping with ``written`` (rows affected) and ``skipped`` counts.
    """
    if not incidents:
        return {"written": 0, "skipped": 0}
    now = _now_iso()
    rows = []
    for inc in incidents:
        link = (inc.get("link") or "").strip()
        title = (inc.get("title") or "").strip()
        if not link or not title:
            continue
        published_at = inc.get("published_at") or now
        rows.append((
            title, link, inc.get("source", ""), inc.get("source_type", "PRESSE"),
            (inc.get("category") or "conflit").lower(), inc.get("region", "Global"),
            inc.get("country", "International"),
            float(inc.get("latitude") or 0.0), float(inc.get("longitude") or 0.0),
            published_at, to_epoch(published_at), inc.get("summary", "") or "",
            inc.get("severity", "medium") or "medium",
            json.dumps(inc.get("actors") or [], ensure_ascii=False),
            json.dumps(inc.get("needs") or [], ensure_ascii=False),
            int(inc.get("risk_level") or 2), now, now, now,
        ))
    if not rows:
        return {"written": 0, "skipped": len(incidents)}
    sql = """
        INSERT INTO incidents (
            title, link, source, source_type, category, region, country,
            latitude, longitude, published_at, published_ts, summary, severity,
            actors, needs, risk_level, created_at, first_seen_at, last_seen_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(link) DO UPDATE SET
            title=excluded.title,
            source=excluded.source,
            source_type=excluded.source_type,
            category=excluded.category,
            region=excluded.region,
            country=excluded.country,
            latitude=excluded.latitude,
            longitude=excluded.longitude,
            published_at=excluded.published_at,
            published_ts=excluded.published_ts,
            summary=excluded.summary,
            severity=excluded.severity,
            actors=excluded.actors,
            needs=excluded.needs,
            risk_level=excluded.risk_level,
            last_seen_at=excluded.last_seen_at
    """
    written = executemany(sql, rows)
    return {"written": max(written, 0), "skipped": len(incidents) - len(rows)}


def _decode_json_field(raw: Any, default: Any) -> Any:
    """Decode a JSON-encoded TEXT column, tolerating already-decoded values."""
    if raw is None:
        return default
    if isinstance(raw, (list, dict)):
        return raw
    try:
        return json.loads(raw)
    except Exception:  # noqa: BLE001
        return default


def _row_to_incident(row: Dict[str, Any]) -> Dict[str, Any]:
    """Normalise a raw archive row into the incident shape the API returns."""
    out = dict(row)
    out["actors"] = _decode_json_field(out.get("actors"), [])
    out["needs"] = _decode_json_field(out.get("needs"), [])
    return out


# ---------------------------------------------------------------------------
# Archive reads
# ---------------------------------------------------------------------------
def _build_filters(
    category: Optional[str],
    region: Optional[str],
    country: Optional[str],
    search: Optional[str],
    date_from: Optional[str],
    date_to: Optional[str],
    source: Optional[str],
    min_risk: Optional[int],
) -> Tuple[str, List[Any]]:
    """Assemble the WHERE clause shared by every archive query.

    Args:
        category: Exact category match; ``"all"`` and ``None`` disable it.
        region: Exact region match.
        country: Case-insensitive substring match.
        search: Case-insensitive match over title, summary, country and source.
        date_from: Inclusive lower bound, ISO date or timestamp.
        date_to: Inclusive upper bound, ISO date or timestamp.
        source: Case-insensitive substring match on the source name.
        min_risk: Keep only rows with ``risk_level >= min_risk``.

    Returns:
        Tuple ``(sql_fragment, params)`` where the fragment starts with
        ``WHERE`` or is empty.
    """
    clauses: List[str] = []
    params: List[Any] = []
    if category and category.lower() != "all":
        clauses.append("LOWER(category) = ?")
        params.append(category.lower())
    if region and region.lower() != "all":
        clauses.append("region = ?")
        params.append(region)
    if country and country.lower() != "all":
        clauses.append("LOWER(country) LIKE ?")
        params.append(f"%{country.lower()}%")
    if source:
        clauses.append("LOWER(source) LIKE ?")
        params.append(f"%{source.lower()}%")
    if min_risk is not None:
        clauses.append("risk_level >= ?")
        params.append(int(min_risk))
    if search:
        like = f"%{search.lower()}%"
        clauses.append("(LOWER(title) LIKE ? OR LOWER(summary) LIKE ? OR LOWER(country) LIKE ? OR LOWER(source) LIKE ?)")
        params.extend([like, like, like, like])
    if date_from:
        ts = to_epoch(date_from if len(str(date_from)) > 10 else f"{date_from}T00:00:00+00:00")
        if ts is not None:
            clauses.append("published_ts >= ?")
            params.append(ts)
    if date_to:
        raw = str(date_to)
        ts = to_epoch(raw if len(raw) > 10 else f"{raw}T23:59:59+00:00")
        if ts is not None:
            clauses.append("published_ts <= ?")
            params.append(ts)
    if not clauses:
        return "", []
    return " WHERE " + " AND ".join(clauses), params


_SELECT = (
    "SELECT id, title, link, source, source_type, category, region, country, "
    "latitude, longitude, published_at, published_ts, summary, severity, actors, "
    "needs, risk_level, first_seen_at, last_seen_at, created_at FROM incidents"
)


def search_archive(
    limit: int = 200,
    offset: int = 0,
    category: Optional[str] = None,
    region: Optional[str] = None,
    country: Optional[str] = None,
    search: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    source: Optional[str] = None,
    min_risk: Optional[int] = None,
    order: str = "desc",
) -> List[Dict[str, Any]]:
    """Query the persisted event archive.

    Args:
        limit: Maximum rows to return.
        offset: Rows to skip, for pagination.
        category: Category filter.
        region: Region filter.
        country: Country substring filter.
        search: Free-text filter.
        date_from: Inclusive start date (``YYYY-MM-DD`` or full timestamp).
        date_to: Inclusive end date.
        source: Source substring filter.
        min_risk: Minimum risk level (1-5).
        order: ``"desc"`` for newest first, ``"asc"`` for oldest first.

    Returns:
        List of incident dictionaries with ``actors`` and ``needs`` decoded.
    """
    where, params = _build_filters(category, region, country, search, date_from, date_to, source, min_risk)
    direction = "ASC" if str(order).lower() == "asc" else "DESC"
    sql = f"{_SELECT}{where} ORDER BY published_ts {direction} NULLS LAST, id {direction} LIMIT ? OFFSET ?"
    params = params + [int(limit), int(offset)]
    return [_row_to_incident(r) for r in query(sql, params)]


def count_archive(
    category: Optional[str] = None,
    region: Optional[str] = None,
    country: Optional[str] = None,
    search: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    source: Optional[str] = None,
    min_risk: Optional[int] = None,
) -> int:
    """Count archive rows matching the same filters as :func:`search_archive`.

    Returns:
        The matching row count, or ``0`` when nothing matches.
    """
    where, params = _build_filters(category, region, country, search, date_from, date_to, source, min_risk)
    rows = query(f"SELECT COUNT(*) AS n FROM incidents{where}", params)
    return int(rows[0]["n"]) if rows else 0


def archive_stats() -> Dict[str, Any]:
    """Aggregate the archive for the history dashboard.

    Returns:
        Mapping with total rows, the covered time range, and per-category,
        per-region, per-country and per-day breakdowns.
    """
    total = count_archive()
    span = query("SELECT MIN(published_ts) AS lo, MAX(published_ts) AS hi, MIN(first_seen_at) AS first_seen FROM incidents")
    by_category = query("SELECT category, COUNT(*) AS n FROM incidents GROUP BY category ORDER BY n DESC")
    by_region = query("SELECT region, COUNT(*) AS n FROM incidents GROUP BY region ORDER BY n DESC")
    by_country = query("SELECT country, COUNT(*) AS n FROM incidents GROUP BY country ORDER BY n DESC LIMIT 40")
    by_source = query("SELECT source, COUNT(*) AS n FROM incidents GROUP BY source ORDER BY n DESC LIMIT 40")
    by_day = query(
        "SELECT CAST(published_ts / 86400 AS INTEGER) AS day, COUNT(*) AS n "
        "FROM incidents WHERE published_ts IS NOT NULL GROUP BY day ORDER BY day DESC LIMIT 400"
    )
    timeline = []
    for row in by_day:
        try:
            day = datetime.fromtimestamp(int(row["day"]) * 86400, tz=timezone.utc).date().isoformat()
        except Exception:  # noqa: BLE001
            continue
        timeline.append({"date": day, "count": int(row["n"])})
    return {
        "backend": BACKEND,
        "retention_days": RETENTION_DAYS,
        "total_archived": total,
        "oldest_published": span[0].get("lo") if span else None,
        "newest_published": span[0].get("hi") if span else None,
        "first_seen": span[0].get("first_seen") if span else None,
        "by_category": {r["category"]: int(r["n"]) for r in by_category},
        "by_region": {r["region"]: int(r["n"]) for r in by_region},
        "by_country": {r["country"]: int(r["n"]) for r in by_country},
        "by_source": {r["source"]: int(r["n"]) for r in by_source},
        "timeline": timeline,
    }


def archive_dates() -> List[Dict[str, Any]]:
    """List the days for which the archive holds events.

    Returns:
        List of ``{"date", "count"}`` mappings, newest day first.
    """
    rows = query(
        "SELECT CAST(published_ts / 86400 AS INTEGER) AS day, COUNT(*) AS n "
        "FROM incidents WHERE published_ts IS NOT NULL GROUP BY day ORDER BY day DESC"
    )
    out = []
    for row in rows:
        try:
            day = datetime.fromtimestamp(int(row["day"]) * 86400, tz=timezone.utc).date().isoformat()
        except Exception:  # noqa: BLE001
            continue
        out.append({"date": day, "count": int(row["n"])})
    return out


def prune(days: Optional[int] = None) -> int:
    """Delete archived events older than the retention window.

    Args:
        days: Override for :data:`RETENTION_DAYS`.

    Returns:
        Number of deleted rows, or ``-1`` on failure.
    """
    keep_days = RETENTION_DAYS if days is None else int(days)
    if keep_days <= 0:
        return 0
    cutoff = datetime.now(timezone.utc).timestamp() - keep_days * 86400
    return execute("DELETE FROM incidents WHERE published_ts IS NOT NULL AND published_ts < ?", (cutoff,))


# ---------------------------------------------------------------------------
# Geozones
# ---------------------------------------------------------------------------
def save_geozone(name: str, geometry_type: str, geojson_data: str, area_sqkm: float) -> Optional[Dict[str, Any]]:
    """Persist a drawn geozone and return it with its new identifier.

    Args:
        name: User-chosen zone name.
        geometry_type: GeoJSON geometry type.
        geojson_data: Serialised GeoJSON geometry.
        area_sqkm: Computed surface in square kilometres.

    Returns:
        The stored zone as a dictionary, or ``None`` on failure.
    """
    now = _now_iso()
    rows = execute_returning(
        "INSERT INTO geozones (name, geometry_type, geojson_data, area_sqkm, created_at) "
        "VALUES (?,?,?,?,?) RETURNING id, name, geometry_type, geojson_data, area_sqkm, created_at",
        (name, geometry_type, geojson_data, float(area_sqkm), now),
    )
    return rows[0] if rows else None


def list_geozones() -> List[Dict[str, Any]]:
    """Return every persisted geozone, newest first.

    Returns:
        List of geozone dictionaries.
    """
    return query("SELECT id, name, geometry_type, geojson_data, area_sqkm, created_at FROM geozones ORDER BY id DESC")


def delete_geozone(zone_id: int) -> bool:
    """Delete one geozone.

    Args:
        zone_id: Identifier to remove.

    Returns:
        ``True`` when a row was deleted.
    """
    return execute("DELETE FROM geozones WHERE id = ?", (int(zone_id),)) > 0
