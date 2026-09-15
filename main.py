#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HUMAN-OSINT v1.0 - Serveur Backend FastAPI
Architecture de veille OSINT, analyse GEOINT et persistance SQLite.
"""

import os
import json
import sqlite3
import logging
from datetime import datetime
from typing import List, Optional, Dict, Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
import feedparser
import dateutil.parser

# Configuration de la journalisation
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("HUMAN-OSINT")

# Constantes de l'application
DB_NAME = "osint_database.db"

app = FastAPI(
    title="HUMAN-OSINT v1.0 API",
    description="API REST de renseignement en sources ouvertes et cartographie tactique GEOINT",
    version="1.0.0"
)

# Activation du CORS pour autoriser les requêtes depuis l'interface web (locale ou distribuée)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -----------------------------------------------------------------------------
# Modèles de Données Pydantic
# -----------------------------------------------------------------------------

class IncidentModel(BaseModel):
    id: Optional[int] = None
    title: str
    link: str
    source: str
    category: str
    latitude: float
    longitude: float
    published_at: str
    created_at: Optional[str] = None

class GeozoneCreate(BaseModel):
    name: str = Field(..., description="Nom de la zone tactique")
    geometry_type: str = Field(..., description="Type de géométrie (Polygon, LineString, Point, etc.)")
    geojson_data: Any = Field(..., description="Donnée brute GeoJSON ou chaîne JSON")
    area_sqkm: float = Field(default=0.0, description="Surface en km² ou distance mesurée")

class GeozoneResponse(BaseModel):
    id: int
    name: str
    geometry_type: str
    geojson_data: Any
    area_sqkm: float
    created_at: str

# -----------------------------------------------------------------------------
# Gestion de la Base de Données SQLite
# -----------------------------------------------------------------------------

def get_db_connection() -> sqlite3.Connection:
    """Établit une connexion vers la base de données SQLite locale avec WAL mode."""
    conn = sqlite3.connect(DB_NAME, timeout=10.0)
    conn.row_factory = sqlite3.Row
    # Activation du mode WAL (Write-Ahead Logging) pour de meilleures performances de concurrence
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn

def init_db():
    """Initialise les tables de la base de données SQLite si elles n'existent pas."""
    logger.info("Vérification et initialisation de la base SQLite...")
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        # Table 1 : incidents (Historisation de la veille OSINT)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS incidents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                link TEXT UNIQUE NOT NULL,
                source TEXT NOT NULL,
                category TEXT NOT NULL,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                published_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
        """)
        
        # Table 2 : geozones (Persistance des dessins tactiques GEOINT)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS geozones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                geometry_type TEXT NOT NULL,
                geojson_data TEXT NOT NULL,
                area_sqkm REAL NOT NULL,
                created_at TEXT NOT NULL
            );
        """)
        
        # Indexation pour accélérer les requêtes de recherche et l'évitement des doublons
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_incidents_category ON incidents(category);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_incidents_link ON incidents(link);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_geozones_created ON geozones(created_at);")
        conn.commit()
    logger.info(f"Base de données '{DB_NAME}' prête et synchronisée.")

# Initialisation au démarrage
@app.on_event("startup")
def on_startup():
    init_db()

# -----------------------------------------------------------------------------
# Dictionnaires et Fonctions d'Extraction Spatiale et Sémantique
# -----------------------------------------------------------------------------

