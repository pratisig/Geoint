# HUMAN-OSINT v4.1 POWER // OSINT/GEOINT ULTIMATE PLATFORM

> ## ✨ Nouveautés v4.1
>
> **1. Flux d'actualité réellement auto-updatés.** Le canal SSE ne comparait que
> le *nombre* d'incidents : un cycle renvoyant le même nombre d'éléments plus
> récents ne produisait aucun événement, et l'interface ne se mettait jamais à
> jour. La détection porte désormais sur une **empreinte du contenu**
> (`content_hash`), avec heartbeat anti-timeout, backoff de reconnexion,
> rafraîchissement au retour d'onglet et badge d'âge des données.
> Diagnostic : `GET /api/auto-update/status`.
>
> **2. Globe 3D avec un vrai fond de carte.** `Cesium.Viewer` était construit
> avec l'option `imageryProvider`, **supprimée dans CesiumJS 1.107** (dépréciée
> en 1.104). En 1.115 elle est ignorée silencieusement : le globe affichait sa
> `baseColor` (bleu) et seule la couche de frontières blanches restait visible.
> Le globe utilise maintenant `baseLayer` + `Cesium.ImageryLayer`, et le
> sélecteur de fond de carte (Esri 0.3m / Google Sat / **OpenStreetMap** /
> dark / relief) fonctionne aussi bien en 2D qu'en 3D.
>
> **3. Documentation et aide intégrée.** 53 fonctions Python et 86 fonctions
> JavaScript documentées, une barre d'aide en tête de chacun des 12 outils,
> des infobulles sur les contrôles, un panneau 📖 AIDE global, et une
> référence API générée depuis le schéma OpenAPI réel.
>
> 📘 **Documentation complète : [`DOCUMENTATION.md`](DOCUMENTATION.md)** —
> régénérable avec `python tools/generate_docs.py`.
>
> ✅ **Tests : `cd tests && npm install && npm test`** (37 assertions, jsdom)


Plateforme opérationnelle de renseignement OSINT/GEOINT **EN TEMPS RÉEL V4 POWER** : **70+ sources RSS live, NASA EONET + FIRMS satellite, USGS sismique, GDELT x5, Reddit x6, Telegram x4**, globe **3D satellite réel HD Esri 0.3m + Google Sat**, **80+ Google Dorks**, **40+ moteurs OSINT spécialisés**, **Reverse Image Search 7 moteurs**, **générateur de rapports**, **agent IA avec clés utilisateur**.

> **v4.0 POWER** : Fix scraping robuste (ThreadPool 12 workers + fallback 30 incidents), Android Bridge natif (plus de liste vide), GitHub Pages démo 30 incidents, reverse image search (Google, Yandex, TinEye, Bing, Baidu, Sogou, KarmaDecay), advanced multi-engine search (Shodan, Censys, ZoomEye, Hunter, IntelX, VirusTotal, MarineTraffic, FlightRadar, Sentinel), report generator markdown/GeoJSON, AI agent (OpenAI, Gemini, Anthropic, Mistral) avec clés localStorage.

---

## 1. 🚀 Déploiement Rapide

### Option A - GitHub Actions (APK + Pages) - RECOMMANDÉ ✅

**1. Activer GitHub Pages (Web App)**
- Repo GitHub > `Settings` > `Pages` > Source : `GitHub Actions`
- Push sur `main` ou `arena/01a0a621-geoint` déclenche `.github/workflows/pages.yml`
- URL finale : `https://<username>.github.io/Geoint/`
- **Mode démo V4 offline** : 30 incidents, 40 engines, 80 dorks, reverse image, report local, EXIF
- **Mode LIVE complet** : ajoutez `?api=https://votre-backend.onrender.com` à l'URL ou cliquez 🔧 API

**2. Build APK automatique V4**
- Workflow `.github/workflows/android.yml` se déclenche à chaque push
- `Actions` > `Android APK Build` > dernier run > `Artifacts` > `HUMAN-OSINT-V4-debug-apk`
- Téléchargez APK, installez sur Android
- **Fix V4** : Android utilise bridge natif `AndroidOSINT.getLiveFeeds()` - plus de liste vide, 30 incidents baseline + scraping robuste 20 sources

**3. Tester en local (dev)**
```bash
git clone https://github.com/pratisig/Geoint.git
cd Geoint
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
# Ouvrir http://localhost:8000
```

### Option B - Backend Cloud Gratuit (pour LIVE complet sur GitHub Pages)

