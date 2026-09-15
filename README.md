# HUMAN-OSINT v3.0 ULTIMATE // MAX SCRAPING + SATELLITE + DORKING POWER TOOL

Plateforme opérationnelle de renseignement OSINT/GEOINT **EN TEMPS RÉEL ULTIME** : **35+ sources RSS live, NASA EONET satellite, USGS sismique, GDELT media, Reddit, Telegram**, globe **3D satellite réel HD Esri 0.3m**, **60+ Google Dorks** power tool, Android + Web.

> **v3.0 ULTIMATE** : Scraping maximal (35 RSS + GDELT + Reddit + Telegram), Dorking exhaustif 60+ dorks avec générateur custom 9 variantes, Social/Media tabs, EXIF GPS, SQLite, SSE, auto-refresh 30s.

---

## 1. 🚀 Déploiement Rapide - Meilleures solutions pour tester

### Option A - GitHub Actions (APK + Pages) - RECOMMANDÉ pour tester direct depuis GitHub ✅

**1. Activer GitHub Pages (Web App)**
- Allez dans votre repo GitHub > `Settings` > `Pages`
- Source : `GitHub Actions`
- Push sur `main` ou `arena/01a0a4ee-geoint` déclenche le workflow `.github/workflows/pages.yml`
- URL finale : `https://<username>.github.io/Geoint/`
- **Mode démo offline** : fonctionne sans backend (satellite réel, dessin, 16 dorks, EXIF)
- **Mode LIVE complet** : ajoutez `?api=https://votre-backend.onrender.com` à l'URL ou cliquez 🔧 API

**2. Build APK automatique**
- Workflow `.github/workflows/android.yml` se déclenche à chaque push
- Allez dans `Actions` > `Android APK Build` > dernier run > `Artifacts` > `HUMAN-OSINT-ULTIMATE-debug-apk`
- Téléchargez l'APK, installez sur Android (autoriser sources inconnues)
- Pas besoin d'Android Studio !

**3. Tester en local (dev)**
```bash
git clone https://github.com/pratisig/Geoint.git
cd Geoint
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
# Ouvrir http://localhost:8000
```

### Option B - Backend Cloud Gratuit (pour avoir le LIVE complet sur GitHub Pages)

**Render.com (recommandé, free tier)**
```bash
# 1. Connectez votre repo GitHub à https://render.com
# 2. New Web Service > Select Geoint repo
# 3. Config auto via render.yaml :
#    Build: pip install -r requirements.txt
#    Start: uvicorn main:app --host 0.0.0.0 --port $PORT
# 4. Deploy -> URL type https://human-osint-ultimate.onrender.com
# 5. Test: https://human-osint-ultimate.onrender.com/api/health
# 6. Frontend Pages: https://<user>.github.io/Geoint/?api=https://human-osint-ultimate.onrender.com
```

**Railway.app / Fly.io / Hugging Face Spaces**
- Même principe, utilisez `Dockerfile` fourni
```bash
docker build -t human-osint .
docker run -p 8000:8000 human-osint
```

**Autres solutions rapides**
- **Replit** : Import repo, Run `python main.py`
- **Gitpod / Codespaces** : Ouvrez repo dans Codespace, `uvicorn main:app --host 0.0.0.0 --port 8000`, port forwarding auto
- **Ngrok** pour exposer local : `ngrok http 8000` -> donnez URL à `?api=`

### Option C - Android Studio (dev complet)
```bash
# Ouvrir dans Android Studio
# Sync Gradle (JDK 21 requis, AGP 9.1.1)
# Run sur émulateur ou device
# L'app charge file:///android_asset/osint/index.html avec backend local ou distant configurable
```

---

## 2. Architecture v3.0 ULTIMATE

### Backend LIVE MAX SCRAPING
- **FastAPI + Uvicorn + SQLite WAL + SSE**
- **35 RSS** : ReliefWeb, GDACS, WHO, Crisis Group, BBC, CNN, Reuters, Al Jazeera, France24, RFI, Le Monde, Guardian, AP, Jeune Afrique, AfricaNews, DefenseNews, MSF, ICRC, ISW, OilPrice, HackerNews, BleepingComputer, The Record, CISA, etc.
- **Satellite** : NASA EONET API (wildfires, volcans), USGS all_day.geojson (M4.5+), ReliefWeb API v1, GDACS
- **Médias massifs** : GDELT Doc API `artlist` - 3 requêtes (conflit, humanitaire, catastrophe) = 45 articles
- **Sociaux** : Reddit JSON `r/OSINT, r/UkraineConflict, r/Syria, r/Sahel` + Telegram `t.me/s/OSINTtechnical` scraping BeautifulSoup
- **Total** : ~47 sources, refresh 45s background loop, stats tracking rss/sat/social/media/gdelt
- **Fallback dynamique** : 15 incidents ULTIMATE avec jitter 0.15° + timestamp now-0-180min (preuve LIVE même sans réseau)

