# HUMAN-OSINT v4.2 — Documentation

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

_HUMAN-OSINT v4.2 ULTIMATE LIVE API — version **4.2.0**, 40 endpoints._

| Méthode | Chemin | Paramètres | Résumé |
|---|---|---|---|
| `GET` | `/` | — | Serve Index |
| `POST` | `/api/ai/analyze` | — | Ai Analyze |
| `GET` | `/api/ai/providers` | — | Ai Providers |
| `GET` | `/api/auto-update/status` | — | Auto Update Status |
| `GET` | `/api/dorks/all` | `category`?, `severity`?, `search`? | Get All Dorks |
| `GET` | `/api/dorks/categories` | — | Dork Categories |
| `POST` | `/api/dorks/generate` | — | Generate Dork |
| `GET` | `/api/export/formats` | — | Export Formats |
| `GET` | `/api/export/incidents.{fmt}` | `fmt`, `category`?, `region`?, `country`?, `source`?, `search`?, `date_from`?, `date_to`?, `min_risk`?, `live_only`?, `limit`? | Export Incidents |
| `GET` | `/api/feeds/live` | `limit`?, `category`?, `region`?, `country`?, `search`? | Get Live Feeds |
| `GET` | `/api/geocode` | `q`, `limit`?, `language`? | Geocode Endpoint |
| `GET` | `/api/geocode/reverse` | `lat`, `lon`, `language`? | Reverse Geocode Endpoint |
| `GET` | `/api/geozones` | — | Get Geozones |
| `POST` | `/api/geozones` | — | Create Geozone |
| `DELETE` | `/api/geozones/{zone_id}` | `zone_id` | Delete Geozone |
| `GET` | `/api/health` | — | Health |
| `GET` | `/api/history/dates` | — | History Dates |
| `DELETE` | `/api/history/prune` | `days`? | History Prune |
| `GET` | `/api/history/stats` | — | History Stats |
| `GET` | `/api/humanitarian/actors` | — | Humanitarian Actors |
| `GET` | `/api/incidents/history` | `category`?, `region`?, `country`?, `source`?, `search`?, `date`?, `date_from`?, `date_to`?, `min_risk`?, `order`?, `limit`?, `offset`? | History |
| `GET` | `/api/live/combined` | — | Combined |
| `POST` | `/api/live/refresh` | — | Trigger Refresh |
| `GET` | `/api/live/stream` | — | Live Stream |
| `GET` | `/api/osint/comprehensive` | — | Comprehensive |
| `GET` | `/api/osint/engines` | `category`?, `search`?, `free_only`? | Get Osint Engines |
| `POST` | `/api/osint/engines/search` | — | Search Osint Engines |
| `GET` | `/api/osint/identity/catalog` | — | Identity Catalog |
| `POST` | `/api/osint/identity/email` | — | Identity Email |
| `POST` | `/api/osint/identity/person` | — | Identity Person |
| `POST` | `/api/osint/identity/phone` | — | Identity Phone |
| `POST` | `/api/osint/identity/username` | — | Identity Username |
| `GET` | `/api/osint/media` | `limit`? | Media Feed |
| `GET` | `/api/osint/reverse-image/engines` | — | Reverse Image Engines |
| `POST` | `/api/osint/reverse-image/generate` | — | Reverse Image Generate |
| `GET` | `/api/osint/social` | `limit`? | Social Feed |
| `POST` | `/api/report/generate` | — | Generate Report |
| `GET` | `/api/satellite/layers` | — | Satellite Layers |
| `GET` | `/api/search/advanced` | — | Advanced Search Info |
| `GET` | `/api/security/assessment` | — | Security Assessment |


---

## 4. Backend — fonctions d'API (`main.py`)

### `health()`

Lightweight liveness/readiness probe used by CI and Render health checks.

Returns:
    JSON with service status, version, auto-update timing, cache sizes,
    aggregate stats and the per-source status map.

### `get_live_feeds(limit, category, region, country, search)`

Return the cached live feed, optionally filtered.

Falls back to the SQLite ``incidents`` table when the in-memory cache is
still empty (e.g. immediately after a cold start).

Args:
    limit: Maximum number of incidents to return (1-300).
    category: Category filter, or ``None``/``all`` for no filtering.
    region: Region filter.
    country: Case-insensitive substring match on the country field.
    search: Free-text match over title, summary, country and actors.

Returns:
    List of incident dictionaries, newest first.

### `combined()`

Return incidents plus social and media feeds with aggregate counts.

Returns:
    Mapping with the three feeds and a ``meta`` block holding totals,
    per-region/category/country breakdowns and the refresh interval.

### `social_feed(limit)`

Return only the social-media items (Reddit, Telegram, GDELT social).

Args:
    limit: Maximum number of items to return (1-150).

Returns:
    Mapping with ``social``, ``count``, ``sources`` and ``last_updated``.

### `media_feed(limit)`

Return only the press/media items parsed from the RSS feeds.

Args:
    limit: Maximum number of items to return (1-300).

Returns:
    Mapping with ``media``, ``count``, ``sources`` and ``last_updated``.

### `comprehensive()`

Return the full cached dataset together with scraping-coverage metadata.

Returns:
    Mapping with incidents, social, media and a detailed ``meta`` block.

### `live_stream()`

Server-Sent Events feed that pushes an event on every auto-update.

The previous version compared ``len(live_cache["incidents"])`` only, so a
cycle that returned the same *count* of newer items was invisible to the
browser and the UI never refreshed. This version compares the
``content_hash`` maintained by :func:`background_loop`, sends an initial
snapshot immediately on connect, and emits an SSE comment heartbeat every
``SSE_HEARTBEAT_SEC`` seconds so proxies keep the connection open.

Yields:
    ``text/event-stream`` frames of the shape
    ``{"type": "update"|"heartbeat", "count", "social", "media",
    "content_hash", "last_updated", "next_refresh_at"}``.

### `trigger_refresh(bg)`

Force an immediate re-scrape of every source, without waiting for the next cycle.

The work is queued on FastAPI's ``BackgroundTasks`` so the caller gets an
instant acknowledgement. The refreshed content is published to
``live_cache`` (and therefore to the SSE stream) once it completes.

Args:
    bg: FastAPI background-task scheduler.

Returns:
    JSON acknowledgement with the number of incidents currently cached.