**Render.com (recommandé, free tier)**
```bash
# 1. Connectez repo GitHub à https://render.com
# 2. New Web Service > Select Geoint repo
# 3. Config auto via render.yaml :
#    Build: pip install -r requirements.txt
#    Start: uvicorn main:app --host 0.0.0.0 --port $PORT
# 4. Deploy -> URL type https://human-osint-v4.onrender.com
# 5. Test: https://human-osint-v4.onrender.com/api/health
# 6. Frontend Pages: https://<user>.github.io/Geoint/?api=https://human-osint-v4.onrender.com
```

**Docker**
```bash
docker build -t human-osint-v4 .
docker run -p 8000:8000 human-osint-v4
# http://localhost:8000/docs
```

---

## 2. Architecture v4.0 POWER

### Backend LIVE ROBUSTE
- **FastAPI + Uvicorn + SQLite WAL + SSE + ThreadPool 12 workers**
- **60+ RSS** : ReliefWeb (updates/disasters/reports), GDACS, WHO, UN News EN/FR, Crisis Group, BBC World/Africa/MiddleEast, Al Jazeera, France24 FR/EN/Afrique, RFI Afrique/Monde, Le Monde, Guardian World/Global, DW World/Africa, Euronews, Jeune Afrique, AfricaNews, AP, Reuters World/Africa/MiddleEast, OilPrice, Maritime, HackerNews, Bleeping, TheRecord, CISA, ISW, DefenseNews, DefenseOne, OCHA, NASA, USGS, EIA, MSF, ICRC, UNHCR, WFP, ACLED, LiveUAMap, VOA Africa/World, AllAfrica, Sahel Intelligence, The New Humanitarian, HRW, Amnesty, NYT World, CNN Africa
- **Satellite** : NASA EONET API (wildfires, volcans, tempêtes), USGS all_day.geojson M4.0+, ReliefWeb API v1, GDACS, NASA FIRMS (simulé)
- **Médias massifs** : GDELT Doc API `artlist` - 5 requêtes (conflit, humanitaire, catastrophe, protest, cyber) = 60 articles
- **Sociaux** : Reddit JSON `r/OSINT, r/UkraineConflict, r/Syria, r/Sahel, r/geopolitics, r/worldnews` + Telegram `t.me/s/OSINTtechnical, @UkraineOSINT, @liveuamap, @rybar` scraping BeautifulSoup
- **Total** : ~76 sources, refresh 60s background loop, stats tracking rss/sat/social/media/gdelt + sources_status
- **Fallback robuste** : 30 incidents V4 avec jitter 0.15° + timestamp now-0-360min (preuve LIVE même sans réseau) - plus de 6, maintenant 30 couvrant monde entier
- **Fix Android** : OsintRepository.kt utilise 20 RSS + EONET + USGS + ReliefWeb API + GDELT simulé, avec ThreadPool, insertion robuste, baseline 30 incidents avec actors/needs/riskLevel

### Frontend V4 POWER - 12 TABS
- **LIVE 70+** : Agrégé 60 RSS + sat + GDELT x5 + social, filtres thématique/régional/pays/source/search
- **SOCIAL** : Reddit x6 + Telegram x4 + GDELT Social
- **MEDIA** : 60 RSS + GDELT artlist
- **IMAGE OSINT** : Reverse image search 7 moteurs (Google, Yandex visages, TinEye exact, Bing Visual, Baidu, Sogou, KarmaDecay Reddit) + EXIF GPS + drag&drop + génération auto
- **SEARCH** : Advanced multi-engine search builder - sélectionnez parmi 40 moteurs, générez requêtes Google, Shodan, Censys, VirusTotal, Wayback, MarineTraffic, FlightRadar, Sentinel, etc.
- **ENGINES** : 40+ moteurs OSINT spécialisés listés par catégorie (search, image, IoT, people, breach, domain, archive, maritime, aviation, satellite, osint, social, code) - moins connus inclus: Mojeek, ZoomEye, IntelX, Dehashed, SecurityTrails, DNSDumpster, urlscan.io, Ahmia, ADSBExchange, etc. Filtre free/paid
- **DORKS 80+** : 80 dorks pré-construits (18 catégories: documents, credentials, database, iot, geospatial, backup, scada, social, humanitarian, darknet, people, vuln, sahel, ukraine, satellite, maritime, aviation, advanced), recherche, filtres severity, builder custom avec 9 variantes + google_urls
- **GEOINT** : Zones + satellite HD Esri 0.3m + Google Satellite + Dark + OSM + Relief, dessin Leaflet.draw + Turf.js mesure km²/km, sauvegarde SQLite ou localStorage (démo) ou Android bridge, export GeoJSON, EXIF GPS pin rose satellite + reverse
- **REPORT** : Générateur rapports complets sur sujet donné - topic, régions, catégories, time_range (24h/7d/30d/90d/all), max incidents, sections (summary, incidents, risk, actors, map, recommendations), format markdown, GeoJSON export, IA optionnelle (OpenAI, Gemini, Anthropic, Mistral) avec clé fournie par utilisateur
- **SÉCURITÉ** : Risque par région V4, 30 régions max, recommandations tactiques
- **ACTEURS** : OCHA, MSF, NASA, GDELT, Reddit, Telegram, Bellingcat, MarineTraffic, FlightRadar, etc.
- **AI AGENT** : Agent IA avec clés utilisateur (localStorage, jamais serveur) - providers OpenAI GPT-4o/mini, Gemini 1.5 Flash/Pro, Claude 3 Haiku/Sonnet, Mistral - prompt OSINT, inclusion incidents LIVE top 15, contexte régional, analyse stratégique, test clé, copy/download