# Dictionnaire de géolocalisation approximative par mots-clés d'intérêts géopolitiques et crises
GEO_HOTSPOTS: Dict[str, Dict[str, float]] = {
    # Moyen-Orient & Levant
    "gaza": {"lat": 31.45, "lng": 34.38},
    "israel": {"lat": 31.76, "lng": 35.21},
    "jerusalem": {"lat": 31.77, "lng": 35.23},
    "tel aviv": {"lat": 32.08, "lng": 34.78},
    "cisjordanie": {"lat": 31.94, "lng": 35.30},
    "west bank": {"lat": 31.94, "lng": 35.30},
    "liban": {"lat": 33.89, "lng": 35.50},
    "lebanon": {"lat": 33.89, "lng": 35.50},
    "beyrouth": {"lat": 33.89, "lng": 35.50},
    "beirut": {"lat": 33.89, "lng": 35.50},
    "syrie": {"lat": 34.80, "lng": 38.99},
    "syria": {"lat": 34.80, "lng": 38.99},
    "damas": {"lat": 33.51, "lng": 36.27},
    "damascus": {"lat": 33.51, "lng": 36.27},
    "iran": {"lat": 32.42, "lng": 53.68},
    "teheran": {"lat": 35.68, "lng": 51.38},
    "yemen": {"lat": 15.55, "lng": 48.51},
    "houthi": {"lat": 15.35, "lng": 44.20},
    "sanaa": {"lat": 15.36, "lng": 44.19},
    "mer rouge": {"lat": 20.00, "lng": 38.50},
    "red sea": {"lat": 20.00, "lng": 38.50},
    "bab-el-mandeb": {"lat": 12.58, "lng": 43.33},
    "ormuz": {"lat": 26.56, "lng": 56.25},
    "hormuz": {"lat": 26.56, "lng": 56.25},
    "irak": {"lat": 33.22, "lng": 43.67},
    "iraq": {"lat": 33.22, "lng": 43.67},
    "bagdad": {"lat": 33.31, "lng": 44.36},

    # Europe de l'Est & Caucase
    "ukraine": {"lat": 48.37, "lng": 31.16},
    "kiev": {"lat": 50.45, "lng": 30.52},
    "kyiv": {"lat": 50.45, "lng": 30.52},
    "donbass": {"lat": 48.01, "lng": 37.80},
    "donetsk": {"lat": 48.01, "lng": 37.80},
    "zaporijia": {"lat": 47.83, "lng": 35.13},
    "crimee": {"lat": 45.30, "lng": 34.40},
    "crimea": {"lat": 45.30, "lng": 34.40},
    "kharkiv": {"lat": 49.99, "lng": 36.23},
    "odessa": {"lat": 46.48, "lng": 30.72},
    "russie": {"lat": 55.75, "lng": 37.61},
    "russia": {"lat": 55.75, "lng": 37.61},
    "moscou": {"lat": 55.75, "lng": 37.61},
    "moscow": {"lat": 55.75, "lng": 37.61},
    "bielorussie": {"lat": 53.70, "lng": 27.95},
    "armenie": {"lat": 40.06, "lng": 45.03},
    "azerbaidjan": {"lat": 40.14, "lng": 47.57},

    # Asie & Pacifique
    "taiwan": {"lat": 23.69, "lng": 120.96},
    "taipei": {"lat": 25.03, "lng": 121.56},
    "mer de chine": {"lat": 16.00, "lng": 114.00},
    "chine": {"lat": 35.86, "lng": 104.19},
    "china": {"lat": 35.86, "lng": 104.19},
    "pekin": {"lat": 39.90, "lng": 116.40},
    "beijing": {"lat": 39.90, "lng": 116.40},
    "coree du nord": {"lat": 40.33, "lng": 127.51},
    "north korea": {"lat": 40.33, "lng": 127.51},
    "pyongyang": {"lat": 39.03, "lng": 125.76},
    "birmanie": {"lat": 21.91, "lng": 95.95},
    "myanmar": {"lat": 21.91, "lng": 95.95},
    "pakistan": {"lat": 30.37, "lng": 69.34},
    "afghanistan": {"lat": 33.93, "lng": 67.70},
    "kaboul": {"lat": 34.55, "lng": 69.20},

    # Afrique & Sahel
    "soudan": {"lat": 12.86, "lng": 30.21},
    "sudan": {"lat": 12.86, "lng": 30.21},
    "khartoum": {"lat": 15.50, "lng": 32.55},
    "darfour": {"lat": 13.00, "lng": 25.00},
    "darfur": {"lat": 13.00, "lng": 25.00},
    "congo": {"lat": -4.03, "lng": 21.75},
    "rdc": {"lat": -4.03, "lng": 21.75},
    "drc": {"lat": -4.03, "lng": 21.75},
    "goma": {"lat": -1.67, "lng": 29.22},
    "mali": {"lat": 17.57, "lng": -3.99},
    "bamako": {"lat": 12.63, "lng": -8.00},
    "niger": {"lat": 17.60, "lng": 8.08},
    "niamey": {"lat": 13.51, "lng": 2.12},
    "burkina faso": {"lat": 12.23, "lng": -1.56},
    "sahel": {"lat": 15.00, "lng": 2.00},
    "somalie": {"lat": 5.15, "lng": 46.19},
    "somalia": {"lat": 5.15, "lng": 46.19},
    "mogadiscio": {"lat": 2.04, "lng": 45.34},
    "ethiopie": {"lat": 9.14, "lng": 40.48},
    "libye": {"lat": 26.33, "lng": 17.22},
    "nigeria": {"lat": 9.08, "lng": 8.67},

    # Amériques
    "venezuela": {"lat": 6.42, "lng": -66.58},
    "caracas": {"lat": 10.48, "lng": -66.90},
    "colombie": {"lat": 4.57, "lng": -74.29},
    "haiti": {"lat": 18.97, "lng": -72.28},
    "port-au-prince": {"lat": 18.59, "lng": -72.30},
    "etats-unis": {"lat": 38.90, "lng": -77.03},
    "usa": {"lat": 38.90, "lng": -77.03},
    "washington": {"lat": 38.90, "lng": -77.03}
}