### `auto_update_status()`

Report the state of the server-side auto-update engine.

Useful for diagnosing "the feed is not updating": it exposes whether a
cycle is running, when the next one is scheduled, how long the last one
took, the last error, and per-source health.

Returns:
    JSON with ``auto_update`` flag, intervals, timestamps, content hash,
    cache sizes, stats and ``sources_status``.

### `get_all_dorks(category, severity, search)`

List the Google-dork database, optionally filtered.

Args:
    category: Dork category filter.
    severity: Severity filter (``low``, ``medium``, ``high``, ``critical``).
    search: Free-text match over title, query and description.

Returns:
    Mapping with the matching dorks and the total count.

### `generate_dork(req)`

Build a custom Google dork from user-supplied parameters.

Produces several operator variants (site, inurl, intitle, filetype,
ext, cache) combined with the requested keyword.

Args:
    req: Validated :class:`DorkGenerateRequest` payload.

Returns:
    Mapping with the generated dork variants and ready-to-open URLs.

### `dork_categories()`

List every dork category present in the database with its count.

Returns:
    List of ``{category, count}`` mappings.

### `reverse_image_engines()`

List the reverse-image-search engines the UI can deep-link to.

Returns:
    Mapping with the engine list (Google, Yandex, TinEye, Bing, Baidu,
    KarmaDecay, ...).

### `reverse_image_generate(req)`

Build reverse-image-search URLs for a given image URL.

Args:
    req: Validated :class:`ReverseImageRequest` payload.

Returns:
    Mapping of engine name to the pre-filled search URL.

### `get_osint_engines(category, search, free_only)`

List the specialised OSINT search engines.

Args:
    category: Engine category filter.
    search: Free-text match over name, description and URL.
    free_only: When true, keep only the engines that need no API key.

Returns:
    Mapping with the matching engines and the total count.

### `search_osint_engines(req)`

Fan a single query out to several OSINT engines at once.

Args:
    req: Validated :class:`AdvancedSearchRequest` payload carrying the
        query and the list of selected engines.

Returns:
    Mapping of engine name to its ready-to-open result URL.

### `advanced_search_info()`

Describe the advanced-search feature for the UI help panel.

Returns:
    Mapping with the operator syntax, examples and engine groups.

### `generate_report(req)`

Produce a structured intelligence report from the cached feed.

Args:
    req: Validated :class:`ReportRequest` payload.

Returns:
    Mapping with executive summary, filtered incidents, statistics and
    recommendations. Optionally enriched by an AI provider when the
    caller supplied an API key.

### `ai_analyze(req)`

Send the cached context to a user-provided AI provider.

Supports OpenAI, Google Gemini, Anthropic and Mistral. The API key is
supplied per request and is never stored server-side.

Args:
    req: Validated :class:`AIAnalyzeRequest` payload.

Returns:
    Mapping with the model answer, the provider used and token usage.

# Validate key

### `ai_providers()`

List the supported AI providers and their configuration requirements.

Returns:
    List of provider descriptors for the settings panel.

### `history(category, region, country, source, search, date, date_from, date_to, min_risk, order, limit, offset)`

Query the persisted event archive.

This is what makes past events consultable: every auto-update cycle
archives its events, so the feed can be browsed by day, by period or by
any combination of filters, long after the item left the live cache.

Args:
    category: Category filter, or ``all`` to disable.
    region: Region filter.
    country: Country substring filter.
    source: Source substring filter.
    search: Free-text filter over title, summary, country and source.
    date: Restrict to a single day (``YYYY-MM-DD``).
    date_from: Inclusive start of a period.
    date_to: Inclusive end of a period.
    min_risk: Minimum risk level, 1 to 5.
    order: ``desc`` for newest first, ``asc`` for oldest first.
    limit: Page size, up to 2000.
    offset: Rows to skip, for pagination.

Returns:
    Mapping with the matching ``incidents``, the ``total`` count for the
    same filters, the pagination echo and the storage backend in use.

### `history_stats()`

Aggregate the archive for the history dashboard.

Returns:
    Mapping with the archived total, the covered time range, the retention
    policy and per-category/region/country/source/day breakdowns.

### `history_dates()`

List the days for which the archive holds events.

Returns:
    Mapping with a ``dates`` list of ``{date, count}``, newest first, so
    the UI can offer a date picker restricted to days that have data.

### `history_prune(days)`

Delete archived events older than the retention window.

Args:
    days: Override for the ``OSINT_RETENTION_DAYS`` setting. ``0`` is a
        no-op, never a full wipe.

Returns:
    Mapping with the number of deleted rows and the remaining count.

### `security_assessment()`

Compute a synthetic security posture for the monitored regions.

Returns:
    Mapping with per-region risk scores, trends and contributing factors.

### `humanitarian_actors()`

List the humanitarian actors referenced by the platform.

Returns:
    Mapping with actor profiles (agencies, NGOs, armed groups) and their
    areas of operation.

### `satellite_layers()`

List the satellite and map layers available to the front-end.

Returns:
    Mapping describing the 2D tile layers and the 3D Cesium base layers
    (Esri World Imagery, OpenStreetMap, dark basemaps, labels).

### `create_geozone(zone)`

Persist a user-drawn geozone.

Args:
    zone: Validated :class:`GeozoneCreate` payload.

Returns:
    The stored zone as a :class:`GeozoneResponse`.

Raises:
    HTTPException: 500 when the row could not be written.

### `get_geozones()`

Return every persisted geozone.

Returns:
    List of :class:`GeozoneResponse` objects, newest first.

### `delete_geozone(zone_id)`

Delete a persisted geozone.

Args:
    zone_id: Identifier of the zone to remove.

Returns:
    Acknowledgement mapping.

Raises:
    HTTPException: 404 when the identifier is unknown.

### `export_incidents(fmt, category, region, country, source, search, date_from, date_to, min_risk, live_only, limit)`

Export the event list, coordinates included, in a GIS format.

The same filters as :func:`history` apply, so an export always matches
what the user is looking at on screen.

Args:
    fmt: One of ``geojson``, ``csv``, ``kml`` or ``gpx``.
    category: Category filter.
    region: Region filter.
    country: Country substring filter.
    source: Source substring filter.
    search: Free-text filter.
    date_from: Inclusive start date.
    date_to: Inclusive end date.
    min_risk: Minimum risk level.
    live_only: Export the live cache instead of the whole archive.
    limit: Maximum number of rows.