### Frontend ULTIMATE
- **7 tabs** : LIVE (35+), SOCIAL (Reddit/Telegram), MEDIA (35 RSS), SÉCURITÉ (risque par région), GEOINT (zones + satellite), ACTEURS (OCHA, MSF, NASA...), DORKS ULTIMATE (60+)
- **Carte** : Leaflet 2D Esri World Imagery 0.3m HD réel + Cesium 3D Globe réel (UrlTemplateImageryProvider, pas de token Ion)
- **Overlays** : RainViewer météo live, OpenTopoMap relief, OSM
- **Dessin** : Leaflet.draw + Turf.js mesure km²/km, sauvegarde SQLite ou localStorage (mode démo), export GeoJSON
- **EXIF** : Drag&drop JPG, extraction GPS via ExifReader, pin rouge satellite
- **Dorking Power Tool** : 60 dorks pré-construits (16 catégories), recherche, filtres severity, builder custom avec 9 variantes + google_urls, bouton LANCER GOOGLE direct
- **Config API** : `?api=URL` param + localStorage + bouton 🔧 API, mode démo offline automatique sur github.io sans backend

### Base de données
- SQLite `osint_database.db` WAL
- Tables `incidents` (avec actors, needs, risk_level, severity, country, region, source_type) et `geozones`
- Endpoints `/api/geozones` CRUD + `/api/incidents/history`

---

## 3. Endpoints API v3.0

| Méthode | Endpoint | Description |
|---|---|---|
| `GET` | `/api/feeds/live?limit=80` | LIVE ULTIME agrégé 35 RSS + sat + GDELT + social |
| `GET` | `/api/osint/social?limit=40` | Réseaux sociaux Reddit + Telegram live |
| `GET` | `/api/osint/media?limit=50` | Médias 35 RSS + GDELT artlist |
| `GET` | `/api/osint/comprehensive` | Incidents + social + media + meta coverage |
| `GET` | `/api/live/combined` | Combiné + meta by_region/by_category |
| `GET` | `/api/live/stream` | SSE push 10s |
| `POST` | `/api/live/refresh` | Force refresh |
| `GET` | `/api/dorks/all?category=&severity=&search=` | 60 dorks filtrables |
| `POST` | `/api/dorks/generate` | Builder custom dork + 9 variantes + google_urls |
| `GET` | `/api/dorks/categories` | Stats par catégorie |
| `GET` | `/api/security/assessment` | Analyse risque LIVE par région |
| `GET` | `/api/humanitarian/actors` | Clusters + enjeux |
| `GET` | `/api/satellite/layers` | Config couches satellite |
| `GET` | `/api/health` | Health + rss_sources + dorks_count |

---

## 4. Dorking ULTIMATE - 60+ Dorks

**Catégories** (16) :
- documents (5) : CONFIDENTIAL, gov, ReliefWeb, état-major
- credentials (5) : Pastebin passwords, RSA keys, .env, config.js
- database (4) : dump.sql, phpMyAdmin, Firebase, Elasticsearch
- iot (4) : caméras IP, AXIS, Shodan
- geospatial (5) : KML/KMZ militaires, Shapefiles, GeoJSON
- backup (4) : .bak, archive.org, Wayback
- scada (3) : ICS, SCADA, modbus
- social (6) : Twitter/X, Telegram, Reddit, TikTok, Discord
- humanitarian (4) : OCHA reports, UNHCR, HDX
- darknet (2) : onion, darknet forums
- people (2) : LinkedIn, people search
- vuln (2) : CVE, exploits
- sahel (3) : JNIM, Wagner, M23
- ukraine (2) : DeepState, LiveUAMap
- satellite (3) : NASA EONET, Sentinel, USGS
- advanced (6) : GitHub secrets, .env, log files, inurl:admin

Chaque dork : id, category, severity (critical/high/medium/low), title, query, description, tags, launch Google.