# Mots-clés pour la catégorisation automatique des incidents
KEYWORDS_CATEGORY = {
    "conflict": [
        "war", "strike", "missile", "combat", "drone", "army", "attack", "bomb",
        "guerre", "frappe", "attaque", "armée", "explosion", "soldat", "front",
        "invasion", "otan", "nato", "troupes", "cease-fire", "hamas", "hezbollah",
        "tsahal", "rebels", "insurgents", "otage", "hostage", "shelling", "clash"
    ],
    "oil": [
        "oil", "gas", "petrol", "barrel", "opec", "pipeline", "refinery", "energy",
        "tanker", "eia", "brent", "wti", "crude", "pétrole", "gaz", "baril",
        "opep", "raffinerie", "énergie", "carburant", "hydrocarbure"
    ],
    "epidemic": [
        "epidemic", "virus", "who", "disease", "outbreak", "covid", "mpox", "cholera",
        "infection", "health", "vaccine", "flu", "oms", "épidémie", "choléra",
        "contamination", "sanitaire", "pandémie", "fièvre", "ebola", "urgence sanitaire"
    ],
    "geo": [
        "earthquake", "flood", "cyclone", "tsunami", "volcano", "hurricane", "storm",
        "landslide", "drought", "séisme", "tremblement", "inondation", "tempête",
        "sécheresse", "ouragan", "volcan", "catastrophe", "glissement de terrain"
    ]
}

def extract_geo_location(text: str) -> tuple[float, float]:
    """Analyse le texte et retourne les coordonnées estimées [Latitude, Longitude]."""
    text_lower = text.lower()
    for name, coords in GEO_HOTSPOTS.items():
        # Détection de mot complet ou segment signifiant
        if f" {name} " in f" {text_lower} " or f"'{name}'" in text_lower or f"({name})" in text_lower:
            return coords["lat"], coords["lng"]
        if name in text_lower:
            return coords["lat"], coords["lng"]
    # Coordonnées par défaut (Méditerranée orientale / point de convergence géostratégique)
    return 32.5, 35.0

def classify_category(text: str, source: str) -> str:
    """Détermine la catégorie de l'incident (conflict, oil, epidemic, geo)."""
    text_lower = text.lower()
    source_lower = source.lower()

    if "oilprice" in source_lower or "eia" in source_lower:
        return "oil"
    if "oms" in source_lower or "who" in source_lower:
        return "epidemic"

    for category, terms in KEYWORDS_CATEGORY.items():
        for term in terms:
            if term in text_lower:
                return category
    
    return "conflict"  # Catégorie dominante par défaut pour la veille de crise

# -----------------------------------------------------------------------------
# Configuration des Flux RSS Cibles
# -----------------------------------------------------------------------------

RSS_FEEDS = [
    {"source": "ReliefWeb", "url": "https://reliefweb.int/updates/rss.xml", "default_cat": "geo"},
    {"source": "Crisis Group", "url": "https://www.crisisgroup.org/rss.xml", "default_cat": "conflict"},
    {"source": "ONU", "url": "https://news.un.org/feed/subscribe/en/news/all/rss.xml", "default_cat": "conflict"},
    {"source": "OilPrice", "url": "https://oilprice.com/rss/main", "default_cat": "oil"},
    {"source": "BBC", "url": "http://feeds.bbci.co.uk/news/world/rss.xml", "default_cat": "conflict"},
    {"source": "OMS", "url": "https://www.who.int/rss-feeds/news-english.xml", "default_cat": "epidemic"},
    {"source": "EIA", "url": "https://www.eia.gov/rss/press_releases.xml", "default_cat": "oil"}
]