Returns:
    A downloadable file response with the correct MIME type.

Raises:
    HTTPException: 400 when the format is not supported.

### `export_formats()`

List the GIS export formats the API can produce.

Returns:
    Mapping of format key to its MIME type, file extension, description
    and the software that reads it.

### `geocode_endpoint(q, limit, language)`

Resolve a place name, address or landmark to coordinates.

Proxied through the backend rather than called from the browser so the
Nominatim usage policy is respected (one request per second, a
User-Agent identifying the application) and to avoid CORS issues.

Args:
    q: Free-text query.
    limit: Maximum number of results.
    language: Preferred language for the labels.

Returns:
    Mapping with the results, each carrying coordinates, an address
    breakdown and a bounding box for zooming.

### `reverse_geocode_endpoint(lat, lon, language)`

Resolve coordinates to an address.

Args:
    lat: Latitude in decimal degrees.
    lon: Longitude in decimal degrees.
    language: Preferred language for the label.

Returns:
    Mapping with the resolved address and its parts.

### `identity_email(req)`

Analyse an e-mail address and build every related lookup link.

Args:
    req: Validated :class:`EmailRequest` payload.

Returns:
    Mapping with the parsing result, flags (disposable, role account,
    free provider), the Gravatar hash and the engine links. Optionally
    the MX records proving the domain can receive mail.

### `identity_phone(req)`

Normalise a phone number and build WhatsApp and lookup links.

Args:
    req: Validated :class:`PhoneRequest` payload.

Returns:
    Mapping with the E.164 form, country, line type, carrier hint, a
    direct WhatsApp link and the reverse-lookup engines.

### `identity_username(req)`

Build profile URLs for a username across the monitored platforms.

Args:
    req: Validated :class:`UsernameRequest` payload.

Returns:
    Mapping with name variants and one ready-to-open URL per platform.

### `identity_person(req)`

Build people-search queries for a full name.

Args:
    req: Validated :class:`PersonRequest` payload.

Returns:
    Mapping with the composed query and the search-engine links.

### `identity_catalog()`

Describe every identity tool so the UI can render its own help.

Returns:
    Mapping keyed by tool with the engine registries and a usage summary.

### `serve_index()`

Serve the single-page front-end at the repository root.

Returns:
    ``FileResponse`` for ``index.html``.


---

## 5. Backend — fonctions internes (`main.py`)

### `compute_content_hash(incidents)`

Return a short fingerprint of a list of incidents.

The previous implementation of the SSE stream only notified clients when
``len(incidents)`` changed, so a refresh that returned the same *number*
of (but newer) items never reached the browser. Hashing the identifiers
and publication dates fixes that.

Args:
    incidents: Cached incidents, each expected to expose ``link`` and
        ``published_at``.

Returns:
    A 16-char hexadecimal digest, stable for identical content and
    different as soon as one item is added, removed or re-dated.

### `get_db_connection()`

Open a SQLite connection with row access by column name.

Returns:
    ``sqlite3.Connection`` whose ``row_factory`` is ``sqlite3.Row``, so
    query results behave like dictionaries.

### `init_db()`

Create the archive schema, delegating to the storage layer.

Kept as a thin wrapper so the rest of the module (and existing callers)
do not have to know whether the backend is SQLite or PostgreSQL.

### `get_db_connection()`

Open a connection to the active storage backend.

Retained for backwards compatibility with code that still speaks DB-API
directly. New code should use :mod:`storage`, which handles both SQLite
and PostgreSQL and never lets a database error reach the API.

Returns:
    A DB-API connection with mapping-style row access.

### `extract_geo(text)`

Guess a ``(latitude, longitude)`` pair from free text.

Looks for an explicit ``lat,lon`` pattern first, then falls back to a
coarse region centroid so that every incident can be plotted on the map.

Args:
    text: Headline or summary to inspect.

Returns:
    Tuple ``(latitude, longitude)`` as floats.

### `classify(text, source)`

Map a headline onto one of the platform risk categories.

Args:
    text: Headline or summary.
    source: Originating source name, used to break ties.

Returns:
    One of ``conflit``, ``catastrophe``, ``epidemie``, ``energie``,
    ``cyber`` or ``protest``.

### `actors_from_text(text, region)`

Extract the actors named in a headline.

Args:
    text: Headline or summary.
    region: Region label, used to widen the candidate list.

Returns:
    Deduplicated list of actor names (states, armed groups, agencies).

### `needs_from_cat(cat)`

Derive the humanitarian needs implied by a category.

Args:
    cat: Category produced by :func:`classify`.

Returns:
    List of need labels such as ``Abri``, ``Médical`` or ``Eau``.

### `fetch_eonet()`

Fetch active natural events from the NASA EONET API.

Returns:
    Normalised incident dictionaries (``SATELLITE`` source type). Empty
    list on any network or parsing error - the caller keeps going.

### `fetch_usgs()`

Fetch recent significant earthquakes from the USGS GeoJSON feed.

Returns:
    Normalised incident dictionaries. Empty list on error.

### `fetch_reliefweb_api()`

Fetch the latest humanitarian reports from the ReliefWeb API.

Returns:
    Normalised incident dictionaries. Empty list on error.

### `fetch_gdelt()`

Query the GDELT DOC 2.0 API for crisis-related coverage.

Runs several predefined thematic queries and merges the results.

Returns:
    Normalised incident dictionaries. Empty list on error.

### `fetch_reddit_osint()`

Fetch the newest posts from the monitored OSINT subreddits.

Returns:
    Normalised incident dictionaries (``SOCIAL`` source type).
    Empty list on error.

### `fetch_telegram_osint()`

Scrape the public web preview of monitored Telegram channels.

Returns:
    Normalised incident dictionaries (``SOCIAL`` source type).
    Empty list on error or when ``beautifulsoup4`` is unavailable.

### `fetch_rss_single(feed_info)`

Download and parse one RSS/Atom feed.

Args:
    feed_info: Mapping with at least ``source``, ``url``, ``source_type``
        and ``region`` keys.

Returns:
    Normalised incident dictionaries for that feed. The outcome is also
    recorded in ``live_cache["sources_status"]`` so the UI can show which
    sources are healthy.

### `fetch_rss_comprehensive()`

