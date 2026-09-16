# HUMAN-OSINT v4.1 — Documentation

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

_HUMAN-OSINT v4.1 ULTIMATE LIVE API — version **4.1.0**, 28 endpoints._

| Méthode | Chemin | Paramètres | Résumé |
|---|---|---|---|
| `GET` | `/` | — | Serve Index |
| `POST` | `/api/ai/analyze` | — | Ai Analyze |
| `GET` | `/api/ai/providers` | — | Ai Providers |
| `GET` | `/api/auto-update/status` | — | Auto Update Status |
| `GET` | `/api/dorks/all` | `category`?, `severity`?, `search`? | Get All Dorks |
| `GET` | `/api/dorks/categories` | — | Dork Categories |
| `POST` | `/api/dorks/generate` | — | Generate Dork |
| `GET` | `/api/feeds/live` | `limit`?, `category`?, `region`?, `country`?, `search`? | Get Live Feeds |
| `GET` | `/api/geozones` | — | Get Geozones |
| `POST` | `/api/geozones` | — | Create Geozone |
| `DELETE` | `/api/geozones/{zone_id}` | `zone_id` | Delete Geozone |
| `GET` | `/api/health` | — | Health |
| `GET` | `/api/humanitarian/actors` | — | Humanitarian Actors |
| `GET` | `/api/incidents/history` | `category`?, `region`?, `date`?, `limit`? | History |
| `GET` | `/api/live/combined` | — | Combined |
| `POST` | `/api/live/refresh` | — | Trigger Refresh |
| `GET` | `/api/live/stream` | — | Live Stream |
| `GET` | `/api/osint/comprehensive` | — | Comprehensive |
| `GET` | `/api/osint/engines` | `category`?, `search`?, `free_only`? | Get Osint Engines |
| `POST` | `/api/osint/engines/search` | — | Search Osint Engines |
| `GET` | `/api/osint/media` | `limit`? | Media Feed |
| `GET` | `/api/osint/reverse-image/engines` | — | Reverse Image Engines |
| `POST` | `/api/osint/reverse-image/generate` | — | Reverse Image Generate |
| `GET` | `/api/osint/social` | `limit`? | Social Feed |
| `POST` | `/api/report/generate` | — | Generate Report |
| `GET` | `/api/satellite/layers` | — | Satellite Layers |
| `GET` | `/api/search/advanced` | — | Advanced Search Info |
| `GET` | `/api/security/assessment` | — | Security Assessment |


---

## 3. Backend — fonctions d'API (`main.py`)

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

### `history(category, region, date, limit)`

Query the persisted incident archive.

Args:
    category: Category filter.
    region: Region filter.
    date: ISO date to restrict the query to a single day.
    limit: Maximum number of rows (1-500).

Returns:
    List of archived incident dictionaries.

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

### `get_geozones()`

Return every persisted geozone.

Returns:
    List of :class:`GeozoneResponse` objects.

### `delete_geozone(zone_id)`

Delete a persisted geozone.

Args:
    zone_id: Identifier of the zone to remove.

Returns:
    Acknowledgement mapping.

Raises:
    HTTPException: 404 when the identifier is unknown.

### `serve_index()`

Serve the single-page front-end at the repository root.

Returns:
    ``FileResponse`` for ``index.html``.


---

## 4. Backend — fonctions internes (`main.py`)

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

Create the SQLite schema if it does not exist yet.

Idempotent: safe to call on every boot. Creates the ``incidents`` and
``geozones`` tables plus the indexes used by the filter endpoints.

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

### `lifespan(app)`

Application lifespan hook: initialise SQLite, prime the cache, start auto-update.

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

## 5. Front-end — fonctions JavaScript (`index.html`)

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

### `syncCesiumIncidents()`

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
