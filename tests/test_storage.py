#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Storage-layer tests, run identically against SQLite and PostgreSQL.

``storage.py`` reads its configuration at import time, so each backend is
exercised in its own subprocess. Invoke without arguments to run SQLite, or
pass ``--postgres <DATABASE_URL>`` to exercise the PostgreSQL path — the same
assertions, the same SQL, a different engine.

Usage:
    python tests/test_storage.py
    python tests/test_storage.py --postgres postgresql://user@host/db
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PASS = 0
FAIL = 0


def ok(cond: bool, label: str, extra: str = "") -> None:
    """Record and print one assertion result."""
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  \u2705 " + label)
    else:
        FAIL += 1
        print("  \u274C " + label + (f"  -> {extra}" if extra else ""))


SAMPLE = [
    {"title": "Séisme M6.8 Mer Rouge - alerte tsunami", "link": "https://example.test/eq-1",
     "source": "USGS", "source_type": "SATELLITE", "category": "catastrophe",
     "region": "Moyen-Orient", "country": "Mer Rouge", "latitude": 14.5, "longitude": 42.8,
     "published_at": "2026-09-10T08:00:00+00:00", "summary": "Secousse majeure",
     "severity": "high", "actors": ["USGS", "GDACS"], "needs": ["Secours"], "risk_level": 4},
    {"title": "Offensive JNIM Sahel - embuscade Gao", "link": "https://example.test/sa-2",
     "source": "GDELT Media", "source_type": "OSINT", "category": "conflit",
     "region": "Afrique", "country": "Mali", "latitude": 16.27, "longitude": -0.04,
     "published_at": "2026-09-14T10:30:00+00:00", "summary": "Embuscade IED",
     "severity": "critical", "actors": ["JNIM", "FAMa"], "needs": ["Sécurité"], "risk_level": 5},
    {"title": "Coupure câble sous-marin Atlantique", "link": "https://example.test/cab-3",
     "source": "Reuters", "source_type": "PRESSE", "category": "cyber",
     "region": "Afrique", "country": "Sénégal", "latitude": 14.69, "longitude": -17.44,
     "published_at": "2026-08-02T06:00:00+00:00", "summary": "Latence en hausse",
     "severity": "medium", "actors": ["Opérateur"], "needs": ["IT"], "risk_level": 3},
]