Fetch every feed in ``RSS_FEEDS`` concurrently.

Returns:
    Flat list of normalised incidents from all reachable feeds.

### `generate_dynamic_fallback()`

Build a synthetic but plausible incident set.

Used when upstream sources are unreachable (offline mode, cold start,
blocked network) so the UI never renders empty.

Returns:
    List of normalised incident dictionaries tagged ``DEMO``.

### `fetch_all_comprehensive()`

Run every collector once and merge the results into one feed.

Pipeline: satellite APIs (EONET, USGS, ReliefWeb) -> RSS -> GDELT ->
social (Reddit, Telegram) -> demo fallback if fewer than 10 items ->
de-duplication by link -> chronological sort -> persistence of the top
120 items to SQLite.

Returns:
    Mapping with ``incidents``, ``social`` and ``media`` lists.

### `parse_date(d)`

Parse an ISO-ish date string, defaulting to *now* on failure.

### `background_loop()`

Re-scrape every source on a fixed cycle and publish the result.

Runs for the whole lifetime of the process (started from :func:`lifespan`).
Each cycle:

1. Skips if another cycle is already running (``is_refreshing`` guard).
2. Calls :func:`fetch_all_comprehensive` in a worker thread so the
   event loop stays responsive while dozens of HTTP calls are in flight.
3. Publishes incidents / social / media into ``live_cache`` and recomputes
   ``content_hash``, which is what makes :func:`live_stream` notify clients.
4. Sleeps ``BACKGROUND_REFRESH_INTERVAL`` plus a random jitter.

Errors are logged and stored in ``live_cache["last_error"]`` instead of
killing the loop, so a single broken upstream source can never stop the
auto-update.

### `prime_cache_synchronously()`

Fill ``live_cache`` once at boot, falling back to generated demo data.

Render's free tier puts the service to sleep, so the very first request
after a wake-up would otherwise return an empty feed while the background
loop completes its first cycle. Calling this from :func:`lifespan` makes
the first response useful immediately.

Returns:
    ``"live"`` when real sources answered, ``"fallback"`` when the
    generated demo dataset had to be used.

### `prime_cache_in_background()`

Fill the cache once at boot *without* delaying startup.

The lifespan hook used to ``await`` :func:`prime_cache_synchronously`
directly, next to a comment claiming a slow upstream could not block the
startup health checks. That comment was wrong: FastAPI does not finish
startup — and uvicorn therefore does not answer ``/api/health`` — until
the pre-``yield`` part of the lifespan returns. Awaiting a first fetch
that fans out to ~76 upstreams made both the CI health check and Render's
``healthCheckPath`` time out whenever those upstreams were slow.

Failures are logged rather than raised: a boot prime that dies must not
take the service down, the background loop retries on its own cycle.

### `lifespan(app)`

Application lifespan hook: initialise storage, start auto-update.

Returns immediately so ``/api/health`` answers while the first fetch —
which fans out to ~76 upstreams — runs in the background. See
:func:`prime_cache_in_background` for why the prime is not awaited.

Args:
    app: The FastAPI instance being started.

Yields:
    Control back to FastAPI for the duration of the service. On shutdown
    the background auto-update task is cancelled.

### `gen()`

Yield ``update`` frames on content change, ``heartbeat`` frames otherwise.

### `do()`

Background task body: re-scrape all sources and publish to the cache.

### `filter_incidents_for_report(topic, regions, categories, time_range, max_incidents)`

Select the incidents that match a report configuration.

Args:
    topic: Free-text topic constraint.
    regions: Region whitelist.
    categories: Category whitelist.
    time_range: One of ``24h``, ``7d``, ``30d`` or ``all``.
    max_incidents: Hard cap on the number of rows returned.

Returns:
    List of matching incident dictionaries.

### `is_recent(inc)`

Return True when ``iso`` is within the requested time range.

Args:
    iso: ISO-8601 timestamp.
    hours: Look-back window in hours.

Returns:
    ``True`` when the timestamp is newer than *now - hours*.


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

### `backend_name()`

Return the active storage backend name.

Returns:
    ``"postgres"`` or ``"sqlite"``.

### `_adapt(sql)`

Translate ``?`` placeholders into psycopg2's ``%s`` and apply DDL fixes.

Args:
    sql: SQL written in the SQLite dialect.

Returns:
    The same statement adapted for the active backend. On SQLite the
    statement is returned unchanged.

### `connect()`

Open a connection to the active backend.

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

### `query(sql, params)`

Run a SELECT and return every row as a dictionary.

Args:
    sql: Statement with ``?`` placeholders.
    params: Bound parameters.

Returns:
    List of row dictionaries. Empty list when nothing matches, and on any
    database error (logged, never raised, so one bad query cannot take the
    API down).

### `execute(sql, params)`

Run an INSERT/UPDATE/DELETE and commit.

Args:
    sql: Statement with ``?`` placeholders.
    params: Bound parameters.

Returns:
    Number of affected rows, or ``-1`` when the statement failed.

### `execute_returning(sql, params)`

Run a mutating statement with ``RETURNING`` and commit.

:func:`query` never commits, so it must not be used for INSERT/UPDATE:
the statement would be rolled back when the connection closes even though
``RETURNING`` appeared to succeed.

Args:
    sql: Mutating statement with ``?`` placeholders and a RETURNING clause.
    params: Bound parameters.

Returns:
    The returned rows as dictionaries, or an empty list on failure.

### `executemany(sql, seq)`

Run the same statement for many parameter tuples in one transaction.

Args:
    sql: Statement with ``?`` placeholders.
    seq: Iterable of parameter tuples.

Returns:
    Number of affected rows, or ``-1`` on failure.

### `_now_iso()`

Return the current UTC time as an ISO-8601 string.

### `to_epoch(value)`

Convert an ISO-8601 timestamp to epoch seconds.

Storing a numeric timestamp alongside the text one is what makes date-range
queries reliable: ISO strings are only comparable lexicographically when
every producer uses the same format and timezone offset, which 60+ scraped
sources do not guarantee.

Args:
    value: ISO-8601 timestamp, possibly naive.

Returns:
    Seconds since the epoch as a float, or ``None`` when unparseable.

### `init_schema()`

Create the archive schema if missing and migrate older tables.

