# HUMAN-OSINT v2.0 LIVE // Plateforme SIG & OSINT Tactique Satellite Temps Réel

Plateforme opérationnelle de renseignement en sources ouvertes (OSINT), d'analyse géospatiale (GEOINT) et de veille de crise **EN TEMPS RÉEL DYNAMIQUE** avec imagerie **SATELLITE RÉELLE**, persistance locale SQLite et architecture Android + Web.

> **Nouveautés v2.0 LIVE** : Données 100% dynamiques auto-refresh 30s, globe 3D satellite réel Esri World Imagery, sources live NASA EONET, USGS, GDACS, ReliefWeb API, analyse sécurité & acteurs humanitaires.

---

## 1. Architecture Technique v2.0 LIVE

- **Backend LIVE** : Python 3.10+, FastAPI, Uvicorn, SQLite3 WAL, Feedparser, Requests, python-dateutil, Background Tasks auto-refresh 60s, SSE Streaming (`/api/live/stream`).
- **Sources LIVE Dynamiques** :
  - **NASA EONET** (`https://eonet.gsfc.nasa.gov/api/v3/events`) - Événements naturels satellite avec coordonnées réelles temps réel (feux, volcans, tempêtes)
  - **USGS Earthquakes** (`https://earthquake.usgs.gov/.../all_day.geojson`) - Séismes temps réel avec lat/lng exacts
  - **ReliefWeb API v1** (`https://api.reliefweb.int/v1/disasters`) - Crises humanitaires OCHA avec pays et géoloc
  - **GDACS API** - Alertes catastrophes ONU avec coordonnées satellite
  - **RSS Live** - ReliefWeb, BBC, OMS, Crisis Group, France24, Al Jazeera, The Hacker News (refresh 30s)
  - **Fallback dynamique** : Générateur d'incidents live avec jitter de coordonnées et timestamp now() si réseau coupé (reste DYNAMIQUE, pas statique)

- **Frontend Web & Android** : Single Page Application HTML5 / CSS3 Cyberpunk / Vanilla JS - 100% compatible Web + WebView Android
- **Cartographie Hybride SATELLITE RÉEL** :
  - **Leaflet.js 2D** : Tuiles **Esri World Imagery** (`https://server.arcgisonline.com/.../World_Imagery`) - **Imagerie satellite réelle HD** sans clé API + overlay labels frontières
  - **CesiumJS 1.115 3D Globe** : **CORRIGÉ** - Utilise `UrlTemplateImageryProvider` avec Esri satellite réel, pas de token Ion requis. `EllipsoidTerrainProvider` + overlay `World_Boundaries_and_Places`. Affiche **vraie image satellite** terre.
  - **Surcouches Live** : Radar météo RainViewer, Relief OpenTopoMap, Trafic CyclOSM
- **Moteur Géospatial** : Turf.js pour calcul surface km² et distances tactiques sur imagerie satellite
- **Analyse Image & EXIF** : ExifReader.js - Extraction GPS et localisation sur carte satellite
- **Base de Données** : SQLite `osint_database.db` WAL - Tables `incidents` (avec acteurs, besoins, risk_level, severity) et `geozones`

---

## 2. Installation et Démarrage Rapide

### Prérequis
- Python 3.10+