# Incidents de démonstration opérationnels (fallback en cas de coupure réseau)
DEMO_INCIDENTS = [
    {
        "title": "Moyen-Orient : Frappes de précision signalées sur des infrastructures au Sud-Liban",
        "link": "https://crisiswatch.org/alerts/lebanon-strikes-2026",
        "source": "Crisis Group",
        "category": "conflict",
        "latitude": 33.27,
        "longitude": 35.20,
        "published_at": datetime.utcnow().isoformat()
    },
    {
        "title": "Mer Rouge : Nouvelle tentative d'interception d'un tanker pétrolier dans le détroit de Bab-el-Mandeb",
        "link": "https://oilprice.com/geopolitics/red-sea-tanker-incident-2026",
        "source": "OilPrice",
        "category": "oil",
        "latitude": 12.58,
        "longitude": 43.33,
        "published_at": datetime.utcnow().isoformat()
    },
    {
        "title": "OMS Alerte : Surveillance épidémiologique accrue suite à une flambée de choléra au Soudan",
        "link": "https://who.int/emergencies/sudan-cholera-response-2026",
        "source": "OMS",
        "category": "epidemic",
        "latitude": 15.50,
        "longitude": 32.55,
        "published_at": datetime.utcnow().isoformat()
    },
    {
        "title": "Détroit de Taïwan : Détection d'un groupe aéronaval à proximité des zones économiques exclusives",
        "link": "https://news.un.org/monitoring/taiwan-strait-naval-activity-2026",
        "source": "ONU",
        "category": "conflict",
        "latitude": 24.50,
        "longitude": 119.80,
        "published_at": datetime.utcnow().isoformat()
    },
    {
        "title": "Front Est Ukraine : Intensification des frappes d'artillerie et mouvements de blindés près de Kharkiv",
        "link": "https://bbc.com/news/world-europe-kharkiv-front-2026",
        "source": "BBC",
        "category": "conflict",
        "latitude": 49.99,
        "longitude": 36.23,
        "published_at": datetime.utcnow().isoformat()
    },
    {
        "title": "Séisme magnitude 6.2 enregistré en mer Égée : Pas d'alerte tsunami majeure",
        "link": "https://reliefweb.int/disaster/aegean-sea-earthquake-2026",
        "source": "ReliefWeb",
        "category": "geo",
        "latitude": 37.89,
        "longitude": 26.85,
        "published_at": datetime.utcnow().isoformat()
    }
]

# -----------------------------------------------------------------------------
# Endpoints API : Gestion des Flux et Incidents
# -----------------------------------------------------------------------------

@app.get("/api/feeds/live", response_model=List[Dict[str, Any]])
def get_live_feeds():
    """
    Scrape les flux RSS, extrait la géolocalisation et la catégorie,
    insère les nouveaux incidents en base SQLite (sans doublons sur l'URL),
    et retourne les 20 incidents les plus récents.
    """
    nouveaux_articles = 0
    now_iso = datetime.utcnow().isoformat()

    conn = get_db_connection()
    cursor = conn.cursor()

    for feed_info in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_info["url"])
            for entry in feed.entries[:8]:  # Limite par flux pour garder des performances rapides
                title = entry.get("title", "").strip()
                link = entry.get("link", "").strip()
                if not title or not link:
                    continue

                # Détermination de la date de publication
                pub_date = entry.get("published") or entry.get("updated")
                if pub_date:
                    try:
                        pub_iso = dateutil.parser.parse(pub_date).isoformat()
                    except Exception:
                        pub_iso = now_iso
                else:
                    pub_iso = now_iso

                # Traitement géospatial et sémantique
                lat, lng = extract_geo_location(title)
                category = classify_category(title, feed_info["source"])

                try:
                    cursor.execute("""
                        INSERT INTO incidents (title, link, source, category, latitude, longitude, published_at, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (title, link, feed_info["source"], category, lat, lng, pub_iso, now_iso))
                    nouveaux_articles += 1
                except sqlite3.IntegrityError:
                    # L'article existe déjà (contrainte UNIQUE sur link)
                    pass
        except Exception as e:
            logger.warning(f"Erreur lors de la lecture du flux {feed_info['source']}: {e}")

    # Injection automatique des incidents de démonstration si la base est vide (ex: premier démarrage sans réseau)
    cursor.execute("SELECT COUNT(*) as count FROM incidents")
    total_in_db = cursor.fetchone()["count"]
    if total_in_db == 0:
        logger.info("Base vide : injection des incidents tactiques de référence.")
        for item in DEMO_INCIDENTS:
            try:
                cursor.execute("""
                    INSERT INTO incidents (title, link, source, category, latitude, longitude, published_at, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (item["title"], item["link"], item["source"], item["category"], item["latitude"], item["longitude"], item["published_at"], now_iso))
            except sqlite3.IntegrityError:
                pass

    conn.commit()

    # Récupération des 20 incidents les plus récents
    cursor.execute("""
        SELECT id, title, link, source, category, latitude, longitude, published_at, created_at
        FROM incidents
        ORDER BY published_at DESC, id DESC
        LIMIT 20
    """)
    rows = cursor.fetchall()
    conn.close()

    return [dict(row) for row in rows]

@app.get("/api/incidents/history", response_model=List[Dict[str, Any]])
def get_incidents_history(
    category: Optional[str] = Query(None, description="Filtrer par catégorie (conflict, oil, geo, epidemic)"),
    date: Optional[str] = Query(None, description="Filtrer par date (format YYYY-MM-DD)")
):
    """
    Renvoie tout l'historique des incidents enregistrés dans la BDD,
    avec filtrage optionnel par catégorie et date.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    query = "SELECT id, title, link, source, category, latitude, longitude, published_at, created_at FROM incidents WHERE 1=1"
    params = []

    if category and category.lower() != "all":
        query += " AND category = ?"
        params.append(category.lower())

    if date:
        query += " AND published_at LIKE ?"
        params.append(f"{date}%")

    query += " ORDER BY published_at DESC, id DESC LIMIT 500"

    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()

    return [dict(row) for row in rows]

# -----------------------------------------------------------------------------
# Endpoints API : Gestion des Zones GEOINT (Tactical Drawings)
# -----------------------------------------------------------------------------

@app.post("/api/geozones", response_model=GeozoneResponse)
def create_geozone(zone: GeozoneCreate):
    """
    Reçoit un objet GeoJSON issu du tracé cartographique Leaflet/Cesium,
    sauvegarde la géométrie et sa surface dans la base SQLite locale.
    """
    now_iso = datetime.utcnow().isoformat()
    
    # Sérialisation uniforme de la géométrie GeoJSON
    if isinstance(zone.geojson_data, (dict, list)):
        raw_geojson = json.dumps(zone.geojson_data, ensure_ascii=False)
    else:
        raw_geojson = str(zone.geojson_data)

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO geozones (name, geometry_type, geojson_data, area_sqkm, created_at)
        VALUES (?, ?, ?, ?, ?)
    """, (zone.name, zone.geometry_type, raw_geojson, zone.area_sqkm, now_iso))
    conn.commit()
    new_id = cursor.lastrowid
    conn.close()

    # Reconversion du json pour la réponse
    try:
        parsed_geom = json.loads(raw_geojson)
    except Exception:
        parsed_geom = raw_geojson

    return {
        "id": new_id,
        "name": zone.name,
        "geometry_type": zone.geometry_type,
        "geojson_data": parsed_geom,
        "area_sqkm": zone.area_sqkm,
        "created_at": now_iso
    }

