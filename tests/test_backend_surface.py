#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Guard the module-level surface of the backend modules.

Why this file exists
--------------------
A v4.2 patch deleted ``GEO_HOTSPOTS``, ``KEYWORDS_CATEGORY``, ``extract_geo``,
``classify``, ``actors_from_text`` and ``needs_from_cat`` from ``main.py``
while twelve call sites still referenced them. ``import main`` kept working —
the names only blow up when the GDELT / Reddit / Telegram ingestion paths
actually run — so every local test passed and only the CI "Syntax check" step
caught it. That is exactly the class of bug a unit test should own.

Two independent guards:

1. :func:`undefined_names` — a stdlib-only, scope-aware scan for names that are
   read but never bound anywhere in scope. It is validated below against the
   known-broken revision, so the detector itself cannot silently rot into
   reporting nothing.
2. A direct execution of every restored helper, so they are proven to run and
   not merely to exist.

Run:
    python tests/test_backend_surface.py
"""

from __future__ import annotations

import ast
import builtins
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

MODULES = ["main.py", "storage.py", "osint_tools.py"]

PASS = 0
FAIL = 0

# Builtins are never "undefined", plus the handful of names Python injects.
_ALWAYS_BOUND = set(dir(builtins)) | {"__name__", "__file__", "__doc__", "__package__",
                                      "__spec__", "__loader__", "__builtins__"}


def ok(cond: bool, label: str, extra: str = "") -> None:
    """Record one assertion and print it."""
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  \u2705 {label}")
    else:
        FAIL += 1
        print(f"  \u274c {label}" + (f"  -> {extra}" if extra else ""))


def _binds(node: ast.AST) -> set[str]:
    """Collect every name this node binds in the enclosing scope."""
    out: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and isinstance(sub.ctx, (ast.Store, ast.Del)):
            out.add(sub.id)
        elif isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(sub.name)
        elif isinstance(sub, ast.arg):
            out.add(sub.arg)
        elif isinstance(sub, ast.Import):
            for a in sub.names:
                out.add((a.asname or a.name).split(".")[0])
        elif isinstance(sub, ast.ImportFrom):
            for a in sub.names:
                out.add(a.asname or a.name)
        elif isinstance(sub, ast.ExceptHandler) and sub.name:
            out.add(sub.name)
        elif isinstance(sub, ast.Global) or isinstance(sub, ast.Nonlocal):
            out.update(sub.names)
    return out


def undefined_names(src: str) -> list[tuple[int, str]]:
    """Find names read inside functions but bound nowhere reachable.

    Scope model: module level, then each function/method, then each nested
    function. Comprehensions get their own scope. This is deliberately simple —
    it is validated against a known-broken revision rather than trusted.

    Args:
        src: Python source text.

    Returns:
        Sorted list of ``(lineno, name)`` pairs that look undefined.
    """
    tree = ast.parse(src)
    module_bound = _binds(tree) - set()
    # Names bound at module level only (walk top-level statements).
    module_bound = set()
    for stmt in tree.body:
        module_bound |= _binds(stmt)
    # Decorators and annotations reference module-level names too.
    problems: list[tuple[int, str]] = []

    def scan(func: ast.AST, enclosing: set[str]) -> None:
        local = _binds(func)
        # Comprehension targets bind inside the comprehension, already covered
        # by _binds because they are Name(Store) nodes.
        visible = enclosing | local | _ALWAYS_BOUND
        for sub in ast.walk(func):
            if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
                if sub.id not in visible:
                    problems.append((sub.lineno, sub.id))
            elif isinstance(sub, ast.Attribute):
                # `mod.attr` — only `mod` needs to resolve; walk handles it.
                continue
        for sub in ast.walk(func):
            if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and sub is not func:
                scan(sub, visible | {sub.name})

    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scan(stmt, module_bound | {stmt.name})
    return sorted(set(problems))


def check_modules() -> None:
    """Assert no backend module reads an unbound name."""
    print("\n=== noms lus mais jamais liés (régression f41d5ee) ===")
    for name in MODULES:
        src = (ROOT / name).read_text(encoding="utf-8")
        bad = undefined_names(src)
        ok(not bad, f"{name}: aucun nom indéfini", "; ".join(f"{l}:{n}" for l, n in bad[:8]))


def check_detector_is_not_blind() -> None:
    """Prove the detector reports something on deliberately broken code.

    A linter that always returns an empty list passes every test while
    guarding nothing. Feeding it the exact pattern that slipped through once
    keeps it honest.
    """
    print("\n=== le détecteur lui-même est testé ===")
    broken = "def f():\n    return extract_geo('Gaza')\n"
    found = [n for _, n in undefined_names(broken)]
    ok(found == ["extract_geo"], "the detector flags a deleted module-level name", str(found))
    clean = "GEO = {'a': 1}\n\n\ndef f():\n    return GEO['a']\n"
    ok(undefined_names(clean) == [], "the detector stays quiet on correct code")


def check_registries() -> None:
    """Assert the data the CI 'Syntax check' step reads is present and non-empty."""
    print("\n=== registres lus par le step CI « Syntax check » ===")
    import main

    ok(len(main.RSS_FEEDS) > 50, "RSS_FEEDS is populated", str(len(main.RSS_FEEDS)))
    ok(len(main.DORKS_DATABASE) > 50, "DORKS_DATABASE is populated", str(len(main.DORKS_DATABASE)))
    ok(len(main.GEO_HOTSPOTS) > 50, "GEO_HOTSPOTS is populated", str(len(main.GEO_HOTSPOTS)))
    ok(len(main.KEYWORDS_CATEGORY) == 6, "KEYWORDS_CATEGORY covers the 6 categories",
       str(sorted(main.KEYWORDS_CATEGORY)))


def check_restored_helpers() -> None:
    """Execute the helpers that were deleted once, so they are proven to run."""
    print("\n=== exécution réelle des fonctions restaurées ===")
    import main

    lat, lon, country, region = main.extract_geo("Gaza strike")
    ok((lat, lon) == (31.45, 34.38) and region == "Moyen-Orient",
       "extract_geo geolocates a known hotspot", f"{lat},{lon} {region}")
    fb = main.extract_geo("rien de connu ici")
    ok(fb[2] == "International" and fb[3] == "Global",
       "extract_geo falls back to International/Global", str(fb))
    ok(main.classify("earthquake hits the coast", "x") == "catastrophe",
       "classify maps a natural-disaster headline")
    ok(main.classify("cholera outbreak reported", "WHO") == "epidemie",
       "classify honours the source override")
    ok(isinstance(main.actors_from_text("army offensive JNIM", "Afrique"), list),
       "actors_from_text returns a list")
    ok(main.needs_from_cat("inconnu") == ["Assistance"],
       "needs_from_cat has a default branch")


def main_entry() -> int:
    """Run every check and return a process exit code."""
    check_modules()
    check_detector_is_not_blind()
    check_registries()
    check_restored_helpers()
    print(f"\n──────── backend surface: {PASS} passed, {FAIL} failed ────────")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main_entry())