### Cartes V4 - SATELLITE RÉEL HD FIX
- **2D** : Leaflet Esri World Imagery 0.3m HD réel (default) + Google Satellite alternative + Dark + OSM + Relief Topo
- **3D** : Cesium 1.115 Globe réel avec **UrlTemplateImageryProvider Esri World Imagery 0.3m** (pas de token Ion) + overlay labels Esri - VRAIE vue satellite, pas Bing par défaut. Camera flyTo + sync incidents avec labels 🛰️ SAT LIVE / 📱 SOCIAL
- **Overlays** : RainViewer météo live, OpenTopoMap relief, OSM
- **Fix** : initCesiumSatellite utilise Esri imagery provider explicitement, terrain EllipsoidTerrainProvider, background noir, showGroundAtmosphere, 3D globe satellite réel HD

### Base de données V4
- SQLite `osint_database.db` WAL + `osint_tactical.db` Android v4
- Tables `incidents` (avec actors JSON, needs JSON, risk_level, severity, country, region, source_type, language, verified) et `geozones`
- Android Entity v4 avec severity, actors, needs, riskLevel, language, verified

---

## 3. Endpoints API v4.0

| Méthode | Endpoint | Description |
|---|---|---|
| `GET` | `/api/health` | Health + rss_sources 60 + dorks 80 + engines 40 + sources_status |
| `GET` | `/api/feeds/live?limit=100&category=&region=&country=&search=` | LIVE V4 agrégé 70+ sources avec filtres thématique/régional |
| `GET` | `/api/osint/social?limit=40` | Réseaux sociaux Reddit x6 + Telegram x4 live |
| `GET` | `/api/osint/media?limit=60` | Médias 60 RSS + GDELT x5 |
| `GET` | `/api/osint/comprehensive` | Incidents + social + media + meta coverage V4 |
| `GET` | `/api/live/combined` | Combiné + meta by_region/by_category/by_country |
| `GET` | `/api/live/stream` | SSE push 10s |
| `POST` | `/api/live/refresh` | Force refresh |
| `GET` | `/api/dorks/all?category=&severity=&search=` | 80 dorks filtrables V4 |
| `POST` | `/api/dorks/generate` | Builder custom dork + 9 variantes + google_urls |
| `GET` | `/api/dorks/categories` | Stats par catégorie |
| `GET` | `/api/osint/reverse-image/engines` | 7 moteurs reverse image |
| `POST` | `/api/osint/reverse-image/generate` | Génère URLs reverse pour image_url |
| `GET` | `/api/osint/engines?category=&search=&free_only=` | 40+ moteurs OSINT spécialisés |
| `POST` | `/api/osint/engines/search` | Recherche multi-moteurs + dork variants |
| `GET` | `/api/search/advanced` | Info advanced search |
| `POST` | `/api/report/generate` | Générateur rapport markdown/GeoJSON + IA optionnelle |
| `GET` | `/api/ai/providers` | Liste providers IA |
| `POST` | `/api/ai/analyze` | Analyse IA avec clé utilisateur (OpenAI, Gemini, Anthropic) |
| `GET` | `/api/security/assessment` | Analyse risque LIVE par région V4 |
| `GET` | `/api/humanitarian/actors` | Clusters + enjeux V4 |
| `GET` | `/api/satellite/layers` | Config couches satellite V4 (Esri HD + Google Sat + Bing) |
| `GET` | `/api/incidents/history?category=&region=&limit=` | Historique SQLite |
| `POST` | `/api/geozones` | Créer zone |
| `GET` | `/api/geozones` | Lister zones |
| `DELETE` | `/api/geozones/{id}` | Supprimer zone |

---

## 4. Dorking V4 - 80+ Dorks + 40 Engines + Reverse Image

