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
STORAGE_PY = ROOT / "storage.py"
OSINT_TOOLS_PY = ROOT / "osint_tools.py"
INDEX_HTML = ROOT / "index.html"
OUT = ROOT / "DOCUMENTATION.md"


def python_functions(path: Path | None = None) -> list[dict]:
    """Extract every function of a Python module with its docstring.

    Args:
        path: Module to parse. Defaults to ``main.py``.

    Returns:
        List of dicts with ``name``, ``signature``, ``doc``, ``lineno`` and
        ``route`` (set for FastAPI route handlers).
    """
    src = (path or MAIN_PY).read_text(encoding="utf-8")
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


def render(py_funcs: list[dict], js_funcs: list[dict], api_section: str,
           storage_funcs: list[dict] | None = None,
           tools_funcs: list[dict] | None = None) -> str:
    """Assemble the final Markdown document.

    Args:
        py_funcs: Functions of ``main.py``.
        js_funcs: Functions of ``index.html``.
        api_section: Markdown table built from the live OpenAPI schema.
        storage_funcs: Functions of ``storage.py``.
        tools_funcs: Functions of ``osint_tools.py``.

    Returns:
        The complete ``DOCUMENTATION.md`` body.
    """
    endpoints = [f for f in py_funcs if f["route"]]
    internals = [f for f in py_funcs if not f["route"]]
    storage_funcs = storage_funcs or []
    tools_funcs = tools_funcs or []

    def block(f: dict, heading_prefix: str = "###") -> str:
        doc = f["doc"] or "_Pas de documentation._"
        return f"{heading_prefix} `{f['signature']}`\n\n{doc}\n"

    py_endpoint_md = "\n".join(block(f) for f in endpoints)
    py_internal_md = "\n".join(block(f) for f in internals)
    js_md = "\n".join(
        f"### `{f['name']}({', '.join(f['params'])})`\n\n{f['doc'] or '_Pas de documentation._'}\n"
        for f in js_funcs
    )
    storage_md = "\n".join(block(f) for f in storage_funcs)
    tools_md = "\n".join(block(f) for f in tools_funcs)

    return f"""# HUMAN-OSINT v4.2 — Documentation

Document généré par `tools/generate_docs.py`. Ne pas éditer à la main :
modifiez les docstrings dans `main.py`, `storage.py`, `osint_tools.py` et les
blocs JSDoc dans `index.html`, puis relancez le générateur.

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
| Persistance | `storage.py` | Archive des événements, SQLite **ou** PostgreSQL selon `DATABASE_URL` |
| Outils OSINT/SIG | `osint_tools.py` | Export GeoJSON/CSV/KML/GPX, géocodage, analyse d'identité |
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
| `DATABASE_URL` | _(vide)_ | Chaîne PostgreSQL. Dès qu'elle commence par `postgres://` ou `postgresql://`, l'archive part en PostgreSQL au lieu de SQLite |
| `OSINT_DB_PATH` | `osint_database.db` | Chemin du fichier SQLite (ignoré quand `DATABASE_URL` est défini) |
| `OSINT_RETENTION_DAYS` | `365` | Ancienneté au-delà de laquelle `prune()` supprime les événements archivés |
| `OSINT_DB_STRICT` | _(vide)_ | À `1`, une base PostgreSQL injoignable lève une exception au lieu de basculer sur SQLite. Utilisé par les tests |

> **Une seule instance** : le cache et le flux SSE vivent dans la mémoire du
> processus. Avec plusieurs instances, un client connecté à l'une ne verrait
> pas les mises à jour produites par l'autre. `render.yaml` fixe
> `numInstances` et `--workers 1` en conséquence.

---

## 2. Nouveautés v4.2

### 2.1 Archive des événements passés

Chaque cycle d'auto-update écrit ses incidents dans l'archive via
`storage.archive_incidents()`. La colonne `link` porte une contrainte
`UNIQUE` : un événement déjà connu n'est pas dupliqué, son `first_seen_at`
est préservé et seul `last_seen_at` avance.

Consultation : `GET /api/incidents/history` accepte `category`, `region`,
`country`, `source`, `search`, `date`, `date_from`, `date_to`, `min_risk`,
`order`, `limit` (≤ 2000) et `offset`. `GET /api/history/stats` renvoie les
agrégats et la chronologie ; `GET /api/history/dates` liste les jours
disponibles ; `DELETE /api/history/prune?days=N` purge au-delà de la rétention.

> **Contrainte Render.** Le niveau gratuit a un **système de fichiers
> éphémère** et n'autorise **pas de disque persistant** : un fichier SQLite y
> est effacé à chaque endormissement et à chaque redéploiement. `render.yaml`
> déclare donc une instance PostgreSQL et branche `DATABASE_URL`.
> Attention : le PostgreSQL **gratuit** de Render expire **30 jours** après sa
> création, avec 14 jours de grâce avant suppression. Pour un historique
> durable, passez sur une instance payante ou exportez régulièrement.
>
> Si la base devient injoignable, l'application **démarre quand même** :
> `storage.connect()` journalise l'erreur et bascule le processus sur SQLite.
> Cette bascule est couverte par un test dédié.

### 2.2 Export SIG

`GET /api/export/incidents.<format>` accepte `geojson`, `csv`, `kml` et `gpx`
et tous les filtres de l'historique : on exporte exactement ce que l'on voit.

| Format | MIME | Destination |
|---|---|---|
| GeoJSON | `application/geo+json` | QGIS, ArcGIS, PostGIS, Leaflet |
| CSV | `text/csv; charset=utf-8` | Excel / tableur (séparateur `;`, BOM inclus) |
| KML | `application/vnd.google-earth.kml+xml` | Google Earth |
| GPX | `application/gpx+xml` | GPS, OsmAnd |

Les coordonnées sont en **longitude d'abord** (CRS84), comme l'exige la RFC
7946. Les lignes sans coordonnées valides — hors plage ou exactement `0,0` —
sont écartées et comptées dans `metadata.without_coordinates`, jamais émises
sur `0,0`. Les en-têtes `X-Exported-Rows` et `X-Rows-With-Coordinates`
résumé le résultat côté client.

Le front-end possède ses propres constructeurs (`buildGeoJSON`, `buildCSV`,
`buildKML`, `buildGPX`) pour que l'export de la liste affichée fonctionne
aussi sans backend, en mode démo GitHub Pages.

### 2.3 Fond de carte haute résolution

`📸 CARTE HD` ne fait pas une capture d'écran : la fonction
`downloadBasemapImage(scale)` recalcule la plage de tuiles couvrant la vue
courante au niveau `zoom + scale - 1`, les re-télécharge et les assemble dans
un canvas. Un facteur 2 donne donc **quatre fois** plus de pixels à
résolution native, un facteur 3 neuf fois plus. Les incidents sont dessinés
par-dessus et l'attribution incrustée.

Le nombre de tuiles est borné à 900 : au-delà, l'export est refusé avec le
compte exact plutôt que de saturer la mémoire du navigateur. Le serveur de
tuiles doit envoyer les en-têtes CORS (Esri, OSM, CARTO et Google le font) ;
sinon le canvas est « pollué » et l'export est refusé avec un message.

Pour le globe 3D, `Cesium.Viewer` est créé avec
`contextOptions.requestWebgl.preserveDrawingBuffer` — sans cette option
`toDataURL()` renvoie une image vide.

### 2.4 Géocodage

`GET /api/geocode?q=...` (direct) et `GET /api/geocode/reverse?lat=...&lon=...`
(inverse), via OpenStreetMap Nominatim côté backend, avec un débit limité à
une requête par seconde conformément à leur politique d'usage. Le champ de
recherche détecte seul une paire « lat, lon » et la route vers le
géocodage inverse.

### 2.5 Outils d'identité

| Outil | Endpoint | Ce qui est calculé |
|---|---|---|
| E-mail | `POST /api/osint/identity/email` | validation, domaine jetable (sous-domaines compris), compte générique, hash Gravatar, pseudos dérivés, **résolution DNS des MX** |
| Téléphone | `POST /api/osint/identity/phone` | normalisation E.164, pays, type de ligne, opérateur estimé, lien WhatsApp |
| Pseudo | `POST /api/osint/identity/username` | 23 plateformes + variantes du pseudo |
| Personne | `POST /api/osint/identity/person` | 9 services, requête composée nom + ville + pays + employeur |

47 services au total, listés par `GET /api/osint/identity/catalog`.

> **Aucune URL inventée.** Les registres ne contiennent que des motifs
> vérifiés. Les services qui n'exposent pas de lien de recherche direct
> (Truecaller, Sync.me) portent `deep_link: false` et un champ `copy` que
> l'interface place dans le presse-papiers : on ouvre le site et on colle.

> **Éthique.** Ces outils n'agrègent que des liens ; ils ne collectent rien.
> Le croisement d'identités portant sur des personnes réelles est encadré par
> la loi : restez dans le périmètre confié et documentez vos accès.

---

## 3. Références de l'API

{api_section}

---

## 4. Backend — fonctions d'API (`main.py`)

{py_endpoint_md}

---

## 5. Backend — fonctions internes (`main.py`)

{py_internal_md}

---

## 6. Persistance (`storage.py`)

Couche de stockage à deux backends. Tout le SQL est écrit en dialecte SQLite
puis adapté à la volée par `_adapt()` : `?` → `%s`, `INTEGER PRIMARY KEY
AUTOINCREMENT` → `SERIAL PRIMARY KEY`, `REAL` → `DOUBLE PRECISION`,
`BOOLEAN DEFAULT 0` → `BOOLEAN DEFAULT FALSE`. Les colonnes ajoutées par
l'archive (`published_ts`, `first_seen_at`, `last_seen_at`) sont créées par
migration, ce qui rend `init_schema()` idempotent sur une installation
existante.

Deux règles à ne pas oublier :

- `query()` **ne commit jamais**. Un `INSERT ... RETURNING` passé par
  `query()` renvoie la ligne puis la déroule silencieusement. Utilisez
  `execute_returning()`.
- Les deux backends renvoient des **mappings** (`sqlite3.Row` et
  `RealDictCursor`). Accédez toujours aux colonnes **par nom** :
  `RealDictRow` ne supporte pas `row[0]`. Le chemin SQLite accepte les deux,
  c'est précisément pourquoi le test double backend existe.

{storage_md}

---

## 7. Outils OSINT et SIG (`osint_tools.py`)

Les registres de services (`EMAIL_ENGINES`, `USERNAME_ENGINES`,
`PHONE_ENGINES`, `PERSON_ENGINES`) ne sont **jamais mutés** : `_fill()`
travaille sur une copie et substitue les espaces réservés dans `url` comme
dans `copy`. Une mutation du registre corromprait tous les appels suivants.

{tools_md}

---

## 8. Front-end — fonctions JavaScript (`index.html`)

{js_md}

---

## 9. Manuel utilisateur intégré

L'onglet **MANUEL** rend la constante `MANUAL`, un registre unique de dix
sections : démarrage, auto-update, SEARCH, ENGINES, DORKS, GEOINT, HISTORIQUE,
GÉOCODAGE, IDENTITÉ, REPORT/IA, plus le copyright. Une seule source de vérité
pour l'onglet, le panneau d'aide en-tête et ce document.

Les trois sections demandées explicitement couvrent :

- **SEARCH** — les opérateurs `site:`, `inurl:`, `intitle:`, `filetype:`,
  `ext:`, `cache:`, comment composer la requête, et ce que fait *Tout
  effacer*.
- **ENGINES** — les 40 moteurs regroupés par famille (infrastructure, fuites,
  domaines, mobilité), le filtre « gratuit uniquement », et ce que renvoie
  réellement chaque service.
- **DORKS** — ce qu'est un dork, le filtrage par catégorie et par sévérité,
  l'échelle `critical`/`high`/`medium`/`low`, le générateur de variantes, et
  le cadre légal.

Chaque section suit la même trame : *à quoi ça sert*, *comment s'en servir*,
*d'où viennent les données*, *limites et cadre légal*.

---

## 10. Tests

```bash
# Backend : import réel + schéma de persistance sur SQLite
python -c "import main"
python tests/test_storage.py

# Le même banc contre un vrai PostgreSQL
python tests/test_storage.py --postgres postgresql://user@host/db

# Front-end (jsdom) : globe 3D, hints, auto-update, archive, export SIG,
# géocodage, identité, manuel, export HD
cd tests && npm install && npm test

# Avec le backend réel en face (active la section d'intégration live)
uvicorn main:app --port 8000 &
cd tests && API_URL=http://127.0.0.1:8000 npm test
```

`tests/test_storage.py` rejoue **la suite identique** sur SQLite et sur
PostgreSQL. Le mode `--postgres` pose `OSINT_DB_STRICT=1`, donc une base
injoignable fait échouer le banc au lieu de le dégrader silencieusement en
SQLite : un « PostgreSQL 47/47 » signifie bien que PostgreSQL a tourné.

Le banc jsdom vérifie notamment la projection Web Mercator de
`computeTileRange()` contre une implémentation indépendante écrite avec la
forme tangente, les quatre constructeurs d'export côté client, les filtres
embarqués dans les URL d'export, le routage automatique d'une paire
« lat, lon » vers le géocodage inverse, et le refus d'un export HD couvrant
plus de 900 tuiles.

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
    storage_funcs = python_functions(STORAGE_PY)
    tools_funcs = python_functions(OSINT_TOOLS_PY)
    js_funcs = js_functions()
    all_py = [("main.py", py_funcs), ("storage.py", storage_funcs), ("osint_tools.py", tools_funcs)]
    for label, funcs in all_py:
        undoc = [f["name"] for f in funcs if not f["doc"]]
        if undoc:
            print(f"WARNING: {len(undoc)} {label} functions without docstring: {undoc}", file=sys.stderr)
    undoc_js = [f["name"] for f in js_funcs if not f["doc"]]
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

    OUT.write_text(
        render(py_funcs, js_funcs, api_section, storage_funcs, tools_funcs),
        encoding="utf-8",
    )
    print(
        f"Wrote {OUT.relative_to(ROOT)}: "
        f"{len(py_funcs)} main.py functions ({len([f for f in py_funcs if f['route']])} endpoints), "
        f"{len(storage_funcs)} storage.py, {len(tools_funcs)} osint_tools.py, "
        f"{len(js_funcs)} JS functions"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