Idempotent: safe on every boot. Adds the columns introduced by the archive
feature (``published_ts``, ``first_seen_at``, ``last_seen_at``) to
pre-existing installations instead of failing on a duplicate column.

Returns:
    Diagnostic mapping with the backend, the SQLite path and the archived
    row count.

### `archive_incidents(incidents)`

Persist a batch of incidents, preserving when each was first seen.

Uses ``ON CONFLICT(link) DO UPDATE`` rather than ``INSERT OR REPLACE``: the
latter deletes and re-creates the row, which would reset ``first_seen_at``
on every cycle and destroy the notion of "when did this event appear".

Args:
    incidents: Normalised incident dictionaries as produced by the
        collectors in ``main.py``.

Returns:
    Mapping with ``written`` (rows affected) and ``skipped`` counts.

### `_decode_json_field(raw, default)`

Decode a JSON-encoded TEXT column, tolerating already-decoded values.

### `_row_to_incident(row)`

Normalise a raw archive row into the incident shape the API returns.

### `_build_filters(category, region, country, search, date_from, date_to, source, min_risk)`

Assemble the WHERE clause shared by every archive query.

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

### `search_archive(limit, offset, category, region, country, search, date_from, date_to, source, min_risk, order)`

Query the persisted event archive.

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

### `count_archive(category, region, country, search, date_from, date_to, source, min_risk)`

Count archive rows matching the same filters as :func:`search_archive`.

Returns:
    The matching row count, or ``0`` when nothing matches.

### `archive_stats()`

Aggregate the archive for the history dashboard.

Returns:
    Mapping with total rows, the covered time range, and per-category,
    per-region, per-country and per-day breakdowns.

### `archive_dates()`

List the days for which the archive holds events.

Returns:
    List of ``{"date", "count"}`` mappings, newest day first.

### `prune(days)`

Delete archived events older than the retention window.

Args:
    days: Override for :data:`RETENTION_DAYS`.

Returns:
    Number of deleted rows, or ``-1`` on failure.

### `save_geozone(name, geometry_type, geojson_data, area_sqkm)`

Persist a drawn geozone and return it with its new identifier.

Args:
    name: User-chosen zone name.
    geometry_type: GeoJSON geometry type.
    geojson_data: Serialised GeoJSON geometry.
    area_sqkm: Computed surface in square kilometres.

Returns:
    The stored zone as a dictionary, or ``None`` on failure.

### `list_geozones()`

Return every persisted geozone, newest first.

Returns:
    List of geozone dictionaries.

### `delete_geozone(zone_id)`

Delete one geozone.

Args:
    zone_id: Identifier to remove.

Returns:
    ``True`` when a row was deleted.


---

## 7. Outils OSINT et SIG (`osint_tools.py`)

Les registres de services (`EMAIL_ENGINES`, `USERNAME_ENGINES`,
`PHONE_ENGINES`, `PERSON_ENGINES`) ne sont **jamais mutés** : `_fill()`
travaille sur une copie et substitue les espaces réservés dans `url` comme
dans `copy`. Une mutation du registre corromprait tous les appels suivants.

### `_throttled_get(url, params)`

Perform a GET against a third-party API, respecting a 1 req/s budget.

Args:
    url: Endpoint to call.
    params: Query parameters.

Returns:
    Decoded JSON body.

Raises:
    RuntimeError: With a human-readable message when the call fails, so
        callers can surface the reason to the user instead of a stack
        trace.

### `_coords(inc)`

Return ``[longitude, latitude]`` for an incident, or None if absent.

GeoJSON uses longitude-first ordering, the opposite of almost every other
geospatial format, so this is centralised to avoid a silent axis swap.

### `incidents_to_geojson(incidents, name)`

Build a GeoJSON FeatureCollection from incidents.

Incidents without usable coordinates are reported in the collection
metadata rather than silently dropped, so an export never looks complete
when part of the list had no position.

Args:
    incidents: Incident dictionaries.
    name: Collection name recorded in the properties.

Returns:
    A GeoJSON 1.1 FeatureCollection dictionary.

### `incidents_to_csv(incidents)`

Serialise incidents to CSV with a semicolon delimiter.

Semicolons are used because the data is French and contains many commas;
Excel in a French locale opens semicolon CSV correctly on double-click.

Args:
    incidents: Incident dictionaries.

Returns:
    The CSV document as text, prefixed with a UTF-8 BOM so Excel detects
    the encoding.

### `incidents_to_kml(incidents, name)`

Build a KML document, colour-coded by category.

Args:
    incidents: Incident dictionaries.
    name: Document name shown in Google Earth.

Returns:
    A KML 2.2 document as text.

### `incidents_to_gpx(incidents, name)`

Build a GPX 1.1 document of waypoints.

Args:
    incidents: Incident dictionaries.
    name: Document metadata name.

Returns:
    A GPX 1.1 document as text.

### `build_export(incidents, fmt, name)`

Serialise incidents in the requested GIS format.

Args:
    incidents: Incident dictionaries.
    fmt: One of the keys of :data:`EXPORT_FORMATS`.
    name: Document/collection name.

Returns:
    The serialised document as text.

Raises:
    ValueError: When ``fmt`` is not a supported format.

### `geocode(query, limit, language)`

Resolve a place name or address to coordinates via OSM Nominatim.

Args:
    query: Free-text place, address or landmark.
    limit: Maximum number of results (1-10).
    language: Preferred language for the returned labels.

Returns:
    Mapping with ``query``, ``count`` and ``results`` (each with
    ``display_name``, ``latitude``, ``longitude``, ``type``, ``country``,
    ``boundingbox``), or an ``error`` key when the call failed.

### `reverse_geocode(latitude, longitude, language)`

Resolve coordinates to an address via OSM Nominatim.

Args:
    latitude: Decimal degrees.
    longitude: Decimal degrees.
    language: Preferred language for the returned label.

Returns:
    Mapping with the resolved ``display_name``, address parts and the
    input coordinates, or an ``error`` key when the call failed.

### `_shape_nominatim(item)`

Normalise one Nominatim record into the shape the front-end expects.

### `analyze_email(email)`

Validate an email address and build every related lookup link.

No network call is made here: the analysis is purely local so it stays
fast and works offline. Use :func:`email_mx_records` separately for the
DNS check.

Args:
    email: Address to analyse.