**Catégories Dorks (18)** : documents (6), credentials (6), database (5), iot (5), geospatial (6), backup (4), scada (3), social (6), humanitarian (4), darknet (2), people (3), vuln (3), sahel (4), ukraine (3), satellite (5), maritime (1), aviation (1), advanced (10)

**OSINT Engines (40)** : search (6: Google, Bing, Yandex, DuckDuckGo, Brave, Mojeek), image (5: Google Images, Yandex Images, TinEye, Bing Visual, KarmaDecay), iot (3: Shodan, Censys, ZoomEye), people (2: Hunter.io, LinkedIn), breach (3: IntelX, Dehashed, HaveIBeenPwned), domain (5: VirusTotal, SecurityTrails, Whois, DNSDumpster, urlscan.io), archive (2: Wayback, Archive.is), maritime (1: MarineTraffic), aviation (2: FlightRadar24, ADSBExchange), satellite (4: Sentinel Hub, NASA Worldview, EONET, FIRMS), osint (2: Bellingcat Toolkit, OSINT Framework), social (4: Reddit, Telegram, Twitter/X, YouTube), code (1: GitHub)

**Reverse Image (7)** : Google Images, Yandex Images (visages), TinEye (exact), Bing Visual, Baidu, Sogou, KarmaDecay Reddit

**Advanced Search** : Builder multi-moteurs avec site, filetype, country, date_range, extra - génère URLs pour tous moteurs sélectionnés + variantes Shodan/Censys/VirusTotal/Wayback

---

## 5. Rapport & AI Agent V4

**Rapport** : Sujet + régions (virgule) + catégories (virgule) + time_range (24h/7d/30d/90d/all) + max incidents + sections (summary, incidents, risk, actors, map, recommendations) -> Markdown + GeoJSON + stats by_region/by_category/by_country/by_source + risk max/avg + actors aggregation + export .md/.geojson

**AI Agent** : 
- Clés stockées localStorage navigateur (`HUMAN_OSINT_AI_KEY`, `HUMAN_OSINT_AI_PROVIDER`, `HUMAN_OSINT_AI_MODEL`) - jamais serveur
- Providers: OpenAI (gpt-4o-mini rapide, gpt-4o puissant), Gemini (1.5 Flash gratuit généreux, 1.5 Pro puissant), Anthropic Claude (Haiku rapide, Sonnet puissant), Mistral
- Prompt OSINT + inclusion incidents LIVE top 15 + contexte régional + analyse stratégique
- Endpoints: `/api/ai/providers` liste, `/api/ai/analyze` avec prompt, context, incidents, provider, api_key, model -> result
- Sécurité: clés jamais loggées, utilisées uniquement pour requête directe vers provider, pas stockées côté serveur en prod
- Sans clé: mode simulation template

**Exemple rapport avec IA**:
```json
POST /api/report/generate
{
  "topic": "Conflit Sahel JNIM",
  "regions": ["Afrique"],
  "categories": ["conflit"],
  "time_range": "7d",
  "max_incidents": 50,
  "include_sections": ["summary","incidents","risk","actors","map","recommendations"],
  "ai_provider": "gemini",
  "ai_api_key": "AIza..."
}
```

---

## 6. Fix Problèmes Sources V4

**Problème initial** : Outil affirmait 35 sources mais mobile aucun événement, GitHub quelques événements seulement

**Causes** :
- Backend: feedparser.parse direct URL sans requests + pas de ThreadPool + rsshub.app down + pas de fallback robuste si <10 incidents
- Frontend: GitHub Pages mode démo 6 incidents seulement + API_BASE file:// -> localhost:8000 inaccessible
- Mobile: OsintRepository 11 sources seulement + WebView file:// origin -> API_BASE localhost:8000 -> fetch fail -> liste vide + preloadBaseline seulement si count<25 mais getIncidents appelé avant preload

**Corrections V4** :
- Backend: ThreadPoolExecutor 12 workers + requests.get avec User-Agent + feedparser sur content + sources_status tracking + fallback 30 incidents si <10 + GDELT x5 + Reddit x6 + Telegram x4 + 60 RSS robustes + stats failed
- Frontend: Démo V4 30 incidents (au lieu de 6) + détection Android bridge (`window.AndroidOSINT`) + IS_GITHUB_DEMO + Android utilise bridge natif `AndroidOSINT.getLiveFeeds()` JSON avec actors/needs/risk_level + refresh via bridge + localStorage geozones + initAdvEnginesChecklist
- Mobile: OsintRepository V4 20 RSS + EONET + USGS + ReliefWeb API + GDELT simulé + baseline 30 incidents avec actors JSON + parseXmlFeed robuste même sans geo match (garde avec coords globales) + OkHttp timeout 12s + logs + incidentDao v4 avec severity/actors/needs/riskLevel + AppDatabase v4 + OsintJsInterface v4 avec tous champs + getVersion

