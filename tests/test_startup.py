#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prove the startup health check does not wait for the first fetch.

Why this file exists
--------------------
``lifespan()`` used to ``await`` the boot cache prime next to a comment
claiming "a slow upstream cannot block startup health checks". That comment
was wrong. FastAPI does not finish startup — and uvicorn therefore does not
answer ``/api/health`` — until the pre-``yield`` part of the lifespan returns.
The prime fans out to ~76 upstreams, so whenever those were slow the CI health
check (a 60 s retry budget) and Render's ``healthCheckPath`` timed out. That
is why ``test-backend`` flickered between success and failure on identical
code.

Measured on this machine with a 30 s simulated fetch:

    old code  /api/health -> 200 after 33 s
    fixed     /api/health -> 200 after  1 s

Run:
    python tests/test_startup.py
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Point storage at a throwaway file BEFORE importing main, which reads its
# configuration at import time.
os.environ["OSINT_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="osint-startup-"), "t.db")
os.environ.pop("DATABASE_URL", None)
logging.disable(logging.CRITICAL)

PASS = 0
FAIL = 0

#: Simulated duration of a first fetch whose upstreams all hang.
SLOW_FETCH_SEC = 8
#: The app must be answering well inside this, whatever the fetch does.
STARTUP_BUDGET_SEC = 3


def ok(cond: bool, label: str, extra: str = "") -> None:
    """Record one assertion and print it."""
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  \u2705 {label}")
    else:
        FAIL += 1
        print(f"  \u274c {label}" + (f"  -> {extra}" if extra else ""))


def main() -> int:
    """Boot the app against a hanging upstream and time the health probe."""
    print("\n=== démarrage face à des sources qui pendent ===")
    import main as app_module
    from fastapi.testclient import TestClient

    real_fetch = app_module.fetch_all_comprehensive
    release = {"done": False}

    def hanging_fetch():
        """Stand in for a cycle where every upstream times out."""
        time.sleep(SLOW_FETCH_SEC)
        release["done"] = True
        return real_fetch()

    app_module.fetch_all_comprehensive = hanging_fetch
    try:
        # Entering the context manager runs the lifespan up to its `yield`.
        # If the lifespan awaits the prime, this call blocks for the whole
        # simulated fetch — which is precisely the bug under test.
        started = time.time()
        with TestClient(app_module.app) as client:
            elapsed = time.time() - started
            ok(elapsed < STARTUP_BUDGET_SEC,
               f"startup returns in under {STARTUP_BUDGET_SEC}s while a "
               f"{SLOW_FETCH_SEC}s fetch is still running",
               f"took {elapsed:.1f}s")
            ok(not release["done"], "the fetch really was still in flight",
               "it had already finished, so the timing proves nothing")
            res = client.get("/api/health")
            ok(res.status_code == 200, "/api/health answers 200 during the first fetch",
               str(res.status_code))
            body = res.json()
            ok("status" in body and "version" in body,
               "the health payload is complete before priming lands",
               str(list(body)[:4]))
    finally:
        app_module.fetch_all_comprehensive = real_fetch

    print("\n=== la prime finit par remplir le cache ===")
    # With the real (fast, network-blocked => fallback) fetch, the background
    # prime must still populate the cache shortly after boot.
    deadline = time.time() + 60
    filled = None
    while time.time() < deadline:
        with TestClient(app_module.app) as client:
            n = client.get("/api/health").json().get("cached_incidents", 0)
        if n > 0:
            filled = n
            break
        time.sleep(1)
    ok(filled is not None, "the cache is filled by the background prime",
       f"still empty after 60s")
    ok(app_module.live_cache["is_refreshing"] is False,
       "the refresh slot is released once priming completes",
       str(app_module.live_cache["is_refreshing"]))

    print(f"\n──────── startup: {PASS} passed, {FAIL} failed ────────")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