Returns:
    Mapping with the parsing result (``valid``, ``local_part``, ``domain``,
    ``gravatar_md5``, flags) and the ``engines`` list with ready-to-open
    URLs.

### `email_mx_records(domain)`

Look up the MX records of a domain to test whether it can receive mail.

Args:
    domain: Domain name to query.

Returns:
    Mapping with ``domain``, ``exists``, the ``records`` sorted by
    preference, and an ``error`` key when DNS resolution failed.

### `_guess_mail_provider(records)`

Infer the mail provider from MX host names.

### `analyze_phone(number, default_region)`

Parse a phone number and build every related lookup link.

Args:
    number: Number in any common format, with or without country code.
    default_region: ISO country code assumed when the number has no ``+``.
        Defaults to Senegal.

Returns:
    Mapping with the E.164 form, country, number type, national format and
    the ``engines`` list, or an ``error`` key when the number is unusable.

### `analyze_username(username)`

Build profile URLs for a username across the monitored platforms.

Args:
    username: Handle to probe.

Returns:
    Mapping with the normalised username, a few derived variants and the
    ``engines`` list, or an ``error`` key when the input is unusable.

### `analyze_person(name, extras)`

Build people-search queries for a full name, optionally narrowed.

Args:
    name: Full name of the person.
    extras: Optional narrowing hints such as ``city``, ``country``,
        ``employer`` or ``email``; each is appended to the query.

Returns:
    Mapping with the composed query and the ``engines`` list, or an
    ``error`` key when the name is empty.

### `_fill(engine, values)`

Copy an engine descriptor and substitute its URL placeholders.

Args:
    engine: Descriptor from one of the ``*_ENGINES`` registries.
    values: Placeholder replacements.

Returns:
    A new descriptor with ``url`` filled in. The registries are never
    mutated, so concurrent requests cannot interfere.

### `identity_catalog()`

Describe every identity tool so the UI can render its own help.

Returns:
    Mapping keyed by tool with the engine lists and a usage summary.


---

## 8. Front-end — fonctions JavaScript (`index.html`)

### `getApiBase()`

Resolve the backend base URL from the query string, localStorage or the current origin.
@returns {{any}} The absolute API base URL without a trailing slash.

### `setBackendUrl()`

Prompt for a backend URL and reload the app against it.

### `applyToolHints()`

Inject the inline hint bar at the top of every tool panel.

Each pane gets a short description plus a «?» button that opens the full
help panel. Idempotent: running it twice will not duplicate the bars.

### `showToolHelp(tabId)`

Open the global help panel, optionally focused on one tool.

@param {string} [tabId] When given, the panel opens scrolled to that tool.

### `closeToolHelp()`

Close the global help panel.

### `loadHistory(resetOffset)`

Load the persisted event archive using the current filter panel.

Reads the date range, category, region, free-text and ordering controls,
then renders both the statistics header and the result list.

@param {number} [resetOffset] When supplied, the pager is moved to this
  offset before querying.

### `historyPage(direction)`

Move the archive pager by one page.

@param {number} direction +1 for the next page, -1 for the previous one.

### `renderHistory(rows)`

Render the archive result list.

@param {Array} rows Incident rows returned by the history endpoint.

### `loadHistoryStats()`

Load the archive statistics header and the clickable day chips.

### `pickHistoryDay(date)`

Restrict the archive to a single day and reload it.

@param {string} date Day in YYYY-MM-DD form.

### `showHistoryOnMap()`

Plot the archive rows currently on screen onto the active map.

### `exportHistory(fmt)`

Download the archive as a GIS file, honouring the current filters.

@param {string} fmt One of geojson, csv, kml or gpx.

### `exportLiveList(fmt)`

Download the list currently shown in the LIVE tab as a GIS file.

Uses the client-side builders so it also works without a backend.

@param {string} fmt One of geojson, csv, kml or gpx.

### `buildGeoJSON(rows)`

Build a GeoJSON FeatureCollection client-side (offline-capable twin of
the backend exporter).

@param {Array} rows Incident rows.
@returns {object} A GeoJSON FeatureCollection.

### `buildCSV(rows)`

Build semicolon-separated CSV client-side.

@param {Array} rows Incident rows.
@returns {string} CSV text without the BOM (the caller adds it).

### `buildKML(rows)`

Build a KML document client-side.

@param {Array} rows Incident rows.
@returns {string} KML text.

### `buildGPX(rows)`

Build a GPX document client-side.

@param {Array} rows Incident rows.
@returns {string} GPX text.

### `triggerDownload(blob, filename)`

Offer a Blob as a browser download.

@param {Blob} blob Content to download.
@param {string} filename Suggested file name.

### `runGeocode()`

Geocode the text in the search box and render the candidates.

Accepts a place name, a full address, or a "lat, lon" pair which is
detected and routed to reverse geocoding instead.

### `runReverseGeocode()`

Resolve the latitude/longitude inputs to an address and centre the map.

### `geocodeFromClipboard()`

Read a "lat, lon" pair from the clipboard and reverse-geocode it.

### `renderGeocodeResults(data)`

Render geocoding candidates with a button to fly to each one.

@param {object} data Response of the geocoding endpoint.

### `goToGeocodeResult(lat, lon, bbox)`

Fly the active map to a geocoding result, fitting its bounding box.

@param {number} lat Target latitude.
@param {number} lon Target longitude.
@param {Array|null} bbox Optional [south, north, west, east] box.

### `renderIdentityForm()`

Render the input form matching the selected identity tool.

### `runIdentity()`

Run the selected identity analysis and render the outcome.

### `renderIdentityResult(kind, data)`

Render an identity analysis: parsed facts, flags and engine links.

@param {string} kind The tool that produced the result.
@param {object} data Analysis payload returned by the backend.

### `computeTileRange(b, zoom)`

Compute the tile index range covering a bounding box at a given zoom.

Standard Web Mercator slippy-map maths, exposed as a pure function so the
projection can be unit-tested without a browser or a live map.

@param {{south:number,west:number,north:number,east:number}} b Geographic box.
@param {number} zoom Zoom level.
@returns {{minX:number,maxX:number,minY:number,maxY:number,count:number}} Tile range.

### `currentBasemapTemplate()`

Resolve the tile URL template of the currently active basemap.

@returns {{url:string,credit:string}|null} Template and attribution.

### `downloadBasemapImage(scale)`