@app.get("/api/geozones", response_model=List[GeozoneResponse])
def get_all_geozones():
    """
    Récupère toutes les zones tactiques sauvegardées pour les réafficher
    automatiquement sur la carte (Leaflet et Cesium) au chargement de l'application.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, name, geometry_type, geojson_data, area_sqkm, created_at
        FROM geozones
        ORDER BY created_at DESC, id DESC
    """)
    rows = cursor.fetchall()
    conn.close()

    result = []
    for row in rows:
        raw_data = row["geojson_data"]
        try:
            parsed_data = json.loads(raw_data)
        except Exception:
            parsed_data = raw_data

        result.append({
            "id": row["id"],
            "name": row["name"],
            "geometry_type": row["geometry_type"],
            "geojson_data": parsed_data,
            "area_sqkm": row["area_sqkm"],
            "created_at": row["created_at"]
        })
    return result

@app.delete("/api/geozones/{zone_id}")
def delete_geozone(zone_id: int):
    """Supprime une zone tactique spécifique de la base SQLite."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM geozones WHERE id = ?", (zone_id,))
    conn.commit()
    affected = cursor.rowcount
    conn.close()

    if affected == 0:
        raise HTTPException(status_code=404, detail=f"Zone avec l'identifiant {zone_id} non trouvée.")
    
    return {"status": "success", "message": f"Zone {zone_id} supprimée avec succès."}

# -----------------------------------------------------------------------------
# Distribution de l'Interface Web Front-end
# -----------------------------------------------------------------------------

@app.get("/")
def serve_index():
    """Sert l'interface client Single Page Application index.html."""
    index_path = os.path.join(os.path.dirname(__file__), "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path, media_type="text/html")
    return JSONResponse({
        "status": "HUMAN-OSINT v1.0 Backend Running",
        "database": DB_NAME,
        "docs": "/docs",
        "feeds": "/api/feeds/live",
        "geozones": "/api/geozones"
    })

# -----------------------------------------------------------------------------
# Point d'Entrée Principal pour Exécution Directe
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    print("=================================================================")
    print(" [HUMAN-OSINT v1.0] - Démarrage du serveur FastAPI")
    print(f" Base SQLite : {DB_NAME}")
    print(" Interface UI : http://localhost:8000")
    print(" Documentation Swagger : http://localhost:8000/docs")
    print("=================================================================")
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
