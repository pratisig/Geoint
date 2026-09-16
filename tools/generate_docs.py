#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regenerate DOCUMENTATION.md from the source of truth.

Rather than hand-writing an API reference that drifts out of date, this script
derives it from three things that are always current:

1. the live FastAPI OpenAPI schema (endpoints, parameters, response codes);
2. the Python docstrings in ``main.py`` (read through ``ast``);
3. the JSDoc blocks in ``index.html`` (read with a small regex scanner).

Usage:
    python tools/generate_docs.py                 # needs the API on :8000
    python tools/generate_docs.py --offline       # skip the OpenAPI section
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAIN_PY = ROOT / "main.py"
INDEX_HTML = ROOT / "index.html"
OUT = ROOT / "DOCUMENTATION.md"


def python_functions() -> list[dict]:
    """Extract every function in main.py with its docstring and signature.

    Returns:
        List of dicts with ``name``, ``signature``, ``doc``, ``lineno`` and
        ``kind`` (``endpoint`` for FastAPI routes, ``function`` otherwise).
    """
    src = MAIN_PY.read_text(encoding="utf-8")
    tree = ast.parse(src)
    lines = src.split("\n")
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        # Look for a FastAPI decorator directly above the function.
        route = None
        for dec in node.decorator_list:
            if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
                if dec.args and isinstance(dec.args[0], ast.Constant):
                    route = f"{dec.func.attr.upper()} {dec.args[0].value}"
        args = [a.arg for a in node.args.args if a.arg != "self"]
        sig = f"{node.name}({', '.join(args)})"
        out.append({
            "name": node.name,
            "signature": sig,
            "doc": (ast.get_docstring(node) or "").strip(),
            "lineno": node.lineno,
            "route": route,
        })
    out.sort(key=lambda d: d["lineno"])
    return out


JS_FUNC_RE = re.compile(r"^(?P<indent>\s*)(?:async\s+)?function\s+(?P<name>\w+)\s*\((?P<params>[^)]*)\)", re.M)


def js_functions() -> list[dict]:
    """Extract every JS function in index.html with the JSDoc block above it.

    Returns:
        List of dicts with ``name``, ``params``, ``summary`` and ``doc``.
    """
    src = INDEX_HTML.read_text(encoding="utf-8")
    lines = src.split("\n")
    out = []
    for m in JS_FUNC_RE.finditer(src):
        start_line = src[: m.start()].count("\n")
        # Walk upwards over the JSDoc block, if any.
        j = start_line - 1
        while j >= 0 and lines[j].strip() == "":
            j -= 1
        doc = ""
        if j >= 0 and lines[j].strip().endswith("*/"):
            k = j
            while k >= 0 and not lines[k].strip().startswith("/**"):
                k -= 1
            block = lines[max(k, 0): j + 1]
            cleaned = []
            for ln in block:
                ln = ln.strip()
                ln = re.sub(r"^/\*\*", "", ln)
                ln = re.sub(r"\*/$", "", ln)
                ln = re.sub(r"^\*\s?", "", ln)
                cleaned.append(ln.rstrip())
            doc = "\n".join(cleaned).strip()
        summary = doc.split("\n")[0] if doc else ""
        params = [p.strip().split("=")[0].strip() for p in m.group("params").split(",") if p.strip()]
        out.append({"name": m.group("name"), "params": params, "summary": summary, "doc": doc})
    return out


def openapi_section(base_url: str) -> str:
    """Build the endpoint reference table from the live OpenAPI schema."""
    with urllib.request.urlopen(f"{base_url}/openapi.json", timeout=20) as r:
        spec = json.load(r)
    rows = []
    for path, ops in sorted(spec["paths"].items()):
        for method, op in ops.items():
            params = ", ".join(
                f"`{p['name']}`" + ("" if p.get("required") else "?")
                for p in op.get("parameters", [])
            ) or "—"
            summary = (op.get("summary") or "").replace("_", " ")
            rows.append(f"| `{method.upper()}` | `{path}` | {params} | {summary} |")
    body = "\n".join(rows)
    return (
        f"_{spec['info']['title']} — version **{spec['info']['version']}**, "
        f"{len(rows)} endpoints._\n\n"
        "| Méthode | Chemin | Paramètres | Résumé |\n"
        "|---|---|---|---|\n" + body + "\n"
    )