Download the visible basemap as a high-resolution PNG.

Re-fetches the tiles covering the current view at ``currentZoom + scale - 1``
and stitches them into one canvas, so the output is genuinely sharper than
a screenshot rather than a scaled-up capture. Incident markers are drawn on
top and the attribution is burned into the corner.

Requires the tile host to send CORS headers (Esri, OSM, CARTO and Google
all do); otherwise the canvas is tainted and the export is refused with an
explanatory message.

@param {number} [scale] Resolution multiplier: 1, 2 or 3 (default 2).

### `exportCesiumImage(factor, say)`

Export the 3D globe view as a PNG.

Cesium only keeps the drawing buffer if the viewer was created with
``contextOptions.requestWebgl.preserveDrawingBuffer``, which
{@link initCesiumSatellite} sets; the scene is rendered immediately before
reading the canvas so the frame is not empty.

@param {number} factor Requested multiplier, used only for the label.
@param {Function} say Progress reporter.

### `renderManual()`

Render the user-manual tab: a section navigator plus the section bodies.

### `initClock()`

Start the UTC clock in the header.

### `switchTab(tabId)`

Show one sidebar tab and hide the others.

@param {{any}} tabId The id of the pane to activate.

### `initLeaflet()`

Create the 2D Leaflet map, its drawing tools and the incident layer.

### `setBaseTileLayer(key)`

Swap the 2D Leaflet basemap and re-order the overlays on top of it.

@param {{any}} key One of the BASEMAPS keys.

### `switchBaseLayer(key)`

Change the active basemap, dispatching to the 2D or 3D renderer.

@param {string} key One of the {@link BASEMAPS} keys
  (`sat`, `google`, `dark`, `osm`, `terrain`).

### `toggleOverlay(type)`

Add or remove a weather / terrain overlay on the 2D map.

@param {{any}} type `weather` or `terrain`.

### `switchMapMode(mode)`

Switch the map between the 2D Leaflet view and the 3D Cesium globe.

The basemap selector stays visible in both modes: in 2D it drives Leaflet,
in 3D it drives the Cesium imagery layers. The selected basemap is carried
over between modes so the user keeps the same look and feel.

@param {"2D"|"3D"} mode Target rendering mode.

### `buildCesiumLayer(key)`

Build a Cesium imagery layer (plus its labels overlay) for a basemap key.

CesiumJS removed the `Viewer` constructor option `imageryProvider` in
1.107 (deprecated in 1.104). Passing it on 1.115 is silently ignored, the
globe then shows its default `baseColor` (blue) and only the boundaries
overlay is visible. Every layer must therefore be wrapped in an
`ImageryLayer` and handed over through `baseLayer` / `imageryLayers.add`.

@param {string} key One of the {@link BASEMAPS} keys.
@returns {Cesium.ImageryLayer} The ready-to-add base imagery layer.

### `setCesiumBaseLayer(key)`

Swap the 3D globe basemap at runtime, keeping incident markers intact.

Removes every existing imagery layer and re-adds the requested basemap
followed by the labels overlay when the basemap needs one.

@param {string} key One of the {@link BASEMAPS} keys.

### `initCesiumSatellite()`

Create the Cesium 3D viewer with a real (non-blue) imagery basemap.

Uses `baseLayer` instead of the option removed in CesiumJS 1.107, sets a
dark `globe.baseColor` so an in-flight tile request never looks like the
old "blue globe" bug, and falls back to OpenStreetMap if the satellite
provider cannot be constructed.

### `syncCesiumIncidents(rowsOverride)`

Redraw every incident as an entity on the 3D Cesium globe.

### `getDemoIncidents()`

Return the built-in offline demo dataset used when no backend is configured.
@returns {{any}} An array of 30 demo incidents.

### `fetchLiveFeeds(force)`

Fetch and render the live incident feed.

   * Three modes are handled: the Android native bridge, the offline GitHub
   * Pages demo, and the normal HTTP backend. In the last case `force=true`
   * first asks the server to re-scrape every source before reading the cache.

@param {{any}} force When true, trigger a server-side re-scrape before reading.

### `fetchSocial()`

Load the social-media tab from the backend.

### `fetchMedia()`

Load the press/media tab from the backend.

### `startAutoRefresh()`

Start (or restart) the polling timer and the "age of last update" ticker.

Polling is the safety net; the SSE stream is the fast path. The ticker
repaints the header every second so the user can always see how stale the
displayed feed is, which is what makes a stalled auto-update visible.

### `paintUpdateAge()`

Repaint the "last updated Xs ago" badge from {@link lastFetchAt}.

### `toggleAutoRefresh()`

Pause or resume the automatic refresh of the news feeds.

### `connectSSE()`

Open the Server-Sent Events stream that pushes server-side auto-updates.

The server emits an ``update`` frame whenever the content fingerprint
changes (not merely when the item count changes) plus a periodic
heartbeat. On error the connection is closed and retried with an
exponential backoff capped at 60s, so a Render cold start or a proxy
timeout cannot permanently kill the live updates.

### `renderIncidents(incidents)`

Render the incident list into the LIVE panel.

@param {{any}} incidents Array of incident objects.

### `renderMapMarkers(incidents)`

Draw incident markers on the 2D Leaflet map.

@param {{any}} incidents Array of incident objects.

### `centerMapOnCoords(lat, lng)`

Fly the active map (2D or 3D) to a latitude/longitude pair.

@param {{any}} lat Target latitude.
@param {{any}} lng Target longitude.

### `applyAllFilters()`

Re-apply every active filter (region, source, category, theme, free text) to the cached feed.

### `filterByTheme(theme, btn)`

Set the active thematic filter and re-render.

@param {{any}} theme Theme key, or `all`.
@param {{any}} btn

### `fetchReverseEngines()`

Load the reverse-image-search engine list.

### `generateReverseImage()`

Build reverse-image-search URLs for the supplied image URL.

### `renderReverseResults(results, originalUrl)`

Render the reverse-image-search engine links.

@param {{any}} results Engine-to-URL mapping.
@param {{any}} originalUrl

### `handleImageFileSelect(e)`

Handle an image chosen for EXIF extraction.

@param {{any}} e

### `processImageFileV4(file, isImageTab)`

V4 image pipeline: EXIF extraction plus reverse-search links.

@param {{any}} file The image File object.
@param {{any}} isImageTab