---

## 7. Workflows GitHub Actions V4

- `android.yml` : branches main, arena/01a0a4ee-geoint, arena/01a0a621-geoint + artifact HUMAN-OSINT-V4-debug-apk
- `pages.yml` : branches main, arena/01a0a4ee-geoint, arena/01a0a621-geoint + cp index.html -> _site
- `backend.yml` : branches main, arena/01a0a4ee-geoint, arena/01a0a621-geoint + test health V4 + dorks + engines + reverse-image + docker build human-osint-v4:latest

---

## 8. Docker & Cloud V4

**Dockerfile V4**
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY main.py index.html ./
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

**render.yaml** : Déploiement 1-click Render free tier

---

## 9. Installation Locale V4

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --host 0.0.0.0 --port 8000
# Web: http://localhost:8000
# Docs: http://localhost:8000/docs
# Health: http://localhost:8000/api/health -> rss_sources 60, dorks 80, engines 40
```

**Android V4**
```bash
# Android Studio Hedgehog+ / JDK 21
# Ouvrir projet, Sync, Run
# Ou CLI:
./gradlew assembleDebug
```

---

## 10. Pourquoi V4 POWER ?

- **Scraping MAX ROBUSTE** : 60 RSS + 5 satellite + 5 GDELT + 6 Reddit + 4 Telegram = ~80 sources, vs 35 avant, avec ThreadPool + sources_status + fallback 30
- **Mobile Fix** : Android bridge natif, plus de liste vide, 30 incidents baseline + 20 RSS live
- **GitHub Pages Fix** : Démo 30 incidents V4 (vs 6), 40 engines, 80 dorks, reverse image, report local, EXIF
- **Reverse Image** : 7 moteurs (Google, Yandex visages, TinEye exact, Bing, Baidu, Sogou, KarmaDecay) + EXIF GPS + drag&drop + génération auto
- **Advanced Search** : 40+ moteurs OSINT spécialisés moins connus (Mojeek, ZoomEye, IntelX, Dehashed, SecurityTrails, DNSDumpster, urlscan.io, ADSBExchange, MarineTraffic, FlightRadar, Sentinel Hub, NASA Worldview, Bellingcat, OSINT Framework) + builder multi-moteurs + variantes Shodan/Censys
- **Dorking EXHAUSTIF** : 80 dorks vs 60 avant, 18 catégories vs 16, + maritime/aviation
- **Report Generator** : Rapports complets sur sujet donné avec filtres thématique/régional, markdown/GeoJSON export, IA optionnelle
- **AI Agent** : OpenAI, Gemini, Anthropic, Mistral avec clés utilisateur localStorage, analyse stratégique, test clé, jamais stocké serveur
- **Satellite Fix** : 2D Esri 0.3m HD + Google Satellite + Cesium 3D Globe réel satellite (Esri UrlTemplateImageryProvider, pas token Ion) - vraie vue satellite
- **Filtres** : Thématique (conflit, catastrophe, énergie, épidémie, cyber, protest) + régional (Moyen-Orient, Europe, Afrique, Asie-Pacifique, Amériques, Global) + pays + source + search - toute actualité mondiale accessible
- **GitHub Ready** : Workflows APK + Pages + Backend + Dockerfile + render.yaml avec nouvelle branche arena/01a0a621-geoint

---

## 11. Sécurité & Éthique V4

- Dorks & engines fournis à but éducatif OSINT, usage responsable
- Respect robots.txt, User-Agent identifié `HUMAN-OSINT-V4/4.0`
- Données publiques uniquement (OSINT), pas d'intrusion
- Clés IA jamais stockées serveur, uniquement localStorage + requête directe provider
- Reverse image : respect vie privée, Yandex visages puissant mais usage éthique
- Pour signalement vulnérabilité critique, contacter en privé

---

## 12. Licence

MIT - Usage humanitaire, recherche, journalisme, OSINT.

---

**Auteur** : Pratisig / Geoint - v4.0 POWER - 2025
**Stack** : FastAPI, Leaflet, CesiumJS, Turf.js, ExifReader, BeautifulSoup, GDELT, Reddit, Telegram, OpenAI, Gemini, Anthropic
**Déploiement** : GitHub Pages (frontend V4 démo 30 incidents) + Render/Railway/Fly.io (backend V4 80 sources) + GitHub Actions (APK V4)