def run() -> None:
    """Execute the full assertion suite against the configured backend."""
    import storage

    print(f"\n──────── backend: {storage.backend_name()} ────────")

    print("\n=== schema ===")
    info = storage.init_schema()
    ok(info["backend"] == storage.backend_name(), "init_schema reports the active backend", str(info))
    # Idempotent: a second call must not fail nor lose data.
    info2 = storage.init_schema()
    ok(info2["archived_incidents"] == info["archived_incidents"], "init_schema is idempotent")

    print("\n=== archive writes ===")
    first = storage.archive_incidents(SAMPLE)
    ok(first["written"] == len(SAMPLE), f"all {len(SAMPLE)} incidents archived", str(first))
    ok(storage.count_archive() == len(SAMPLE), "row count matches", str(storage.count_archive()))
    time.sleep(0.05)
    again = storage.archive_incidents(SAMPLE)
    ok(again["written"] >= 0, "re-archiving the same batch does not error", str(again))
    ok(storage.count_archive() == len(SAMPLE), "link UNIQUE deduplicates on re-archive",
       str(storage.count_archive()))

    print("\n=== filtering ===")
    ok([r["title"] for r in storage.search_archive(category="conflit")] ==
       ["Offensive JNIM Sahel - embuscade Gao"], "category filter")
    ok([r["title"] for r in storage.search_archive(region="Afrique")].__len__() == 2, "region filter")
    ok([r["title"] for r in storage.search_archive(country="mali")] ==
       ["Offensive JNIM Sahel - embuscade Gao"], "country substring filter (case-insensitive)")
    ok([r["title"] for r in storage.search_archive(source="usgs")] ==
       ["Séisme M6.8 Mer Rouge - alerte tsunami"], "source substring filter")
    ok([r["title"] for r in storage.search_archive(search="IED")] ==
       ["Offensive JNIM Sahel - embuscade Gao"], "free-text search hits the summary")
    ok([r["title"] for r in storage.search_archive(min_risk=5)] ==
       ["Offensive JNIM Sahel - embuscade Gao"], "minimum risk filter")
    ok(storage.count_archive(category="all") == len(SAMPLE), "category='all' disables the filter")

    print("\n=== date ranges (the archive's whole purpose) ===")
    sept = storage.search_archive(date_from="2026-09-01", date_to="2026-09-30")
    ok(len(sept) == 2, "September window returns 2 events", str(len(sept)))
    aug = storage.search_archive(date_from="2026-08-01", date_to="2026-08-31")
    ok([r["title"] for r in aug] == ["Coupure câble sous-marin Atlantique"], "August window returns the older event")
    ok(len(storage.search_archive(date_from="2026-09-12")) == 1, "open-ended lower bound")
    ok(len(storage.search_archive(date_to="2026-09-12")) == 2, "open-ended upper bound")
    ok(storage.count_archive(date_from="2026-09-01", date_to="2026-09-30") == 2, "count_archive honours the same range")

    print("\n=== ordering and pagination ===")
    desc = [r["title"] for r in storage.search_archive(order="desc")]
    ok(desc[0] == "Offensive JNIM Sahel - embuscade Gao", "desc puts the newest first", str(desc[0]))
    asc = [r["title"] for r in storage.search_archive(order="asc")]
    ok(asc[0] == "Coupure câble sous-marin Atlantique", "asc puts the oldest first", str(asc[0]))
    page = storage.search_archive(limit=1, offset=1, order="asc")
    ok(len(page) == 1 and page[0]["title"] == "Séisme M6.8 Mer Rouge - alerte tsunami",
       "limit/offset pagination", str([r["title"] for r in page]))

    print("\n=== timestamps and JSON columns ===")
    row = storage.search_archive(order="asc")[0]
    ok(isinstance(row["published_ts"], float), "published_ts is numeric (reliable range queries)",
       type(row["published_ts"]).__name__)
    ok(row["first_seen_at"] != row["last_seen_at"],
       "first_seen_at preserved when an event is seen again", f"{row['first_seen_at']} vs {row['last_seen_at']}")
    ok(isinstance(row["actors"], list), "actors decoded back to a list", type(row["actors"]).__name__)
    ok(isinstance(row["needs"], list), "needs decoded back to a list", type(row["needs"]).__name__)

    print("\n=== aggregates ===")
    stats = storage.archive_stats()
    ok(stats["total_archived"] == len(SAMPLE), "stats total", str(stats["total_archived"]))
    ok(stats["by_category"].get("conflit") == 1, "stats by_category")
    ok(stats["by_region"].get("Afrique") == 2, "stats by_region")
    ok(len(stats["timeline"]) == 3, "timeline has one bucket per day", str(len(stats["timeline"])))
    dates = storage.archive_dates()
    ok([d["date"] for d in dates][0] == "2026-09-14", "archive_dates is newest-first", str(dates[:1]))

    print("\n=== geozones ===")
    zone = storage.save_geozone("Zone Dakar", "Polygon", '{"type":"Polygon","coordinates":[[[0,0]]]}', 12.5)
    ok(zone is not None and zone["name"] == "Zone Dakar", "save_geozone returns the stored row", str(zone))
    ok(len(storage.list_geozones()) == 1, "the geozone is actually committed (not rolled back)",
       str(len(storage.list_geozones())))
    ok(storage.delete_geozone(zone["id"]) is True, "delete_geozone removes it")
    ok(len(storage.list_geozones()) == 0, "no geozone remains")

    print("\n=== retention ===")
    ok(storage.prune(365) == 0, "prune(365) keeps recent events")
    ok(storage.count_archive() == len(SAMPLE), "row count unchanged after prune(365)")
    deleted = storage.prune(0)
    ok(deleted >= 0, "prune(0) is a no-op rather than a wipe", str(deleted))
    ok(storage.count_archive() == len(SAMPLE), "data survives prune(0)")

    print("\n=== resilience ===")
    ok(storage.query("SELECT * FROM does_not_exist") == [], "a bad query returns [] instead of raising")
    ok(storage.execute("INSERT INTO does_not_exist VALUES (?)", (1,)) == -1, "a bad write returns -1")
    ok(storage.archive_incidents([]) == {"written": 0, "skipped": 0}, "empty batch is a no-op")
    ok(storage.archive_incidents([{"title": "", "link": ""}])["skipped"] == 1, "rows without link/title are skipped")
    ok(storage.to_epoch("not-a-date") is None, "to_epoch tolerates garbage input")
    # Cross-check against the stdlib rather than a hardcoded magic number.
    from datetime import datetime, timezone
    expected = datetime(2026, 9, 14, 10, 30, tzinfo=timezone.utc).timestamp()
    got = storage.to_epoch("2026-09-14T10:30:00+00:00")
    ok(got == expected, "to_epoch matches datetime.timestamp() for an ISO-8601 input",
       f"got {got}, expected {expected}")
    naive = storage.to_epoch("2026-09-14T10:30:00")
    ok(naive == expected, "a naive timestamp is treated as UTC", f"got {naive}")