def render(py_funcs: list[dict], js_funcs: list[dict], api_section: str) -> str:
    """Assemble the final Markdown document."""
    endpoints = [f for f in py_funcs if f["route"]]
    internals = [f for f in py_funcs if not f["route"]]

    def block(f: dict, heading_prefix: str = "###") -> str:
        doc = f["doc"] or "_Pas de documentation._"
        return f"{heading_prefix} `{f['signature']}`\n\n{doc}\n"

    py_endpoint_md = "\n".join(block(f) for f in endpoints)
    py_internal_md = "\n".join(block(f) for f in internals)
    js_md = "\n".join(
        f"### `{f['name']}({', '.join(f['params'])})`\n\n{f['doc'] or '_Pas de documentation._'}\n"
        for f in js_funcs
    )

    return f"""# HUMAN-OSINT v4.1 — Documentation

Document généré par `tools/generate_docs.py`. Ne pas éditer à la main :
modifiez les docstrings dans `main.py` et les blocs JSDoc dans `index.html`,
puis relancez le générateur.

```bash
uvicorn main:app --port 8000 &     # dans un autre terminal
python tools/generate_docs.py
```

---

## 1. Vue d'ensemble

HUMAN-OSINT est une plateforme OSINT/GEOINT composée de trois livrables qui
partagent la même interface :

| Livrable | Fichier | Rôle |
|---|---|---|
| Backend FastAPI | `main.py` | Scraping, agrégation, cache, auto-update, API |
| Front-end web | `index.html` | SPA Leaflet 2D + Cesium 3D, servi par le backend ou GitHub Pages |
| Application Android | `app/` | WebView embarquant la même SPA (`app/src/main/assets/osint/index.html`) |

### Mise à jour automatique des flux

Le rafraîchissement repose sur deux mécanismes complémentaires :

1. **Côté serveur** — `background_loop()` re-scrape toutes les sources toutes
   les `OSINT_REFRESH_INTERVAL` secondes (60 s par défaut, plus un jitter
   aléatoire) et publie le résultat dans `live_cache`. Une empreinte
   (`content_hash`) est recalculée à chaque cycle.
2. **Côté client** — le navigateur ouvre un canal **Server-Sent Events** sur
   `/api/live/stream`. Le serveur pousse un événement dès que `content_hash`
   change, plus un heartbeat toutes les `OSINT_SSE_HEARTBEAT` secondes pour
   que les proxys ne coupent pas la connexion. Un polling toutes les 60 s sert
   de filet de sécurité, et un rafraîchissement immédiat est déclenché au
   retour sur un onglet laissé en arrière-plan.

> **Piège historique** : l'ancienne implémentation SSE ne comparait que le
> *nombre* d'incidents. Un cycle renvoyant le même nombre d'éléments plus
> récents ne produisait aucun événement, et l'interface ne se mettait jamais à
> jour. La comparaison porte désormais sur l'empreinte du contenu.

Diagnostic complet : `GET /api/auto-update/status`.

### Variables d'environnement

| Variable | Défaut | Effet |
|---|---|---|
| `OSINT_REFRESH_INTERVAL` | `60` | Période de re-scraping, en secondes |
| `OSINT_REFRESH_JITTER` | `10` | Amplitude du jitter aléatoire ajouté à chaque cycle |
| `OSINT_SSE_HEARTBEAT` | `15` | Période du heartbeat SSE, en secondes |
| `OSINT_MAX_CACHE` | `300` | Nombre maximal d'incidents conservés en mémoire |

> **Une seule instance** : le cache et le flux SSE vivent dans la mémoire du
> processus. Avec plusieurs instances, un client connecté à l'une ne verrait
> pas les mises à jour produites par l'autre. `render.yaml` fixe
> `numInstances` et `--workers 1` en conséquence.

---

## 2. Références de l'API

{api_section}

---

## 3. Backend — fonctions d'API (`main.py`)

{py_endpoint_md}

---

## 4. Backend — fonctions internes (`main.py`)

{py_internal_md}

---

## 5. Front-end — fonctions JavaScript (`index.html`)

{js_md}

---

## 6. Tests

```bash
# Backend
python -m py_compile main.py
python -c "import main"

# Front-end (jsdom) : vérifie le globe 3D, les hints, l'auto-update
cd tests && npm install && npm test

# Avec le backend réel en face
uvicorn main:app --port 8000 &
cd tests && API_URL=http://127.0.0.1:8000 npm test
```

Le test front-end charge la page dans jsdom en remplaçant Leaflet, Cesium,
turf et ExifReader par des bouchons. Le bouchon Cesium enregistre les options
passées à `Cesium.Viewer`, ce qui permet de vérifier que le globe est bien
construit avec `baseLayer` et non avec l'option `imageryProvider` supprimée
dans CesiumJS 1.107 — la cause du « globe bleu à frontières blanches ».
"""


def main() -> int:
    """Generate DOCUMENTATION.md. Returns a process exit code."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--offline", action="store_true", help="skip the live OpenAPI section")
    ap.add_argument("--api", default="http://127.0.0.1:8000", help="base URL of a running API")
    args = ap.parse_args()

    py_funcs = python_functions()
    js_funcs = js_functions()
    undoc_py = [f["name"] for f in py_funcs if not f["doc"]]
    undoc_js = [f["name"] for f in js_funcs if not f["doc"]]
    if undoc_py:
        print(f"WARNING: {len(undoc_py)} Python functions without docstring: {undoc_py}", file=sys.stderr)
    if undoc_js:
        print(f"WARNING: {len(undoc_js)} JS functions without JSDoc: {undoc_js}", file=sys.stderr)

    if args.offline:
        api_section = "_Section générée en mode `--offline` : relancez sans ce flag avec l'API démarrée._\n"
    else:
        try:
            api_section = openapi_section(args.api.rstrip("/"))
        except Exception as exc:  # noqa: BLE001 - degrade gracefully
            print(f"WARNING: could not read OpenAPI schema ({exc}); use --offline", file=sys.stderr)
            api_section = f"_Schema OpenAPI indisponible ({exc})._\n"

    OUT.write_text(render(py_funcs, js_funcs, api_section), encoding="utf-8")
    print(
        f"Wrote {OUT.relative_to(ROOT)}: "
        f"{len(py_funcs)} Python functions ({len([f for f in py_funcs if f['route']])} endpoints), "
        f"{len(js_funcs)} JS functions"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
