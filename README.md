# HUMAN-OSINT v1.0 // Plateforme SIG & OSINT Tactique

Plateforme opérationnelle de renseignement en sources ouvertes (OSINT), d'analyse géospatiale (GEOINT) et de veille de crise en temps réel avec persistance locale SQLite.

---

## 1. Architecture Technique

- **Backend** : Python 3.10+, FastAPI, Uvicorn, SQLite3 natif (mode WAL), Feedparser.
- **Frontend** : Single Page Application (HTML5 / CSS3 Cyberpunk / Vanilla JS).
- **Cartographie Hybride** : Leaflet.js (2D) avec tuiles Dark Matter & CesiumJS (Globe 3D).
- **Moteur Géospatial** : Turf.js pour le calcul dynamique de surface (km²) et distances tactiques.
- **Analyse Image & EXIF** : ExifReader.js pour la détection instantanée de balises GPS et localisation sur carte.
- **Base de Données** : Fichier local `osint_database.db` avec création automatique des tables.

---

## 2. Installation et Démarrage Rapide

### Prérequis
- Python 3.10 ou supérieur installé sur votre système.

### Étape 1 : Cloner ou extraire les fichiers du projet
Assurez-vous de disposer des fichiers suivants dans le même répertoire :
- `requirements.txt`
- `main.py`
- `index.html`
- `README.md`

### Étape 2 : Créer un environnement virtuel (recommandé)
```bash
python3 -m venv venv
source venv/bin/activate  # Sur Linux/macOS
# ou sur Windows :
# venv\Scripts\activate
```

### Étape 3 : Installer les dépendances
```bash
pip install -r requirements.txt
```

### Étape 4 : Lancer le serveur backend FastAPI
```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```
*Ou directement :*
```bash
python3 main.py
```

### Étape 5 : Accéder à l'application
- **Interface Utilisateur (Web App)** : [http://localhost:8000](http://localhost:8000)
- **Documentation API Interactive (Swagger)** : [http://localhost:8000/docs](http://localhost:8000/docs)

---

## 3. Vérification de la Base de Données SQLite (`osint_database.db`)

Au premier démarrage, le serveur crée automatiquement le fichier `osint_database.db` à la racine s'il n'existe pas.

Pour inspecter la base de données :
```bash
# Avec le client CLI sqlite3
sqlite3 osint_database.db

# Lister les tables créées :
.tables
# Résultat attendu : geozones  incidents

# Vérifier le schéma des incidents :
.schema incidents

# Vérifier les incidents insérés lors de la veille live :
SELECT id, source, category, title, latitude, longitude FROM incidents LIMIT 5;

# Vérifier les zones GEOINT tracées et sauvegardées :
SELECT id, name, geometry_type, area_sqkm, created_at FROM geozones;
```

---

## 4. Spécifications des Endpoints API

| Méthode | Endpoint | Description |
|---|---|---|
| `GET` | `/api/feeds/live` | Agrège les flux RSS (ReliefWeb, BBC, OMS, Crisis Group, ONU, OilPrice, EIA), extrait la géolocalisation et la catégorie, insère sans doublons et renvoie les 20 derniers incidents. |
| `GET` | `/api/incidents/history` | Récupère l'historique complet avec filtres optionnels `?category=...` et `?date=...`. |
| `POST` | `/api/geozones` | Enregistre une zone tracée en base de données (`name`, `geometry_type`, `geojson_data`, `area_sqkm`). |
| `GET` | `/api/geozones` | Renvoie toutes les zones tactiques sauvegardées pour les restaurer sur les cartes 2D/3D. |
| `DELETE` | `/api/geozones/{id}` | Supprime définitivement une zone spécifique de la base de données. |

---

## 5. Fonctionnalités Clés de l'Interface

1. **Veille Live OSINT** :
   - Cartes d'incidents avec codes couleurs néon (`Rouge: Conflit`, `Orange: Énergie/Pétrole`, `Cyan: Catastrophe/Géo`, `Violet: Épidémie`).
   - Bouton `🎯 LOCALISER` pour centrer automatiquement la caméra sur le foyer de tension.
2. **Outils GEOINT & Cartographie 2D/3D** :
   - Dessin de polygones, périmètres de sécurité, itinéraires et zones circulaires.
   - Calcul instantané des superficies en km² grâce à Turf.js.
   - Sauvegarde persistante dans SQLite et bouton d'export au format standard GeoJSON.
   - Bascule instantanée entre la vue tactique 2D (Leaflet) et le globe spatial 3D (CesiumJS).
3. **Analyse Image & Balises EXIF** :
   - Glisser-déposer d'une photographie.
   - Extraction des coordonnées GPS (Latitude / Longitude / Altitude / Modèle de l'appareil).
   - Positionnement automatique d'une balise rouge d'origine sur la carte.
   - Raccourcis de recherche d'image inversée (Google Lens, Yandex Visual Intelligence, TinEye).
4. **Générateur Google Dorks** :
   - Accès immédiat en un clic à des recherches ciblées pour identifier documents classifiés, fuites de mots de passe, répertoires de sauvegardes ou tracés SIG militaires.