### `locateExifOnMapImage()`

Read an image file's EXIF GPS tags and centre the map on them.

### `reverseFromExifImage()`

Launch a reverse image search for the loaded image file.

### `reverseFromExif()`

Launch a reverse image search from coordinates found in EXIF data.

### `initAdvEnginesChecklist()`

Build the advanced-search engine checklist from the loaded engine list.

### `generateAdvancedSearch()`

Build the multi-engine advanced-search URLs and render them.

### `renderAdvResults(results, query, variants)`

Render the advanced-search result links.

@param {{any}} results Engine-to-URL mapping.
@param {{any}} query
@param {{any}} variants

### `clearAdvancedSearch()`

Reset the advanced-search form to its defaults.

### `fetchEngines()`

Load the OSINT engine directory from the backend.

### `renderEngines(engines)`

Render the OSINT engine directory.

@param {{any}} engines Array of engine objects.

### `filterEngines()`

Filter the engine list by category and free text.

### `fetchDorks()`

Load the Google-dork database from the backend.

### `renderDorks(dorks)`

Render the dork list into its container.

@param {{any}} dorks Array of dork objects.

### `filterDorks()`

Filter the dork list by category, severity and free text.

### `buildLocalDork(keywords, country, site, filetype, exclude)`

Build a Google dork string entirely in the browser, without calling the backend.

@param {{any}} keywords
@param {{any}} country
@param {{any}} site Optional site: operator value.
@param {{any}} filetype
@param {{any}} exclude
@returns {{any}} The composed dork string.

### `generateCustomDork()`

Ask the backend to generate custom dork variants.

### `clearDorkBuilder()`

Reset the custom dork builder form.

### `editDorkBuilder(query)`

Update the live preview while the user edits the dork builder.

@param {{any}} query

### `copyDork(query)`

Copy a dork string to the clipboard.

@param {{any}} query

### `addToReport(title)`

Add the selected incident to the report basket.

@param {{any}} title Incident title or object to add.

### `generateReport()`

Request a full intelligence report from the backend.

### `filterIncidentsForReportLocal(topic, regions, categories, timeRange, maxInc)`

Select the incidents matching the report form, client-side.

@param {{any}} topic
@param {{any}} regions
@param {{any}} categories
@param {{any}} timeRange
@param {{any}} maxInc
@returns {{any}} List of matching incidents.

### `buildReportLocal(topic, incidents, regions, categories, timeRange, sections)`

Assemble a report from the cached incidents when no backend is reachable.

@param {{any}} topic
@param {{any}} incidents
@param {{any}} regions
@param {{any}} categories
@param {{any}} timeRange
@param {{any}} sections
@returns {{any}} A report object mirroring the backend response shape.

### `copyReport()`

Copy the generated report to the clipboard.

### `downloadReport()`

Download the generated report as a text file.

### `downloadGeoJSONReport()`

Download the saved geozones as a GeoJSON FeatureCollection.

### `clearReport()`

Empty the report basket and its preview.

### `loadAIKey()`

Restore the saved AI provider and API key from localStorage.

### `saveAIKey()`

Persist the AI provider and API key to localStorage.

### `clearAIKey()`

Remove the stored AI API key from localStorage.

### `updateAIKeyStatus()`

Refresh the AI key status indicator.

### `testAIKey()`

Validate the stored AI API key with a minimal request.

### `runAIAnalysis()`

Send the current context to the configured AI provider and render the answer.

### `copyAIResult()`

Copy the AI analysis result to the clipboard.

### `downloadAIResult()`

Download the AI analysis as a text file.

### `fetchSecurityAssessment()`

Load the regional security assessment.

### `fetchActors()`

Load the humanitarian actor directory from the backend.

### `loadSavedGeozones()`

Load the persisted geozones from localStorage and draw them.

### `saveGeozonesLocal()`

Write the geozone list to localStorage.

### `renderSavedGeozonesUI()`

Render the saved-geozone list in the GEOINT panel.

### `displaySavedGeozonesOnMap()`

Re-draw every saved geozone on the Leaflet map.

### `promptSaveCurrentDrawn()`

Ask for a name and persist the currently drawn shape as a geozone.

### `saveActiveMeasurement()`

Persist the last measurement drawn on the map.

### `deleteGeozone(zoneId)`

Delete a saved geozone from local storage and from the map.

@param {{any}} zoneId

### `zoomToZone(zoneId)`

Fit the map to the bounds of one saved geozone.

@param {{any}} zoneId

### `exportGeoJSON()`

Serialise the drawn and saved geozones to GeoJSON.
@returns {{any}} A GeoJSON FeatureCollection object.

### `setupDragAndDrop()`

Wire up drag-and-drop of image files onto the IMAGE panel.

### `handleFileSelect(e)`

Handle a file chosen through the file input.

@param {{any}} e

### `processImageFile(file)`

Read an image file and extract its EXIF metadata.

@param {{any}} file The image File object.

### `locateExifOnMap()`

Centre the map on the GPS coordinates read from an image's EXIF data.

### `openDork(query)`

Open a dork in a new browser tab as a Google search.

@param {{any}} query The dork string.

### `escapeHtml(text)`

Escape HTML special characters so untrusted feed text cannot inject markup.

@param {{any}} text
@returns {{any}} The escaped string, safe to insert with innerHTML.

### `formatDate(dateString)`

Format an ISO timestamp for display.

@param {{any}} dateString
@returns {{any}} A short human-readable date string.


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
# Backend : surface des modules (noms indéfinis, registres, helpers)
python tests/test_backend_surface.py

# Schéma de persistance sur SQLite
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

`tests/test_backend_surface.py` existe à cause d'un incident précis : un patch
v4.2 avait supprimé `GEO_HOTSPOTS`, `KEYWORDS_CATEGORY`, `extract_geo`,
`classify`, `actors_from_text` et `needs_from_cat` de `main.py` alors que douze
sites d'appel y référaient encore. `import main` continuait de fonctionner —
ces noms n'explosent que lorsque les chemins d'ingestion GDELT / Reddit /
Telegram s'exécutent réellement — et seul le step CI « Syntax check » l'avait
vu. Le banc contient un détecteur de noms non liés écrit en stdlib, **validé
contre la révision cassée** : un linter qui renvoie toujours une liste vide
passerait tous les tests sans rien garder.

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