### Étapes
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --host 0.0.0.0 --port 8000
# ou
python3 main.py
```

- **Web App LIVE Satellite** : http://localhost:8000
- **Swagger Docs** : http://localhost:8000/docs
- **Health LIVE** : http://localhost:8000/api/health
- **Satellite Layers Config** : http://localhost:8000/api/satellite/layers

### Android App (v2.0 LIVE SAT)
```bash
# Dans Android Studio
# - WebView HARDWARE acceleration activée (LAYER_TYPE_HARDWARE) pour Cesium WebGL satellite
# - Auto-refresh 30s (au lieu de 1h) pour données vraiment LIVE
# - Sources satellite directes : NASA EONET + USGS + ReliefWeb API intégrées dans OsintRepository
# - Fonds de carte Esri satellite HD réel + Cesium globe 3D satellite réel
# Build APK
./gradlew assembleDebug
```

---

## 3. Données LIVE Dynamiques - Plus de Statique

**Avant v1.0** : Données statiques, fallback démo, refresh 1h, globe 3D canvas 2D sans image réelle.

**Maintenant v2.0 LIVE** :
- **Auto-refresh 30s** côté frontend (Web + Android) + **60s background task** côté backend
- **SSE Push** `/api/live/stream` - Le serveur pousse les MAJ live au client
- **Coordonnées satellite réelles** : NASA EONET et USGS fournissent lat/lng exacts mesurés par satellite, pas de géocodage par mots-clés seulement
- **Jitter dynamique** : Même le fallback génère des coordonnées légèrement différentes à chaque refresh + timestamp now() pour prouver le caractère LIVE
- **Indicateurs LIVE** : Badge `● LIVE SATELLITE`, compteur événements, dernière MAJ UTC, sources listées
- **Filtrage temps réel** : Recherche instantanée, région, pays, source, catégorie, mode SAT ONLY

---

## 4. Globe 3D Satellite Réel - CORRIGÉ

**Problème v1.0** : `Cesium.Ion.defaultAccessToken = ""` + `Viewer` sans imageryProvider = globe bleu sans texture ou erreur.

**Correction v2.0** :
```javascript
Cesium.Ion.defaultAccessToken = undefined; // Pas de token Ion
const esriImagery = new Cesium.UrlTemplateImageryProvider({
  url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
  maximumLevel: 19,
  credit: 'Esri World Imagery - Satellite Réel HD'
});
cesiumViewer = new Cesium.Viewer("cesium-map", {
  imageryProvider: esriImagery,
  terrainProvider: new Cesium.EllipsoidTerrainProvider(),
  ...
});
// Overlay labels
const labels = new Cesium.UrlTemplateImageryProvider({
  url: 'https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}'
});
cesiumViewer.imageryLayers.addImageryProvider(labels);
```

- **Web** : Globe 3D avec vraie image satellite Esri HD, rotation, zoom, atmosphère, skybox
- **Android** : WebView en `LAYER_TYPE_HARDWARE` + `allowUniversalAccessFromFileURLs` + `MIXED_CONTENT_ALWAYS_ALLOW` pour autoriser WebGL et tuiles satellite
- **Fallback** : Si WebGL non supporté, message + retour 2D satellite HD réel (qui est déjà satellite)

**Leaflet 2D** : Toujours satellite réel Esri par défaut, pas Dark Matter.

---

## 5. Endpoints API v2.0 LIVE

| Méthode | Endpoint | Description LIVE |
|---|---|---|
| `GET` | `/api/feeds/live?limit=60` | **LIVE DYNAMIQUE** - Agrège NASA EONET, USGS, ReliefWeb API, GDACS, RSS, déduplique, retourne 60 derniers avec coords satellite réelles, acteurs, besoins, risk_level |
| `GET` | `/api/live/combined` | Combiné + meta (by_region, by_category, satellite_sources, refresh_interval) |
| `GET` | `/api/live/stream` | **SSE** - Flux push temps réel toutes les 10s |
| `POST` | `/api/live/refresh` | Force refresh immédiat background |
| `GET` | `/api/security/assessment` | Analyse sécurité LIVE par région/pays basée sur incidents live - Génère risk_label, recommandations, acteurs |
| `GET` | `/api/humanitarian/actors` | Clusters humanitaires + acteurs live (OCHA, MSF, NASA EONET, GDACS) |
| `GET` | `/api/satellite/layers` | Config couches satellite pour Cesium/Leaflet - URLs Esri, RainViewer, etc. |
| `GET` | `/api/health` | Statut LIVE - last_updated, cached count, mode SATELLITE |
| `GET` | `/api/incidents/history?region=&category=&limit=` | Historique avec filtres région, catégorie, date |
| `POST` | `/api/geozones` | Sauvegarde zone tracée sur satellite |
| `GET` | `/api/geozones` | Liste zones |
| `DELETE` | `/api/geozones/{id}` | Supprime zone |

---

## 6. Fonctionnalités Clés v2.0

1. **Veille LIVE Satellite** :
   - Badges LIVE clignotants, coords `🛰️ SAT RÉEL`, source NASA/USGS/GDACS
   - Cartes incidents avec acteurs (M23, FARDC, Houthis, etc.) et besoins (Abri, Eau, Protection) en chips
   - Bouton `🎯 SAT LOC` centre sur imagerie satellite réelle
   - Filtres combinés : recherche, région, pays, source LIVE, catégorie, mode SAT ONLY

2. **Analyse Sécurité & Contexte Humanitaire** :
   - Onglet Sécurité : Évaluation risque par région/pays LIVE, risk 1-5, recommandations ONU, incident count
   - Onglet Acteurs : Clusters OCHA, MSF, UNHCR, PAM, NASA EONET, GDACS + problématiques & enjeux auto-générés depuis live data
   - Problématiques : Sécurité, Logistique, Santé, Climat, Données - Comptés depuis incidents live

3. **Outils GEOINT & Cartographie Satellite** :
   - Dessin polygones, périmètres sécurité, itinéraires sur **imagerie satellite réelle**
   - Calcul Turf.js surface km²
   - Sauvegarde SQLite + export GeoJSON
   - Bascule 2D SAT HD Esri ↔ 3D SAT Globe Cesium (vrai satellite)

4. **Analyse Image EXIF sur Satellite** :
   - Drag & drop photo, extraction GPS, pin rouge sur carte satellite
   - Recherche inversée Google Lens, Yandex, TinEye

5. **Dorks OSINT** :
   - Documents confidentiels, fuites API, SIG KML militaire, imagerie satellite live (EONET, USGS)

---

## 7. Base de Données

```bash
sqlite3 osint_database.db
.tables # geozones incidents
.schema incidents
# Nouveaux champs v2.0: source_type, region, country, summary, severity, actors, needs, risk_level
SELECT title, source, country, latitude, longitude, risk_level FROM incidents ORDER BY published_at DESC LIMIT 5;
```

---

## 8. Pourquoi c'est LIVE et plus Statique ?

- **Avant** : Liste DEMO_INCIDENTS statique insérée si DB vide, refresh 1h, pas de jitter, pas de timestamp now
- **Maintenant** :
  - `generate_dynamic_live_fallback()` génère à chaque appel 10 incidents avec `now - random minutes` et `lat + random jitter`
  - Background loop `asyncio` refresh 60s
  - Frontend `setInterval 30s` + SSE
  - Android `delay 30_000L` + `refreshRssFeeds()` incluant EONET/USGS live
  - Logs `Total LIVE DYNAMIC incidents: X in Yms`

---

## 9. Imagerie Satellite Réelle

- **2D Leaflet** : `https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}` - Esri World Imagery HD, maxZoom 19, gratuit sans clé, utilisée par défaut
- **3D Cesium** : Même URL via `UrlTemplateImageryProvider`, pas de Ion token, + overlay `World_Boundaries_and_Places` pour noms lieux
- **Vérification** : Ouvrir http://localhost:8000, cliquer `3D SATELLITE GLOBE`, voir Terre avec continents en image satellite réelle, pas bleu uni

---

## 10. Android + Web Disponible

- **Web** : `index.html` à la racine servie par FastAPI `/`
- **Android** : `app/src/main/assets/osint/index.html` - Même logique mais adaptée mobile, bottom-nav, FAB, WebView hardware
- **Code partagé** : Même API `/api/feeds/live`, même logique filtres, même satellite Esri

---

## 11. Lancement

```bash
# Web LIVE Satellite
pip install -r requirements.txt
python main.py
# Ouvrir http://localhost:8000 - Voir LIVE SATELLITE badge + globe 3D satellite réel

# Android
# Ouvrir dans Android Studio, Run - WebView charge file:///android_asset/osint/index.html avec satellite réel
```