def test_degraded_boot() -> None:
    """An unreachable PostgreSQL must downgrade to SQLite, not crash the boot.

    Render's free PostgreSQL expires 30 days after creation, so a dead
    ``DATABASE_URL`` is a routine production condition rather than an
    exceptional one. Before this was handled, ``init_schema()`` propagated the
    driver error and the whole API failed to start.

    Runs in a subprocess because the downgrade mutates module-level state,
    which would corrupt the surrounding suite.
    """
    print("\n=== degraded boot (unreachable PostgreSQL) ===")
    script = (
        "import logging, os, sys; logging.disable(logging.CRITICAL);"
        "sys.path.insert(0, os.path.dirname(os.path.abspath(%r)));"
        "import storage;"
        "info = storage.init_schema();"
        "assert storage.backend_name() == 'sqlite', storage.backend_name();"
        "n = storage.archive_incidents([{'title':'t','link':'http://x/d','source':'S',"
        "'source_type':'rss','category':'conflit','region':'afrique','country':'SN',"
        "'severity':'low','risk_level':3,'published_at':'2026-09-16T00:00:00Z',"
        "'latitude':14.7,'longitude':-17.4,'actors':[],'needs':[]}]);"
        "assert n['written'] == 1, n;"
        "assert len(storage.search_archive(limit=5)) == 1;"
        "assert storage.archive_stats()['backend'] == 'sqlite';"
        "print('DEGRADED-OK', info['backend'])"
    ) % str(ROOT)
    env = dict(os.environ,
               DATABASE_URL="postgresql://nobody@127.0.0.1:5999/nope",
               OSINT_DB_PATH=os.path.join(tempfile.mkdtemp(prefix="osint-degraded-"), "d.db"))
    env.pop("OSINT_DB_STRICT", None)
    proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, env=env)
    ok(proc.returncode == 0 and "DEGRADED-OK sqlite" in proc.stdout,
       "the API boots on SQLite when DATABASE_URL is unreachable",
       (proc.stdout + proc.stderr).strip().splitlines()[-1:] or "")
    strict_env = dict(env, OSINT_DB_STRICT="1")
    proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, env=strict_env)
    ok(proc.returncode != 0,
       "OSINT_DB_STRICT=1 re-raises instead, so a PostgreSQL run cannot silently pass as SQLite",
       (proc.stderr or "").strip().splitlines()[-1:])


def main() -> int:
    """Entry point: pick the backend from argv, run the suite, return an exit code."""
    if len(sys.argv) >= 3 and sys.argv[1] == "--postgres":
        os.environ["DATABASE_URL"] = sys.argv[2]
        # A PostgreSQL run must really be PostgreSQL: never let a connection
        # failure quietly downgrade the suite to SQLite and still report a pass.
        os.environ["OSINT_DB_STRICT"] = "1"
    else:
        tmp = tempfile.mkdtemp(prefix="osint-sqlite-")
        os.environ["OSINT_DB_PATH"] = os.path.join(tmp, "test.db")
        os.environ.pop("DATABASE_URL", None)
    run()
    test_degraded_boot()
    print(f"\n──────── {storage_backend()}: {PASS} passed, {FAIL} failed ────────")
    return 1 if FAIL else 0


def storage_backend() -> str:
    """Read the backend name for the summary line without importing storage twice."""
    import storage
    return storage.backend_name()


if __name__ == "__main__":
    raise SystemExit(main())