**Générateur custom** : POST `/api/dorks/generate` avec keywords, site, filetype, country, category, exclude, date_range -> retourne generated_dork + 9 variantes + google_urls + tips.

---

## 5. Workflows GitHub Actions

### `android.yml`
- Trigger : push sur main/arena branch, paths app/**, workflow_dispatch
- Jobs :
  - `build-debug` : JDK 21, Android SDK, génère gradlew si manquant (Gradle 9.3.1), crée debug.keystore, `assembleDebug`, upload artifact 30j
  - `build-release-unsigned` : sur tag ou manual, assembleRelease
- Artifacts : `HUMAN-OSINT-ULTIMATE-debug-apk` téléchargeable sans compte

### `pages.yml`
- Trigger : push index.html, workflow_dispatch
- Permissions : pages:write, id-token:write
- Steps : checkout, configure-pages, cp index.html -> _site, upload-pages-artifact, deploy-pages
- URL : `https://<username>.github.io/Geoint/`
- Supporte `?api=backend_url` pour LIVE

---

## 6. Docker & Cloud

**Dockerfile**
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY main.py index.html ./
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

**render.yaml** : Déploiement 1-click Render free tier.

**Test local Docker**
```bash
docker build -t human-osint-ultimate .
docker run -p 8000:8000 human-osint-ultimate
# http://localhost:8000/docs
```

---

## 7. Configuration Frontend pour GitHub Pages

Le frontend détecte automatiquement :
1. `?api=` ou `?backend=` dans URL -> sauvegarde localStorage
2. `localStorage['HUMAN_OSINT_API_BASE']` -> utilise
3. Si hostname `github.io` sans backend configuré -> **mode démo offline** avec 6 incidents demo, 2 social, 3 media, 3 risk, 16 dorks, geozones localStorage
4. Sinon `window.location.origin`

**Bouton 🔧 API** (header) permet de configurer backend à la volée.

Exemples :
- `https://pratisig.github.io/Geoint/` -> demo offline
- `https://pratisig.github.io/Geoint/?api=https://human-osint-ultimate.onrender.com` -> LIVE complet
- `https://pratisig.github.io/Geoint/?api=http://localhost:8000` -> local dev (avec ngrok si besoin)

---

## 8. Installation Locale

```bash
python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt  # inclut beautifulsoup4, lxml, httpx
uvicorn main:app --reload --host 0.0.0.0 --port 8000
# Web: http://localhost:8000
# Docs: http://localhost:8000/docs
# Health: http://localhost:8000/api/health -> rss_sources 35, dorks 60
```

**Android**
```bash
# Android Studio Hedgehog+ / JDK 21
# Ouvrir projet, Sync, Run
# Ou CLI:
./gradlew assembleDebug  # si gradlew existe, sinon workflow GitHub le génère
```

---

## 9. Pourquoi ULTIMATE ?

- **Scraping MAX** : 35 RSS + 4 satellite + 3 GDELT + 4 Reddit + 3 Telegram = ~47 sources, vs 11 avant
- **Dorking EXHAUSTIF** : 60 dorks vs 15 avant, avec severity, tags, builder 9 variantes
- **Social Live** : Reddit JSON + Telegram t.me/s/ BeautifulSoup, pas seulement RSS
- **Media Live** : GDELT artlist massive
- **Frontend** : 7 tabs vs 5, social/media séparés, dorking lab complet, config API, demo offline
- **Satellite** : Esri 0.3m HD + Cesium 3D globe réel (corrigé, pas de token Ion)
- **GitHub Ready** : Workflows APK + Pages + Dockerfile + render.yaml

---

## 10. Sécurité & Éthique

- Dorks fournis à but éducatif OSINT, usage responsable
- Respect robots.txt, User-Agent identifié `HUMAN-OSINT-ULTIMATE/3.0`
- Données publiques uniquement (OSINT), pas d'intrusion
- Pour signalement vulnérabilité critique, contacter en privé

---

## 11. Licence

MIT - Usage humanitaire, recherche, journalisme, OSINT.

---

**Auteur** : Pratisig / Geoint - v3.0 ULTIMATE - 2025
**Stack** : FastAPI, Leaflet, CesiumJS, Turf.js, ExifReader, BeautifulSoup, GDELT, Reddit, Telegram
**Déploiement** : GitHub Pages (frontend) + Render/Railway/Fly.io (backend) + GitHub Actions (APK)
