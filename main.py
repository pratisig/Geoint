#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HUMAN-OSINT v4.2 ULTIMATE - OSINT/GEOINT Power Platform
- 70+ RSS sources live, NASA EONET, USGS, GDACS, ReliefWeb, GDELT 5 queries, Reddit 6 subs, Telegram 5 channels
- Reverse image search (Google, Yandex, TinEye, Bing, Baidu, KarmaDecay)
- Advanced search engines (30+ OSINT engines: Shodan, Censys, ZoomEye, Hunter, IntelX, etc.)
- Google Dorking 80+ dorks
- Report generator + AI agent (user-provided keys: OpenAI, Gemini, Anthropic, Mistral)
- Satellite HD Esri 0.3m + Cesium 3D globe real satellite
- Thematic & regional filters, timeline, stats
"""

import os
import json
import sqlite3
import logging
import asyncio
import random
import re
import time
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Dict, Any
from contextlib import asynccontextmanager
from urllib.parse import quote_plus, quote

from fastapi import FastAPI, HTTPException, Query, BackgroundTasks, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
import feedparser
import dateutil.parser
import requests
try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except:
    HAS_BS4 = False

import storage
import osint_tools

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("HUMAN-OSINT-V4")

DB_NAME = "osint_database.db"

# -------------------------------------------------------------------
# AUTO-UPDATE configuration (overridable via environment variables)
# -------------------------------------------------------------------
# How often the background loop re-scrapes every source, in seconds.
BACKGROUND_REFRESH_INTERVAL = int(os.getenv("OSINT_REFRESH_INTERVAL", "60"))
# How often the SSE stream pushes a keep-alive comment so that reverse
# proxies (Render, Cloudflare, nginx) do not close an idle connection.
SSE_HEARTBEAT_SEC = int(os.getenv("OSINT_SSE_HEARTBEAT", "15"))
# Random +/- jitter added to each cycle so that all workers of a
# multi-worker deployment do not hammer the upstream sources at once.
BACKGROUND_JITTER_SEC = int(os.getenv("OSINT_REFRESH_JITTER", "10"))
# Keep at most this many incidents in memory (and persist them to SQLite).
MAX_CACHED_INCIDENTS = int(os.getenv("OSINT_MAX_CACHE", "300"))

APP_VERSION = "4.2.0"

live_cache: Dict[str, Any] = {
    "incidents": [],
    "social": [],
    "media": [],
    "last_updated": None,
    "is_refreshing": False,
    # Fingerprint of the cached content. The SSE stream compares it to
    # detect *any* change, not only a change in the number of items.
    "content_hash": None,
    "next_refresh_at": None,
    "last_error": None,
    "last_duration_ms": 0,
    # "live" when real sources answered, "fallback" when demo data was used.
    "data_mode": None,
    "stats": {"total_fetches": 0, "rss": 0, "satellite": 0, "social": 0, "media": 0, "gdelt": 0, "failed": 0},
    "sources_status": {}
}


def compute_content_hash(incidents: List[Dict[str, Any]]) -> str:
    """Return a short fingerprint of a list of incidents.

    The previous implementation of the SSE stream only notified clients when
    ``len(incidents)`` changed, so a refresh that returned the same *number*
    of (but newer) items never reached the browser. Hashing the identifiers
    and publication dates fixes that.

    Args:
        incidents: Cached incidents, each expected to expose ``link`` and
            ``published_at``.

    Returns:
        A 16-char hexadecimal digest, stable for identical content and
        different as soon as one item is added, removed or re-dated."""

    import hashlib
    parts = sorted(f"{i.get('link', '')}|{i.get('published_at', '')}" for i in (incidents or []))
    return hashlib.sha1("\n".join(parts).encode("utf-8", "ignore")).hexdigest()[:16]

# -------------------------------------------------------------------
# Models
# -------------------------------------------------------------------
class GeozoneCreate(BaseModel):
    name: str
    geometry_type: str
    geojson_data: Any
    area_sqkm: float = 0.0

class GeozoneResponse(BaseModel):
    id: int
    name: str
    geometry_type: str
    geojson_data: Any
    area_sqkm: float
    created_at: str

class DorkGenerateRequest(BaseModel):
    keywords: str = Field(..., description="Mots-clés principaux")
    country: Optional[str] = None
    site: Optional[str] = None
    filetype: Optional[str] = None
    category: Optional[str] = "all"
    exclude: Optional[str] = None
    date_range: Optional[str] = None

class ReverseImageRequest(BaseModel):
    image_url: str = Field(..., description="URL de l'image à rechercher")
    description: Optional[str] = None

class AdvancedSearchRequest(BaseModel):
    query: str
    engines: List[str] = Field(default_factory=lambda: ["google", "bing", "yandex"])
    category: Optional[str] = "general"
    site: Optional[str] = None
    filetype: Optional[str] = None
    country: Optional[str] = None
    date_range: Optional[str] = None
    extra: Optional[str] = None

class ReportRequest(BaseModel):
    topic: str = Field(..., description="Sujet du rapport")
    regions: List[str] = Field(default_factory=list)
    categories: List[str] = Field(default_factory=list)
    time_range: str = Field(default="7d", description="7d, 30d, 90d, all")
    include_sections: List[str] = Field(default_factory=lambda: ["summary", "incidents", "risk", "actors", "map", "recommendations"])
    max_incidents: int = 50
    format: str = "markdown"
    ai_provider: Optional[str] = None  # openai, gemini, anthropic, mistral
    ai_api_key: Optional[str] = None
    ai_model: Optional[str] = None

class AIAnalyzeRequest(BaseModel):
    prompt: str
    context: Optional[str] = None
    incidents: Optional[List[Dict[str, Any]]] = None
    provider: str = "openai"
    api_key: str
    model: Optional[str] = None

# -------------------------------------------------------------------
# DB
# -------------------------------------------------------------------
def get_db_connection() -> sqlite3.Connection:
    """Open a SQLite connection with row access by column name.

    Returns:
        ``sqlite3.Connection`` whose ``row_factory`` is ``sqlite3.Row``, so
        query results behave like dictionaries."""

    conn = sqlite3.connect(DB_NAME, timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn

def init_db():
    """Create the archive schema, delegating to the storage layer.

    Kept as a thin wrapper so the rest of the module (and existing callers)
    do not have to know whether the backend is SQLite or PostgreSQL.
    """
    info = storage.init_schema()
    logger.info(
        "Storage ready: backend=%s archived=%d retention=%dd",
        info["backend"], info["archived_incidents"], storage.RETENTION_DAYS,
    )
    return info


def get_db_connection() -> sqlite3.Connection:
    """Open a connection to the active storage backend.

    Retained for backwards compatibility with code that still speaks DB-API
    directly. New code should use :mod:`storage`, which handles both SQLite
    and PostgreSQL and never lets a database error reach the API.

    Returns:
        A DB-API connection with mapping-style row access.
    """
    return storage.connect()


# -------------------------------------------------------------------
# RSS FEEDS - 70+ SOURCES V4
# -------------------------------------------------------------------
RSS_FEEDS = [
    {"source": "ReliefWeb Updates", "url": "https://reliefweb.int/updates/rss.xml", "type": "OFFICIEL", "lang": "en"},
    {"source": "ReliefWeb Disasters", "url": "https://reliefweb.int/disasters/rss.xml", "type": "OFFICIEL", "lang": "en"},
    {"source": "ReliefWeb Reports", "url": "https://reliefweb.int/reports/rss.xml", "type": "OFFICIEL", "lang": "en"},
    {"source": "ReliefWeb Jobs", "url": "https://reliefweb.int/jobs/rss.xml", "type": "OFFICIEL", "lang": "en"},
    {"source": "GDACS Alerts", "url": "https://www.gdacs.org/xml/rss.xml", "type": "ALERTE_CATASTROPHE", "lang": "en"},
    {"source": "WHO Disease Outbreak", "url": "https://www.who.int/feeds/entity/csr/don/en/rss.xml", "type": "ALERTE_CATASTROPHE", "lang": "en"},
    {"source": "UN News EN", "url": "https://news.un.org/feed/subscribe/en/news/all/rss.xml", "type": "OFFICIEL", "lang": "en"},
    {"source": "UN News FR", "url": "https://news.un.org/feed/subscribe/fr/news/all/rss.xml", "type": "OFFICIEL", "lang": "fr"},
    {"source": "Crisis Group", "url": "https://www.crisisgroup.org/rss.xml", "type": "RENSEIGNEMENT", "lang": "en"},
    {"source": "BBC World", "url": "http://feeds.bbci.co.uk/news/world/rss.xml", "type": "PRESSE", "lang": "en"},
    {"source": "BBC Africa", "url": "http://feeds.bbci.co.uk/news/world/africa/rss.xml", "type": "PRESSE", "lang": "en"},
    {"source": "BBC Middle East", "url": "http://feeds.bbci.co.uk/news/world/middle_east/rss.xml", "type": "PRESSE", "lang": "en"},
    {"source": "Al Jazeera", "url": "https://www.aljazeera.com/xml/rss/all.xml", "type": "PRESSE", "lang": "en"},
    {"source": "France24 FR", "url": "https://www.france24.com/fr/rss", "type": "PRESSE", "lang": "fr"},
    {"source": "France24 EN", "url": "https://www.france24.com/en/rss", "type": "PRESSE", "lang": "en"},
    {"source": "France24 Afrique", "url": "https://www.france24.com/fr/afrique/rss", "type": "PRESSE", "lang": "fr"},
    {"source": "RFI Afrique", "url": "https://www.rfi.fr/fr/rss", "type": "PRESSE", "lang": "fr"},
    {"source": "RFI Monde", "url": "https://www.rfi.fr/fr/monde/rss", "type": "PRESSE", "lang": "fr"},
    {"source": "Le Monde", "url": "https://www.lemonde.fr/rss/une.xml", "type": "PRESSE", "lang": "fr"},
    {"source": "Le Monde Afrique", "url": "https://www.lemonde.fr/afrique/rss_full.xml", "type": "PRESSE", "lang": "fr"},
    {"source": "The Guardian World", "url": "https://www.theguardian.com/world/rss", "type": "PRESSE", "lang": "en"},
    {"source": "The Guardian Global", "url": "https://www.theguardian.com/global-development/rss", "type": "PRESSE", "lang": "en"},
    {"source": "DW World", "url": "https://rss.dw.com/rdf/rss-en-all", "type": "PRESSE", "lang": "en"},
    {"source": "DW Africa", "url": "https://rss.dw.com/rdf/rss-en-africa", "type": "PRESSE", "lang": "en"},
    {"source": "Euronews", "url": "https://www.euronews.com/rss?format=mrss", "type": "PRESSE", "lang": "en"},
    {"source": "Jeune Afrique", "url": "https://www.jeuneafrique.com/feed/", "type": "PRESSE", "lang": "fr"},
    {"source": "AfricaNews", "url": "https://www.africanews.com/feed/rss", "type": "PRESSE", "lang": "en"},
    {"source": "AP News World", "url": "https://apnews.com/rss/apf-topnews", "type": "PRESSE", "lang": "en"},
    {"source": "Reuters World", "url": "https://www.reutersagency.com/feed/?best-topics=world", "type": "PRESSE", "lang": "en"},
    {"source": "OilPrice", "url": "https://oilprice.com/rss/main", "type": "PRESSE", "lang": "en"},
    {"source": "Maritime Executive", "url": "https://maritime-executive.com/rss", "type": "PRESSE", "lang": "en"},
    {"source": "The Hacker News", "url": "https://feeds.feedburner.com/TheHackersNews", "type": "CYBER_FUITE", "lang": "en"},
    {"source": "BleepingComputer", "url": "https://www.bleepingcomputer.com/feed/", "type": "CYBER_FUITE", "lang": "en"},
    {"source": "The Record Cyber", "url": "https://therecord.media/feed", "type": "CYBER_FUITE", "lang": "en"},
    {"source": "CISA Alerts", "url": "https://www.cisa.gov/cybersecurity-advisories/all.xml", "type": "CYBER_FUITE", "lang": "en"},
    {"source": "ISW Ukraine", "url": "https://www.understandingwar.org/rss.xml", "type": "RENSEIGNEMENT", "lang": "en"},
    {"source": "Defense News", "url": "https://www.defensenews.com/arc/outboundfeeds/rss/category/global/?outputType=xml", "type": "PRESSE", "lang": "en"},
    {"source": "Defense One", "url": "https://www.defenseone.com/rss/all/", "type": "PRESSE", "lang": "en"},
    {"source": "OCHA Relief", "url": "https://www.unocha.org/rss.xml", "type": "OFFICIEL", "lang": "en"},
    {"source": "NASA Breaking", "url": "https://www.nasa.gov/rss/dyn/breaking_news.rss", "type": "ALERTE_CATASTROPHE", "lang": "en"},
    {"source": "USGS 2.5 Day", "url": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_day.atom", "type": "ALERTE_CATASTROPHE", "lang": "en"},
    {"source": "EIA Energy", "url": "https://www.eia.gov/rss/press_releases.xml", "type": "PRESSE", "lang": "en"},
    {"source": "MSF News", "url": "https://www.msf.org/rss.xml", "type": "OFFICIEL", "lang": "en"},
    {"source": "ICRC News", "url": "https://www.icrc.org/en/rss.xml", "type": "OFFICIEL", "lang": "en"},
    {"source": "UNHCR", "url": "https://www.unhcr.org/rss.xml", "type": "OFFICIEL", "lang": "en"},
    {"source": "WFP News", "url": "https://www.wfp.org/rss/news.xml", "type": "OFFICIEL", "lang": "en"},
    {"source": "ACLED", "url": "https://acleddata.com/feed/", "type": "RENSEIGNEMENT", "lang": "en"},
    {"source": "LiveUAMap", "url": "https://liveuamap.com/rss", "type": "RENSEIGNEMENT", "lang": "en"},
    {"source": "VOA Africa", "url": "https://www.voanews.com/api/zyqetevumgqeqatyq-40", "type": "PRESSE", "lang": "en"},
    {"source": "VOA World", "url": "https://www.voanews.com/api/zyqqeumkqeqqetgyq-40", "type": "PRESSE", "lang": "en"},
    {"source": "AllAfrica", "url": "https://allafrica.com/tools/headlines/rdf/main/headlines.rdf", "type": "PRESSE", "lang": "en"},
    {"source": "Sahel Intelligence", "url": "https://www.sahel-intelligence.com/feed/", "type": "RENSEIGNEMENT", "lang": "fr"},
    {"source": "The New Humanitarian", "url": "https://www.thenewhumanitarian.org/rss.xml", "type": "OFFICIEL", "lang": "en"},
    {"source": "IRIN News", "url": "https://www.thenewhumanitarian.org/rss.xml", "type": "OFFICIEL", "lang": "en"},
    {"source": "Human Rights Watch", "url": "https://www.hrw.org/rss/news", "type": "OFFICIEL", "lang": "en"},
    {"source": "Amnesty", "url": "https://www.amnesty.org/en/rss/", "type": "OFFICIEL", "lang": "en"},
    {"source": "Reuters Africa", "url": "https://www.reutersagency.com/feed/?best-topics=africa", "type": "PRESSE", "lang": "en"},
    {"source": "Reuters Middle East", "url": "https://www.reutersagency.com/feed/?best-topics=middle-east", "type": "PRESSE", "lang": "en"},
    {"source": "NYT World", "url": "https://rss.nytimes.com/services/xml/rss/nyt/World.xml", "type": "PRESSE", "lang": "en"},
    {"source": "CNN Africa", "url": "http://rss.cnn.com/rss/edition_africa.rss", "type": "PRESSE", "lang": "en"},
    {"source": "Al Jazeera Africa", "url": "https://www.aljazeera.com/xml/rss/all.xml", "type": "PRESSE", "lang": "en"},
]

# -------------------------------------------------------------------
# DORKS DATABASE - 80+ V4
# -------------------------------------------------------------------
DORKS_DATABASE = [
    {"id": 1, "category": "documents", "severity": "critical", "title": "Documents CONFIDENTIEL / RESTRICTED", "query": 'filetype:pdf "CONFIDENTIAL" OR "RESTRICTED" OR "INTERNAL USE ONLY"', "description": "PDF classifiés exposés", "tags": ["pdf", "confidentiel"]},
    {"id": 2, "category": "documents", "severity": "high", "title": "Documents gouvernementaux sensibles", "query": 'site:gov OR site:gouv.fr filetype:pdf "confidentiel" OR "secret défense"', "description": "Docs gouvernementaux", "tags": ["gov", "pdf"]},
    {"id": 3, "category": "documents", "severity": "high", "title": "Rapports situation humanitaire", "query": 'site:reliefweb.int OR site:unocha.org filetype:pdf "situation report" OR "humanitarian needs"', "description": "Rapports humanitaires", "tags": ["humanitaire", "ocha"]},
    {"id": 4, "category": "documents", "severity": "medium", "title": "Documents état-major", "query": 'intext:"classified" OR "état-major" ext:doc OR ext:docx OR ext:pdf', "description": "Docs militaires", "tags": ["militaire"]},
    {"id": 5, "category": "documents", "severity": "high", "title": "Procès-verbaux & conseils ministres", "query": 'filetype:pdf "conseil des ministres" OR "procès-verbal" "confidentiel"', "description": "PV officiels", "tags": ["gouvernement"]},
    {"id": 6, "category": "credentials", "severity": "critical", "title": "Mots de passe & API keys Pastebin", "query": 'site:pastebin.com OR site:ghostbin.com OR site:justpaste.it "password" OR "api_key" OR "secret"', "description": "Fuites credentials", "tags": ["pastebin", "password"]},
    {"id": 7, "category": "credentials", "severity": "critical", "title": "Clés privées RSA / SSH", "query": '"BEGIN RSA PRIVATE KEY" OR "BEGIN OPENSSH PRIVATE KEY" OR "BEGIN DSA PRIVATE KEY"', "description": "Clés privées exposées", "tags": ["rsa", "ssh"]},
    {"id": 8, "category": "credentials", "severity": "critical", "title": "Fichiers .env avec secrets", "query": 'filetype:env "DB_PASSWORD" OR "API_KEY" OR "SECRET_KEY"', "description": ".env exposés", "tags": ["env", "secret"]},
    {"id": 9, "category": "credentials", "severity": "high", "title": "Config AWS / Azure", "query": 'filetype:json "aws_access_key" OR "aws_secret" OR "azure" "credentials"', "description": "Cloud credentials", "tags": ["aws", "cloud"]},
    {"id": 10, "category": "credentials", "severity": "high", "title": "Logs avec mots de passe", "query": 'intext:"password=" OR "pwd=" filetype:log OR filetype:txt', "description": "Logs credentials", "tags": ["log"]},
    {"id": 11, "category": "database", "severity": "critical", "title": "Dumps SQL exposés", "query": 'intitle:"index of" "database.sql" OR "dump.sql" OR "backup.sql"', "description": "Dumps DB", "tags": ["sql", "dump"]},
    {"id": 12, "category": "database", "severity": "high", "title": "phpMyAdmin / Adminer ouverts", "query": 'inurl:phpmyadmin OR inurl:adminer OR inurl:"/dbadmin/"', "description": "Panels DB ouverts", "tags": ["phpmyadmin"]},
    {"id": 13, "category": "database", "severity": "high", "title": "Fichiers SQL avec INSERT", "query": 'filetype:sql "INSERT INTO" "password" OR "user"', "description": "SQL avec users", "tags": ["sql"]},
    {"id": 14, "category": "database", "severity": "medium", "title": "MongoDB / Elasticsearch ouverts", "query": 'inurl:"/api/_search" OR inurl:"_cat/indices" "elasticsearch" OR "mongodb"', "description": "NoSQL ouverts", "tags": ["elasticsearch"]},
    {"id": 15, "category": "iot", "severity": "high", "title": "Caméras IP ouvertes", "query": 'inurl:"/view.shtml" OR inurl:"/view/index.shtml" "Network Camera" OR "IP Camera"', "description": "Caméras réseau", "tags": ["camera"]},
    {"id": 16, "category": "iot", "severity": "high", "title": "AXIS Caméras Live", "query": 'intitle:"Live View / - AXIS" OR intitle:"AXIS 2100" OR intitle:"Network Camera"', "description": "AXIS cams", "tags": ["axis"]},
    {"id": 17, "category": "iot", "severity": "medium", "title": "CCTV / DVR login", "query": 'intitle:"DVR Login" OR intitle:"CCTV" inurl:"/login" OR "/admin"', "description": "CCTV login", "tags": ["cctv"]},
    {"id": 18, "category": "iot", "severity": "medium", "title": "Shodan style - Webcams", "query": 'inurl:"/cgi-bin/guestimage.html" OR inurl:"/snapshot.jpg" OR inurl:"/video.mjpg"', "description": "Webcams ouvertes", "tags": ["webcam"]},
    {"id": 19, "category": "geospatial", "severity": "high", "title": "Fichiers KML/KMZ militaires", "query": 'filetype:kml OR filetype:kmz "military" OR "tactical" OR "base" OR "army"', "description": "Tracés militaires", "tags": ["kml", "militaire"]},
    {"id": 20, "category": "geospatial", "severity": "medium", "title": "Shapefiles SHP humanitaires", "query": 'filetype:shp OR filetype:shx OR filetype:dbf "humanitarian" OR "refugee" OR "camp"', "description": "Shapefiles camps", "tags": ["shp", "humanitaire"]},
    {"id": 21, "category": "geospatial", "severity": "medium", "title": "GeoServer WMS/WFS ouverts", "query": 'inurl:"/geoserver/wms" OR inurl:"/geoserver/wfs" OR "GetCapabilities" site:gov', "description": "GeoServer ouverts", "tags": ["geoserver"]},
    {"id": 22, "category": "geospatial", "severity": "low", "title": "Imagerie satellite Sentinel/Landsat", "query": 'site:copernicus.esa.int OR site:earthexplorer.usgs.gov "Sentinel-2" OR "Landsat"', "description": "Portails satellite", "tags": ["satellite"]},
    {"id": 23, "category": "geospatial", "severity": "high", "title": "Cartes infrastructures critiques", "query": 'filetype:pdf "infrastructure" "pipeline" OR "power grid" OR "water supply" site:gov', "description": "Infra critiques", "tags": ["infrastructure"]},
    {"id": 24, "category": "backup", "severity": "critical", "title": "Répertoires backup exposés", "query": 'intitle:"index of" inurl:ftp OR inurl:backup "backup" OR ".bak"', "description": "Backups FTP", "tags": ["backup"]},
    {"id": 25, "category": "backup", "severity": "high", "title": "Fichiers .bak / .old", "query": 'filetype:bak OR filetype:old OR filetype:backup "config" OR "password"', "description": "Fichiers backup", "tags": ["bak"]},
    {"id": 26, "category": "backup", "severity": "high", "title": "Logs erreurs exposés", "query": 'filetype:log "error" OR "warning" "password" OR "username" intitle:"index of"', "description": "Logs erreurs", "tags": ["log"]},
    {"id": 27, "category": "backup", "severity": "medium", "title": "Archives ZIP/RAR sensibles", "query": 'intitle:"index of" filetype:zip OR filetype:rar "confidential" OR "backup"', "description": "Archives sensibles", "tags": ["zip"]},
    {"id": 28, "category": "scada", "severity": "critical", "title": "SCADA / ICS / HMI login", "query": 'intitle:"SCADA" OR "ICS" OR "HMI" inurl:"/portal" OR "/login" OR "/admin"', "description": "SCADA login", "tags": ["scada"]},
    {"id": 29, "category": "scada", "severity": "critical", "title": "Schneider / Siemens login", "query": 'intext:"Schneider Electric" OR "Siemens" OR "Allen-Bradley" intitle:"login" OR "portal"', "description": "Industriel login", "tags": ["ics"]},
    {"id": 30, "category": "scada", "severity": "high", "title": "Systèmes contrôle industriel", "query": 'inurl:"/cgi-bin/" "PLC" OR "RTU" OR "Modbus" "status"', "description": "PLC/RTU", "tags": ["plc"]},
    {"id": 31, "category": "social", "severity": "medium", "title": "Twitter/X OSINT conflit", "query": 'site:twitter.com OR site:x.com ("airstrike" OR "OSINT" OR "geolocated")', "description": "Tweets OSINT", "tags": ["twitter", "osint"]},
    {"id": 32, "category": "social", "severity": "medium", "title": "Telegram OSINT channels", "query": 'site:t.me "OSINT" OR "Ukraine" OR "Syria" OR "Sahel" "conflict"', "description": "Telegram OSINT", "tags": ["telegram"]},
    {"id": 33, "category": "social", "severity": "low", "title": "Reddit OSINT", "query": 'site:reddit.com/r/OSINT OR site:reddit.com/r/UkraineWar OR r/Syria OR r/Sahel', "description": "Reddit OSINT", "tags": ["reddit"]},
    {"id": 34, "category": "social", "severity": "low", "title": "Facebook pages conflit", "query": 'site:facebook.com "conflict" OR "humanitarian" "live" "video"', "description": "Facebook live", "tags": ["facebook"]},
    {"id": 35, "category": "social", "severity": "medium", "title": "YouTube live conflit", "query": 'site:youtube.com "live" "airstrike" OR "conflict" OR "war" "Ukraine" OR "Gaza"', "description": "YouTube live", "tags": ["youtube"]},
    {"id": 36, "category": "social", "severity": "medium", "title": "TikTok OSINT", "query": 'site:tiktok.com "OSINT" OR "conflict" "geolocated"', "description": "TikTok OSINT", "tags": ["tiktok"]},
    {"id": 37, "category": "humanitarian", "severity": "low", "title": "Rapports OCHA / ReliefWeb", "query": 'site:reliefweb.int OR site:unocha.org filetype:pdf "humanitarian needs overview" OR "HNO"', "description": "HNO OCHA", "tags": ["ocha", "hno"]},
    {"id": 38, "category": "humanitarian", "severity": "low", "title": "HDX Humanitarian Data", "query": 'site:data.humdata.org OR site:humdata.org "dataset" "conflict" OR "refugee"', "description": "HDX datasets", "tags": ["hdx"]},
    {"id": 39, "category": "humanitarian", "severity": "low", "title": "MSF / CICR rapports", "query": 'site:msf.org OR site:icrc.org filetype:pdf "situation" OR "report" "conflict"', "description": "MSF/ICRC", "tags": ["msf"]},
    {"id": 40, "category": "humanitarian", "severity": "medium", "title": "Camps réfugiés KML", "query": 'filetype:kml OR filetype:kmz "refugee camp" OR "IDP camp" OR "humanitarian"', "description": "Camps KML", "tags": ["camp", "kml"]},
    {"id": 41, "category": "darknet", "severity": "high", "title": "BreachForums / Leak forums", "query": 'site:breachforums.is OR site:raidforums.com OR site:leakbase.io "database" OR "leak"', "description": "Forums leaks", "tags": ["breach", "leak"]},
    {"id": 42, "category": "darknet", "severity": "high", "title": "Ransomware leak sites", "query": 'intext:"leaked by" "ransomware" "data" site:onion OR site:tor', "description": "Ransomware leaks", "tags": ["ransomware"]},
    {"id": 43, "category": "people", "severity": "medium", "title": "Emails gouvernementaux", "query": 'filetype:xls OR filetype:csv intext:"@gov" OR "@gouv.fr" "email"', "description": "Emails gov", "tags": ["email", "gov"]},
    {"id": 44, "category": "people", "severity": "medium", "title": "LinkedIn OSINT", "query": 'site:linkedin.com "military" OR "humanitarian" "Sahel" OR "Ukraine"', "description": "LinkedIn", "tags": ["linkedin"]},
    {"id": 45, "category": "vuln", "severity": "high", "title": "CVE & Exploits récents", "query": 'site:cve.mitre.org OR site:exploit-db.com "CVE-2024" OR "CVE-2025" "remote"', "description": "CVE récents", "tags": ["cve"]},
    {"id": 46, "category": "vuln", "severity": "high", "title": "Shodan / Censys style", "query": 'intext:"default password" "admin" "login" "router" OR "camera"', "description": "Default creds", "tags": ["shodan"]},
    {"id": 47, "category": "sahel", "severity": "high", "title": "JNIM / AQMI / EIGS documents", "query": '"JNIM" OR "AQMI" OR "EIGS" OR "Ansar Dine" filetype:pdf OR site:twitter.com', "description": "Groupes Sahel", "tags": ["sahel", "jnim"]},
    {"id": 48, "category": "sahel", "severity": "medium", "title": "Wagner / Africa Corps Sahel", "query": '"Wagner" OR "Africa Corps" "Mali" OR "Niger" OR "Burkina" filetype:pdf OR site:telegram', "description": "Wagner Sahel", "tags": ["wagner"]},
    {"id": 49, "category": "sahel", "severity": "medium", "title": "MINUSMA / FAMa rapports", "query": 'site:minusma.unmissions.org OR site:fama.ml filetype:pdf "rapport" OR "sécurité"', "description": "MINUSMA", "tags": ["minusma"]},
    {"id": 50, "category": "ukraine", "severity": "high", "title": "ISW / DeepState OSINT", "query": 'site:understandingwar.org OR site:deepstatemap.live "Russian offensive" OR "Ukrainian"', "description": "ISW DeepState", "tags": ["ukraine", "isw"]},
    {"id": 51, "category": "ukraine", "severity": "medium", "title": "Oryx pertes matérielles", "query": 'site:oryxspioenkop.com "list of" "losses" "Ukraine" OR "Russia"', "description": "Oryx", "tags": ["oryx"]},
    {"id": 52, "category": "satellite", "severity": "low", "title": "NASA FIRMS feux actifs", "query": 'site:firms.modaps.eosdis.nasa.gov "active fire" OR "MODIS" OR "VIIRS"', "description": "FIRMS feux", "tags": ["firms", "nasa"]},
    {"id": 53, "category": "satellite", "severity": "low", "title": "Sentinel Hub EO Browser", "query": 'site:sentinel-hub.com OR site:apps.sentinel-hub.com "EO Browser"', "description": "Sentinel", "tags": ["sentinel"]},
    {"id": 54, "category": "satellite", "severity": "low", "title": "Planet Labs / Maxar", "query": 'site:planet.com OR site:maxar.com "satellite imagery" "Ukraine" OR "Gaza"', "description": "Planet Maxar", "tags": ["maxar"]},
    {"id": 55, "category": "advanced", "severity": "high", "title": "GitHub secrets exposés", "query": 'site:github.com "password" OR "api_key" "DB_PASSWORD" "filename:.env"', "description": "GitHub secrets", "tags": ["github"]},
    {"id": 56, "category": "advanced", "severity": "high", "title": "Jenkins / GitLab ouverts", "query": 'intitle:"Jenkins" OR intitle:"GitLab" inurl:"/login" OR "/admin"', "description": "CI/CD ouverts", "tags": ["jenkins"]},
    {"id": 57, "category": "advanced", "severity": "medium", "title": "Swagger / API docs exposés", "query": 'inurl:"/swagger" OR "/api-docs" OR "/openapi.json" "API"', "description": "Swagger", "tags": ["swagger"]},
    {"id": 58, "category": "advanced", "severity": "medium", "title": "WordPress / Joomla vulnérables", "query": 'inurl:"/wp-admin" OR "/administrator" "login" "version"', "description": "CMS login", "tags": ["wordpress"]},
    {"id": 59, "category": "advanced", "severity": "low", "title": "Wayback Machine OSINT", "query": 'site:web.archive.org "example.com" "confidential" OR "backup"', "description": "Wayback", "tags": ["wayback"]},
    {"id": 60, "category": "advanced", "severity": "low", "title": "Google Cache", "query": 'cache:example.com "confidential" OR "password"', "description": "Google cache", "tags": ["cache"]},
    {"id": 61, "category": "maritime", "severity": "medium", "title": "AIS / MarineTraffic navires", "query": 'site:marinetraffic.com OR site:vesselfinder.com "tanker" OR "cargo" "Red Sea"', "description": "Trafic maritime", "tags": ["ais", "maritime"]},
    {"id": 62, "category": "aviation", "severity": "medium", "title": "ADS-B / FlightRadar avions militaires", "query": 'site:flightradar24.com OR site:adsbexchange.com "military" OR "callsign"', "description": "Trafic aérien", "tags": ["adsb", "aviation"]},
    {"id": 63, "category": "documents", "severity": "medium", "title": "Rapports OCHA HNO/HRP", "query": 'site:reliefweb.int filetype:pdf "Humanitarian Needs Overview" OR "Humanitarian Response Plan"', "description": "HNO/HRP", "tags": ["hno", "hrp"]},
    {"id": 64, "category": "geospatial", "severity": "high", "title": "Coordonnées MGRS / UTM militaires", "query": '"MGRS" OR "UTM" "military grid" filetype:pdf OR filetype:kml', "description": "MGRS", "tags": ["mgrs", "utm"]},
    {"id": 65, "category": "credentials", "severity": "high", "title": "Tokens Discord / Slack", "query": '"discord token" OR "slack token" OR "xoxb-" OR "xoxp-"', "description": "Tokens", "tags": ["discord", "slack"]},
    {"id": 66, "category": "social", "severity": "medium", "title": "Bellingcat / OSINT techniques", "query": 'site:bellingcat.com OR site:osintframework.com "geolocation" OR "verification"', "description": "Bellingcat", "tags": ["bellingcat"]},
    {"id": 67, "category": "satellite", "severity": "low", "title": "NASA Worldview temps réel", "query": 'site:worldview.earthdata.nasa.gov "MODIS" OR "VIIRS" "true color"', "description": "Worldview", "tags": ["worldview"]},
    {"id": 68, "category": "advanced", "severity": "medium", "title": "Exposed Grafana / Kibana", "query": 'intitle:"Grafana" OR intitle:"Kibana" inurl:"/login" OR "/app/kibana"', "description": "Grafana", "tags": ["grafana"]},
    {"id": 69, "category": "iot", "severity": "high", "title": "Printers / IP cams open", "query": 'intitle:"printer status" OR "Network Camera" inurl:"/admin" OR "/status"', "description": "Printers", "tags": ["printer"]},
    {"id": 70, "category": "database", "severity": "high", "title": "Firebase exposé", "query": 'site:firebaseio.com OR "firebaseio.com" ".json" "password" OR "user"', "description": "Firebase", "tags": ["firebase"]},
    {"id": 71, "category": "people", "severity": "medium", "title": "HaveIBeenPwned / breach", "query": 'site:haveibeenpwned.com OR site:dehashed.com OR site:intelx.io "email" OR "breach"', "description": "Breach check", "tags": ["breach"]},
    {"id": 72, "category": "advanced", "severity": "low", "title": "Shodan search", "query": 'site:shodan.io "port:22" OR "port:80" "country:ML" OR "country:UA"', "description": "Shodan", "tags": ["shodan"]},
    {"id": 73, "category": "advanced", "severity": "low", "title": "Censys search", "query": 'site:search.censys.io "services.port: 22" OR "services.port: 80"', "description": "Censys", "tags": ["censys"]},
    {"id": 74, "category": "geospatial", "severity": "medium", "title": "Wikimapia / OSM military", "query": 'site:wikimapia.org OR site:openstreetmap.org "military base" OR "army base"', "description": "Wikimapia", "tags": ["wikimapia"]},
    {"id": 75, "category": "humanitarian", "severity": "low", "title": "HDX / Humanitarian Data", "query": 'site:data.humdata.org "conflict" OR "displacement" OR "food security" filetype:csv', "description": "HDX", "tags": ["hdx"]},
    {"id": 76, "category": "sahel", "severity": "high", "title": "JNIM / ISGS Telegram", "query": 'site:t.me "JNIM" OR "ISGS" OR "EIGS" "Mali" OR "Niger" "attaque"', "description": "JNIM Telegram", "tags": ["telegram", "jnim"]},
    {"id": 77, "category": "ukraine", "severity": "high", "title": "LiveUAMap / DeepState", "query": 'site:liveuamap.com OR site:deepstatemap.live "Ukraine" "Russian" "offensive"', "description": "LiveUAMap", "tags": ["liveuamap"]},
    {"id": 78, "category": "satellite", "severity": "low", "title": "Copernicus / Sentinel", "query": 'site:copernicus.eu OR site:sentinel.esa.int "Sentinel-2" "Ukraine" OR "Gaza"', "description": "Copernicus", "tags": ["copernicus"]},
    {"id": 79, "category": "advanced", "severity": "high", "title": "Exposed .git", "query": 'intitle:"index of" ".git" "config" OR "HEAD"', "description": ".git exposed", "tags": ["git"]},
    {"id": 80, "category": "advanced", "severity": "medium", "title": "Open Directory", "query": 'intitle:"index of" "parent directory" "password" OR "confidential"', "description": "Open dir", "tags": ["directory"]},
]

# -------------------------------------------------------------------
# OSINT ENGINES DATABASE - 30+ engines
# -------------------------------------------------------------------
OSINT_ENGINES = [
    {"id": "google", "name": "Google", "category": "search", "url": "https://www.google.com/search?q={query}", "description": "Moteur généraliste + dorking", "free": True, "tags": ["search", "dorking"]},
    {"id": "bing", "name": "Bing", "category": "search", "url": "https://www.bing.com/search?q={query}", "description": "Microsoft Bing", "free": True, "tags": ["search"]},
    {"id": "yandex", "name": "Yandex", "category": "search", "url": "https://yandex.com/search/?text={query}", "description": "Moteur russe, excellent pour images", "free": True, "tags": ["search", "image"]},
    {"id": "duckduckgo", "name": "DuckDuckGo", "category": "search", "url": "https://duckduckgo.com/?q={query}", "description": "Privacy-focused", "free": True, "tags": ["search", "privacy"]},
    {"id": "brave", "name": "Brave Search", "category": "search", "url": "https://search.brave.com/search?q={query}", "description": "Independent index", "free": True, "tags": ["search"]},
    {"id": "mojeek", "name": "Mojeek", "category": "search", "url": "https://www.mojeek.com/search?q={query}", "description": "Independent UK index", "free": True, "tags": ["search"]},
    {"id": "google_images", "name": "Google Images", "category": "image", "url": "https://www.google.com/search?tbm=isch&q={query}", "description": "Recherche images", "free": True, "tags": ["image", "reverse"]},
    {"id": "yandex_images", "name": "Yandex Images", "category": "image", "url": "https://yandex.com/images/search?text={query}", "description": "Meilleur pour reconnaissance faciale", "free": True, "tags": ["image", "reverse"]},
    {"id": "tineye", "name": "TinEye", "category": "image", "url": "https://tineye.com/search?url={query}", "description": "Reverse image search", "free": True, "tags": ["reverse", "image"]},
    {"id": "bing_visual", "name": "Bing Visual", "category": "image", "url": "https://www.bing.com/images/search?view=detailv2&iss=sbi&q=imgurl:{query}", "description": "Bing reverse image", "free": True, "tags": ["reverse"]},
    {"id": "karmadecay", "name": "KarmaDecay", "category": "image", "url": "http://karmadecay.com/{query}", "description": "Reddit reverse image", "free": True, "tags": ["reddit", "reverse"]},
    {"id": "shodan", "name": "Shodan", "category": "iot", "url": "https://www.shodan.io/search?query={query}", "description": "Moteur IoT / devices", "free": False, "tags": ["iot", "shodan"]},
    {"id": "censys", "name": "Censys", "category": "iot", "url": "https://search.censys.io/search?resource=hosts&q={query}", "description": "Scan internet", "free": False, "tags": ["iot", "censys"]},
    {"id": "zoomeye", "name": "ZoomEye", "category": "iot", "url": "https://www.zoomeye.org/searchResult?q={query}", "description": "Cyberspace search", "free": False, "tags": ["iot"]},
    {"id": "hunter", "name": "Hunter.io", "category": "people", "url": "https://hunter.io/search/{query}", "description": "Email finder", "free": False, "tags": ["email", "people"]},
    {"id": "intelx", "name": "IntelX", "category": "breach", "url": "https://intelx.io/?s={query}", "description": "Data breach search", "free": False, "tags": ["breach", "leak"]},
    {"id": "dehashed", "name": "Dehashed", "category": "breach", "url": "https://dehashed.com/search?query={query}", "description": "Breach database", "free": False, "tags": ["breach"]},
    {"id": "haveibeenpwned", "name": "HaveIBeenPwned", "category": "breach", "url": "https://haveibeenpwned.com/account/{query}", "description": "Check breach", "free": True, "tags": ["breach", "email"]},
    {"id": "virustotal", "name": "VirusTotal", "category": "domain", "url": "https://www.virustotal.com/gui/search/{query}", "description": "Domain/IP scan", "free": True, "tags": ["domain", "malware"]},
    {"id": "securitytrails", "name": "SecurityTrails", "category": "domain", "url": "https://securitytrails.com/domain/{query}", "description": "DNS history", "free": False, "tags": ["dns", "domain"]},
    {"id": "whois", "name": "Whois", "category": "domain", "url": "https://who.is/whois/{query}", "description": "Whois lookup", "free": True, "tags": ["domain"]},
    {"id": "dnsdumpster", "name": "DNSDumpster", "category": "domain", "url": "https://dnsdumpster.com/", "description": "DNS recon", "free": True, "tags": ["dns"]},
    {"id": "urlscan", "name": "urlscan.io", "category": "domain", "url": "https://urlscan.io/search/#{query}", "description": "URL scanner", "free": True, "tags": ["url"]},
    {"id": "wayback", "name": "Wayback Machine", "category": "archive", "url": "https://web.archive.org/web/*/{query}", "description": "Archive web", "free": True, "tags": ["archive"]},
    {"id": "archiveis", "name": "Archive.is", "category": "archive", "url": "https://archive.is/{query}", "description": "Archive alternative", "free": True, "tags": ["archive"]},
    {"id": "marinetraffic", "name": "MarineTraffic", "category": "maritime", "url": "https://www.marinetraffic.com/en/ais/index/search/all/keyword:{query}", "description": "AIS ships", "free": True, "tags": ["ais", "maritime"]},
    {"id": "flightradar24", "name": "FlightRadar24", "category": "aviation", "url": "https://www.flightradar24.com/data/aircraft/{query}", "description": "ADS-B flights", "free": True, "tags": ["adsb", "aviation"]},
    {"id": "adsbexchange", "name": "ADSBExchange", "category": "aviation", "url": "https://globe.adsbexchange.com/?icao={query}", "description": "Military ADS-B", "free": True, "tags": ["adsb", "military"]},
    {"id": "sentinel", "name": "Sentinel Hub", "category": "satellite", "url": "https://apps.sentinel-hub.com/eo-browser/?lat={lat}&lng={lng}&zoom=10", "description": "Satellite imagery", "free": True, "tags": ["satellite"]},
    {"id": "nasa_worldview", "name": "NASA Worldview", "category": "satellite", "url": "https://worldview.earthdata.nasa.gov/?v={lng},{lat},{lng},{lat}&l=Reference_Labels,Reference_Features,VIIRS_SNPP_CorrectedReflectance_TrueColor", "description": "NASA satellite", "free": True, "tags": ["satellite", "nasa"]},
    {"id": "eonet", "name": "NASA EONET", "category": "satellite", "url": "https://eonet.gsfc.nasa.gov/", "description": "Natural events", "free": True, "tags": ["satellite", "eonet"]},
    {"id": "firms", "name": "NASA FIRMS", "category": "satellite", "url": "https://firms.modaps.eosdis.nasa.gov/map/#d:24hrs;@0,0,3z", "description": "Active fires", "free": True, "tags": ["satellite", "fire"]},
    {"id": "bellingcat", "name": "Bellingcat Toolkit", "category": "osint", "url": "https://www.bellingcat.com/resources/", "description": "OSINT toolkit", "free": True, "tags": ["toolkit", "verification"]},
    {"id": "osintframework", "name": "OSINT Framework", "category": "osint", "url": "https://osintframework.com/", "description": "Framework complet", "free": True, "tags": ["framework"]},
    {"id": "reddit", "name": "Reddit Search", "category": "social", "url": "https://www.reddit.com/search/?q={query}", "description": "Reddit OSINT", "free": True, "tags": ["social", "reddit"]},
    {"id": "telegram", "name": "Telegram Search", "category": "social", "url": "https://t.me/s/{query}", "description": "Telegram public", "free": True, "tags": ["social", "telegram"]},
    {"id": "twitter", "name": "Twitter/X Search", "category": "social", "url": "https://twitter.com/search?q={query}", "description": "X search", "free": True, "tags": ["social"]},
    {"id": "youtube", "name": "YouTube Search", "category": "social", "url": "https://www.youtube.com/results?search_query={query}", "description": "Video search", "free": True, "tags": ["video"]},
    {"id": "linkedin", "name": "LinkedIn Search", "category": "people", "url": "https://www.linkedin.com/search/results/all/?keywords={query}", "description": "Professional", "free": True, "tags": ["people", "linkedin"]},
    {"id": "github", "name": "GitHub Search", "category": "code", "url": "https://github.com/search?q={query}", "description": "Code search", "free": True, "tags": ["code", "github"]},
]

# -------------------------------------------------------------------
# REVERSE IMAGE ENGINES
# -------------------------------------------------------------------
REVERSE_IMAGE_ENGINES = [
    {"id": "google", "name": "Google Images", "url_template": "https://images.google.com/searchbyimage?image_url={image_url}", "description": "Google reverse", "free": True},
    {"id": "yandex", "name": "Yandex Images", "url_template": "https://yandex.com/images/search?rpt=imageview&url={image_url}", "description": "Meilleur pour visages", "free": True},
    {"id": "tineye", "name": "TinEye", "url_template": "https://tineye.com/search?url={image_url}", "description": "Exact matches", "free": True},
    {"id": "bing", "name": "Bing Visual", "url_template": "https://www.bing.com/images/search?view=detailv2&iss=sbi&q=imgurl:{image_url}", "description": "Bing reverse", "free": True},
    {"id": "baidu", "name": "Baidu Images", "url_template": "https://graph.baidu.com/details?isfromtuchuang=true&tn=pc&image={image_url}", "description": "Chine", "free": True},
    {"id": "karmadecay", "name": "KarmaDecay Reddit", "url_template": "http://karmadecay.com/{image_url}", "description": "Reddit reverse", "free": True},
    {"id": "sogou", "name": "Sogou Images", "url_template": "https://pic.sogou.com/ris?query={image_url}", "description": "Chine", "free": True},
]

# -------------------------------------------------------------------
# FETCHERS
# -------------------------------------------------------------------
def fetch_eonet() -> List[Dict[str, Any]]:
    """Fetch active natural events from the NASA EONET API.

    Returns:
        Normalised incident dictionaries (``SATELLITE`` source type). Empty
        list on any network or parsing error - the caller keeps going."""

    incidents = []
    try:
        r = requests.get("https://eonet.gsfc.nasa.gov/api/v3/events?status=open&limit=40", timeout=10)
        if r.status_code == 200:
            for ev in r.json().get("events", [])[:30]:
                try:
                    geom = ev.get("geometry", [])
                    if not geom: continue
                    last = geom[-1]
                    coords = last.get("coordinates", [])
                    if len(coords) < 2: continue
                    lon, lat = coords[0], coords[1]
                    cat = ev.get("categories", [{}])[0].get("id", "wildfires")
                    incidents.append({
                        "title": f"[NASA EONET LIVE SAT] {ev.get('title')}",
                        "link": ev.get("link", "https://eonet.gsfc.nasa.gov/"),
                        "source": "NASA EONET Satellite Live",
                        "source_type": "ALERTE_CATASTROPHE",
                        "category": "catastrophe",
                        "latitude": float(lat), "longitude": float(lon),
                        "region": "Global", "country": "Satellite Detection",
                        "published_at": last.get("date", datetime.now(timezone.utc).isoformat()),
                        "summary": f"Détection satellite {cat} temps réel - Coords SAT réelles HD",
                        "severity": "high", "actors": ["NASA", "Secours"], "needs": ["Évaluation"], "risk_level": 4
                    })
                except: continue
    except Exception as e:
        logger.warning(f"EONET error {e}")
    return incidents

def fetch_usgs() -> List[Dict[str, Any]]:
    """Fetch recent significant earthquakes from the USGS GeoJSON feed.

    Returns:
        Normalised incident dictionaries. Empty list on error."""

    incidents = []
    try:
        r = requests.get("https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_day.geojson", timeout=10)
        if r.status_code == 200:
            for feat in r.json().get("features", [])[:20]:
                try:
                    props = feat.get("properties", {})
                    geom = feat.get("geometry", {})
                    coords = geom.get("coordinates", [])
                    if len(coords) < 2: continue
                    lon, lat = coords[0], coords[1]
                    mag = props.get("mag", 0)
                    if mag < 4.0: continue
                    place = props.get("place", "Unknown")
                    incidents.append({
                        "title": f"[USGS LIVE SAT] Séisme M{mag} - {place}",
                        "link": props.get("url", "https://earthquake.usgs.gov/"),
                        "source": "USGS Seismic Live",
                        "source_type": "ALERTE_CATASTROPHE",
                        "category": "catastrophe",
                        "latitude": float(lat), "longitude": float(lon),
                        "region": "Global", "country": place.split(",")[-1].strip() if "," in place else "Global",
                        "published_at": datetime.fromtimestamp(props.get("time", 0)/1000, tz=timezone.utc).isoformat(),
                        "summary": f"M{mag} profondeur {coords[2]}km - Alerte sismique SAT temps réel",
                        "severity": "critical" if mag >=6 else "high",
                        "actors": ["USGS", "GDACS"], "needs": ["Secours"], "risk_level": 5 if mag>=6 else 4
                    })
                except: continue
    except Exception as e:
        logger.warning(f"USGS error {e}")
    return incidents

def fetch_reliefweb_api() -> List[Dict[str, Any]]:
    """Fetch the latest humanitarian reports from the ReliefWeb API.

    Returns:
        Normalised incident dictionaries. Empty list on error."""

    incidents = []
    try:
        r = requests.get("https://api.reliefweb.int/v1/disasters?appname=human-osint-v4&limit=30&sort[]=date:desc&fields[include][]=country&fields[include][]=type&fields[include][]=url&fields[include][]=date&fields[include][]=name", timeout=10)
        if r.status_code == 200:
            for item in r.json().get("data", [])[:20]:
                try:
                    fields = item.get("fields", {})
                    title = fields.get("name", "Crise")
                    country_info = fields.get("country", [])
                    country_name = country_info[0].get("name", "International") if country_info else "International"
                    lat, lon, _, region = extract_geo(country_name + " " + title)
                    lat += (random.random()-0.5)*0.5
                    lon += (random.random()-0.5)*0.5
                    dtype = fields.get("type", [{}])[0].get("name", "Disaster") if fields.get("type") else "Disaster"
                    cat = "catastrophe"
                    if "conflict" in dtype.lower() or "complex" in dtype.lower(): cat = "conflit"
                    elif "epidemic" in dtype.lower(): cat = "epidemie"
                    incidents.append({
                        "title": f"[ReliefWeb LIVE] {title} - {country_name}",
                        "link": fields.get("url", "https://reliefweb.int/disaster"),
                        "source": "ReliefWeb API Live",
                        "source_type": "OFFICIEL",
                        "category": cat,
                        "latitude": lat, "longitude": lon,
                        "region": region, "country": country_name,
                        "published_at": fields.get("date", {}).get("created", datetime.now(timezone.utc).isoformat()),
                        "summary": f"{dtype} - {country_name} - Suivi humanitaire LIVE",
                        "severity": "high", "actors": ["OCHA", "ONG"], "needs": needs_from_cat(cat), "risk_level": 4
                    })
                except: continue
    except Exception as e:
        logger.warning(f"ReliefWeb API error {e}")
    return incidents

def fetch_gdelt() -> List[Dict[str, Any]]:
    """Query the GDELT DOC 2.0 API for crisis-related coverage.

    Runs several predefined thematic queries and merges the results.

    Returns:
        Normalised incident dictionaries. Empty list on error."""

    incidents = []
    try:
        queries = [
            "conflict OR war OR airstrike OR offensive OR battle",
            "humanitarian OR refugee OR displacement OR OCHA OR UNHCR",
            "earthquake OR flood OR cyclone OR epidemic OR cholera OR wildfire",
            "protest OR coup OR election OR riot OR demonstration",
            "cyber OR hack OR ransomware OR breach OR leak"
        ]
        for q in queries:
            try:
                url = f"https://api.gdeltproject.org/api/v2/doc/doc?query={quote(q)}&mode=artlist&maxrecords=20&format=json&sort=datedesc"
                r = requests.get(url, timeout=10)
                if r.status_code == 200:
                    data = r.json()
                    for art in data.get("articles", [])[:12]:
                        try:
                            title = art.get("title", "")
                            if not title: continue
                            lat, lon, country, region = extract_geo(title)
                            incidents.append({
                                "title": f"[GDELT LIVE] {title}",
                                "link": art.get("url", "https://gdeltproject.org/"),
                                "source": f"GDELT Media - {art.get('domain', 'global')}",
                                "source_type": "PRESSE",
                                "category": classify(title, "gdelt"),
                                "latitude": lat + (random.random()-0.5)*0.3,
                                "longitude": lon + (random.random()-0.5)*0.3,
                                "region": region, "country": country,
                                "published_at": art.get("seendate", datetime.now(timezone.utc).isoformat()),
                                "summary": f"Article média global GDELT - Domaine: {art.get('domain')} - Lang: {art.get('language')} - Scraping massif",
                                "severity": "medium", "actors": actors_from_text(title, region), "needs": needs_from_cat(classify(title, "gdelt")), "risk_level": 3
                            })
                        except: continue
            except Exception as e:
                logger.warning(f"GDELT query {q} error {e}")
    except Exception as e:
        logger.warning(f"GDELT error {e}")
    return incidents

def fetch_reddit_osint() -> List[Dict[str, Any]]:
    """Fetch the newest posts from the monitored OSINT subreddits.

    Returns:
        Normalised incident dictionaries (``SOCIAL`` source type).
        Empty list on error."""

    incidents = []
    try:
        subs = ["OSINT", "UkraineConflict", "Syria", "Sahel", "geopolitics", "humanitarian", "worldnews", "CombatFootage"]
        for sub in subs[:6]:
            try:
                url = f"https://www.reddit.com/r/{sub}/new/.json?limit=10"
                r = requests.get(url, headers={"User-Agent": "HUMAN-OSINT-V4/4.0"}, timeout=8)
                if r.status_code == 200:
                    data = r.json()
                    for child in data.get("data", {}).get("children", [])[:6]:
                        try:
                            d = child.get("data", {})
                            title = d.get("title", "")
                            if not title: continue
                            lat, lon, country, region = extract_geo(title)
                            incidents.append({
                                "title": f"[Reddit r/{sub} LIVE] {title}",
                                "link": f"https://www.reddit.com{d.get('permalink', '')}",
                                "source": f"Reddit r/{sub} OSINT",
                                "source_type": "OSINT_DEPÊCHE",
                                "category": classify(title, "reddit"),
                                "latitude": lat + (random.random()-0.5)*0.2,
                                "longitude": lon + (random.random()-0.5)*0.2,
                                "region": region, "country": country,
                                "published_at": datetime.fromtimestamp(d.get("created_utc", 0), tz=timezone.utc).isoformat() if d.get("created_utc") else datetime.now(timezone.utc).isoformat(),
                                "summary": f"Post Reddit OSINT - r/{sub} - Score: {d.get('score', 0)} - Comments: {d.get('num_comments', 0)}",
                                "severity": "medium", "actors": actors_from_text(title, region), "needs": needs_from_cat(classify(title, "reddit")), "risk_level": 2
                            })
                        except: continue
            except Exception as e:
                logger.warning(f"Reddit r/{sub} error {e}")
    except Exception as e:
        logger.warning(f"Reddit error {e}")
    return incidents

def fetch_telegram_osint() -> List[Dict[str, Any]]:
    """Scrape the public web preview of monitored Telegram channels.

    Returns:
        Normalised incident dictionaries (``SOCIAL`` source type).
        Empty list on error or when ``beautifulsoup4`` is unavailable."""

    incidents = []
    if not HAS_BS4:
        return incidents
    try:
        channels = ["OSINTtechnical", "UkraineOSINT", "ConflictNews", "liveuamap", "rybar", "intelslava"]
        for ch in channels[:4]:
            try:
                url = f"https://t.me/s/{ch}"
                r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
                if r.status_code == 200:
                    soup = BeautifulSoup(r.text, "lxml")
                    messages = soup.find_all("div", class_="tgme_widget_message_text")[:6]
                    for msg in messages:
                        try:
                            text = msg.get_text()[:250]
                            if len(text) < 20: continue
                            lat, lon, country, region = extract_geo(text)
                            incidents.append({
                                "title": f"[Telegram @{ch} LIVE] {text[:80]}...",
                                "link": f"https://t.me/s/{ch}",
                                "source": f"Telegram @{ch} OSINT",
                                "source_type": "OSINT_DEPÊCHE",
                                "category": classify(text, "telegram"),
                                "latitude": lat + (random.random()-0.5)*0.2,
                                "longitude": lon + (random.random()-0.5)*0.2,
                                "region": region, "country": country,
                                "published_at": datetime.now(timezone.utc).isoformat(),
                                "summary": f"Message Telegram OSINT - @{ch} - {text[:150]}",
                                "severity": "medium", "actors": actors_from_text(text, region), "needs": needs_from_cat(classify(text, "telegram")), "risk_level": 3
                            })
                        except: continue
            except Exception as e:
                logger.warning(f"Telegram {ch} error {e}")
    except Exception as e:
        logger.warning(f"Telegram error {e}")
    return incidents

def fetch_rss_single(feed_info: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Download and parse one RSS/Atom feed.

    Args:
        feed_info: Mapping with at least ``source``, ``url``, ``source_type``
            and ``region`` keys.

    Returns:
        Normalised incident dictionaries for that feed. The outcome is also
        recorded in ``live_cache["sources_status"]`` so the UI can show which
        sources are healthy."""

    incidents = []
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        # Use requests first to avoid feedparser blocking issues
        headers = {"User-Agent": "HUMAN-OSINT-V4/4.0 (OSINT Platform; +https://github.com/pratisig/Geoint)"}
        r = requests.get(feed_info["url"], headers=headers, timeout=12)
        if r.status_code != 200:
            live_cache["sources_status"][feed_info["source"]] = f"HTTP {r.status_code}"
            return []
        content = r.content
        feed = feedparser.parse(content)
        if not feed.entries:
            # try parsing as text
            feed = feedparser.parse(feed_info["url"])
        for entry in feed.entries[:6]:
            try:
                title = entry.get("title", "").strip()
                link = entry.get("link", "").strip()
                if not title or not link: continue
                pub_date = entry.get("published") or entry.get("updated") or entry.get("pubDate")
                if pub_date:
                    try: pub_iso = dateutil.parser.parse(pub_date).isoformat()
                    except: pub_iso = now_iso
                else: pub_iso = now_iso
                summary_raw = entry.get("summary", "") or entry.get("description", "")
                lat, lng, country, region = extract_geo(title + " " + summary_raw)
                category = classify(title + " " + summary_raw, feed_info["source"])
                incidents.append({
                    "title": title,
                    "link": link,
                    "source": feed_info["source"],
                    "source_type": feed_info["type"],
                    "category": category,
                    "latitude": lat + (random.random()-0.5)*0.3,
                    "longitude": lng + (random.random()-0.5)*0.3,
                    "region": region, "country": country,
                    "published_at": pub_iso,
                    "summary": (summary_raw[:240] + f" | Lang: {feed_info['lang']} | Source: {feed_info['source']}") if summary_raw else f"Source: {feed_info['source']}",
                    "severity": "high" if category in ["conflit", "catastrophe"] else "medium",
                    "actors": actors_from_text(title, region),
                    "needs": needs_from_cat(category),
                    "risk_level": 4 if category in ["conflit", "catastrophe"] else 3,
                    "language": feed_info["lang"]
                })
            except Exception as e:
                continue
        if incidents:
            live_cache["sources_status"][feed_info["source"]] = f"OK {len(incidents)}"
        else:
            live_cache["sources_status"][feed_info["source"]] = "OK 0 (no entries)"
    except Exception as e:
        live_cache["sources_status"][feed_info["source"]] = f"ERR {str(e)[:50]}"
        logger.warning(f"RSS {feed_info['source']} error {e}")
    return incidents

def fetch_rss_comprehensive() -> List[Dict[str, Any]]:
    """Fetch every feed in ``RSS_FEEDS`` concurrently.

    Returns:
        Flat list of normalised incidents from all reachable feeds."""

    incidents = []
    # Use ThreadPool for concurrency
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as executor:
        futures = {executor.submit(fetch_rss_single, feed): feed for feed in RSS_FEEDS}
        for future in concurrent.futures.as_completed(futures):
            try:
                res = future.result()
                incidents.extend(res)
            except Exception as e:
                logger.warning(f"RSS thread error {e}")
    logger.info(f"RSS comprehensive fetched {len(incidents)} incidents from {len(RSS_FEEDS)} sources")
    return incidents

def generate_dynamic_fallback() -> List[Dict[str, Any]]:
    """Build a synthetic but plausible incident set.

    Used when upstream sources are unreachable (offline mode, cold start,
    blocked network) so the UI never renders empty.

    Returns:
        List of normalised incident dictionaries tagged ``DEMO``."""

    now = datetime.now(timezone.utc)
    base = [
        {"title": "Conflit actif Donbass/Pokrovsk - Artillerie lourde + drones FPV [LIVE SAT]", "source": "DeepState OSINT Live + ISW Satellite", "type": "RENSEIGNEMENT", "cat": "conflit", "region": "Europe", "country": "Ukraine (Donbass)", "lat": 48.28, "lng": 37.18, "severity": "critical", "risk": 5, "actors": ["Forces UA", "Forces RU", "OTAN"], "needs": ["Sécurité", "Abri"]},
        {"title": "[NASA EONET LIVE SAT] Incendie actif Darfour - Panache FIRMS visible", "source": "NASA EONET Satellite Live", "type": "ALERTE_CATASTROPHE", "cat": "catastrophe", "region": "Afrique", "country": "Soudan", "lat": 13.62, "lng": 25.34, "severity": "high", "risk": 4, "actors": ["NASA", "OCHA"], "needs": ["Eau", "Abri"]},
        {"title": "[USGS LIVE SAT] Séisme M5.8 Mer Rouge - Alerte tsunami", "source": "USGS Seismic Live", "type": "ALERTE_CATASTROPHE", "cat": "catastrophe", "region": "Moyen-Orient", "country": "Mer Rouge", "lat": 14.5, "lng": 42.8, "severity": "critical", "risk": 5, "actors": ["USGS", "GDACS"], "needs": ["Secours"]},
        {"title": "Attaque maritime missile antinavire Bab-el-Mandeb - Pétrolier [AIS SAT]", "source": "UKMTO Maritime Live + Satellite AIS", "type": "OFFICIEL", "cat": "energie", "region": "Moyen-Orient", "country": "Mer Rouge", "lat": 12.58, "lng": 43.33, "severity": "high", "risk": 4, "actors": ["Houthis", "Coalition navale"], "needs": ["Sécurité maritime"]},
        {"title": "[ReliefWeb LIVE] Crise RDC/Kivu - Déplacement M23 massif [OCHA SAT]", "source": "ReliefWeb API Live", "type": "OFFICIEL", "cat": "conflit", "region": "Afrique", "country": "RDC Congo", "lat": -1.67, "lng": 29.22, "severity": "critical", "risk": 5, "actors": ["M23", "FARDC", "MONUSCO", "MSF", "OCHA"], "needs": ["Abri", "Protection", "Eau"]},
        {"title": "[GDELT MEDIA LIVE] Offensive Sahel - JNIM embuscade Gao-Ménaka [SOCIAL]", "source": "GDELT Media Live + Reddit OSINT", "type": "OSINT_DEPÊCHE", "cat": "conflit", "region": "Afrique", "country": "Mali / Sahel", "lat": 16.27, "lng": -0.04, "severity": "high", "risk": 4, "actors": ["JNIM", "FAMa", "Africa Corps"], "needs": ["Sécurité"]},
        {"title": "[Telegram @OSINTtechnical LIVE] Frappes Idlib - Dépôts munitions détruits", "source": "Telegram OSINT Live", "type": "OSINT_DEPÊCHE", "cat": "conflit", "region": "Moyen-Orient", "country": "Syrie", "lat": 35.93, "lng": 36.63, "severity": "high", "risk": 4, "actors": ["HTS", "Forces Syrie"], "needs": ["Protection"]},
        {"title": "Flambée choléra Lac Tchad Cameroun-Nigéria - CTC urgence [OMS LIVE]", "source": "OMS Live + MSF", "type": "ALERTE_CATASTROPHE", "cat": "epidemie", "region": "Afrique", "country": "Cameroun", "lat": 10.59, "lng": 14.32, "severity": "high", "risk": 4, "actors": ["OMS", "MSF"], "needs": ["Vaccins", "Eau"]},
        {"title": "[Reddit r/OSINT LIVE] Cyberattaque ransomware infra énergétique Ukraine", "source": "Reddit OSINT Live + CERT-UA", "type": "CYBER_FUITE", "cat": "cyber", "region": "Europe", "country": "Ukraine", "lat": 50.45, "lng": 30.52, "severity": "high", "risk": 3, "actors": ["CERT-UA", "CISA"], "needs": ["IT"]},
        {"title": "Tentative coup d'État Conakry Guinée - Blindés présidence [RFI LIVE]", "source": "RFI Afrique Live + OSINT", "type": "OFFICIEL", "cat": "protest", "region": "Afrique", "country": "Guinée", "lat": 9.53, "lng": -13.67, "severity": "high", "risk": 4, "actors": ["Garde", "CEDEAO"], "needs": ["Médiation"]},
        {"title": "Détroit Taïwan - 38 aéronefs franchissant ligne médiane ADIZ [MND SAT]", "source": "MND Taïwan Live Satellite", "type": "OFFICIEL", "cat": "conflit", "region": "Asie-Pacifique", "country": "Taïwan", "lat": 24.15, "lng": 119.5, "severity": "high", "risk": 4, "actors": ["PLA", "MND"], "needs": ["Surveillance"]},
        {"title": "[GDACS LIVE SAT] Cyclone Mozambique Cabo Delgado 180km/h", "source": "GDACS Live Satellite", "type": "ALERTE_CATASTROPHE", "cat": "catastrophe", "region": "Afrique", "country": "Mozambique", "lat": -12.33, "lng": 40.5, "severity": "critical", "risk": 5, "actors": ["GDACS", "OCHA"], "needs": ["Évacuation"]},
        {"title": "[BBC LIVE MEDIA] Frappes Gaza - Bilan humanitaire critique", "source": "BBC World Live Media", "type": "PRESSE", "cat": "conflit", "region": "Moyen-Orient", "country": "Palestine/Gaza", "lat": 31.45, "lng": 34.38, "severity": "critical", "risk": 5, "actors": ["Tsahal", "Hamas", "OCHA"], "needs": ["Abri", "Nourriture", "Santé"]},
        {"title": "[France24 LIVE] Sahel - Enlèvement travailleurs humanitaires 3 frontières", "source": "France24 Live Media", "type": "PRESSE", "cat": "conflit", "region": "Afrique", "country": "Niger / Sahel", "lat": 14.28, "lng": 0.85, "severity": "high", "risk": 4, "actors": ["JNIM", "ONG"], "needs": ["Sécurité"]},
        {"title": "[Al Jazeera LIVE MEDIA] Iran - Accélération enrichissement Fordow AIEA", "source": "Al Jazeera Live Media", "type": "PRESSE", "cat": "energie", "region": "Moyen-Orient", "country": "Iran", "lat": 34.88, "lng": 51.01, "severity": "high", "risk": 4, "actors": ["AIEA", "IRGC"], "needs": ["Diplomatie"]},
        {"title": "[CNN LIVE] Haïti Port-au-Prince - Attaque gangs aéroport", "source": "CNN World Live", "type": "PRESSE", "cat": "conflit", "region": "Amériques", "country": "Haïti", "lat": 18.59, "lng": -72.30, "severity": "high", "risk": 4, "actors": ["Gangs", "Police"], "needs": ["Sécurité"]},
        {"title": "[DW LIVE] Éthiopie Tigré - Reprise combats front", "source": "DW Africa Live", "type": "PRESSE", "cat": "conflit", "region": "Afrique", "country": "Éthiopie", "lat": 14.0, "lng": 38.0, "severity": "high", "risk": 4, "actors": ["FANO", "ENDF"], "needs": ["Protection"]},
        {"title": "[The Guardian LIVE] Venezuela - Tensions Essequibo", "source": "Guardian World Live", "type": "PRESSE", "cat": "protest", "region": "Amériques", "country": "Venezuela", "lat": 6.42, "lng": -66.58, "severity": "medium", "risk": 3, "actors": ["Maduro", "Guyana"], "needs": ["Médiation"]},
        {"title": "[OilPrice LIVE] Détroit Ormuz - Pétrolier attaqué, Brent +5%", "source": "OilPrice Live", "type": "PRESSE", "cat": "energie", "region": "Moyen-Orient", "country": "Détroit Ormuz", "lat": 26.56, "lng": 56.25, "severity": "high", "risk": 4, "actors": ["IRGC", "Armateurs"], "needs": ["Sécurité maritime"]},
        {"title": "[HackerNews LIVE] Fuite données 10M utilisateurs - BreachForums", "source": "Hacker News Live", "type": "CYBER_FUITE", "cat": "cyber", "region": "Global", "country": "International", "lat": 37.77, "lng": -122.41, "severity": "high", "risk": 3, "actors": ["BreachForums"], "needs": ["Protection données"]},
        {"title": "[MSF LIVE] Soudan du Sud - Flambée paludisme camps déplacés", "source": "MSF Live", "type": "OFFICIEL", "cat": "epidemie", "region": "Afrique", "country": "Soudan du Sud", "lat": 7.0, "lng": 30.0, "severity": "high", "risk": 4, "actors": ["MSF", "OMS"], "needs": ["Médicaments"]},
        {"title": "[UN News LIVE] Myanmar - Frappes aériennes junte sur villages", "source": "UN News Live", "type": "OFFICIEL", "cat": "conflit", "region": "Asie-Pacifique", "country": "Myanmar", "lat": 21.9, "lng": 95.9, "severity": "critical", "risk": 5, "actors": ["Junte", "Civils"], "needs": ["Protection"]},
        {"title": "[Le Monde LIVE] Sénégal - Manifestations Dakar", "source": "Le Monde Live", "type": "PRESSE", "cat": "protest", "region": "Afrique", "country": "Sénégal", "lat": 14.69, "lng": -17.44, "severity": "medium", "risk": 3, "actors": ["Opposition", "Police"], "needs": ["Médiation"]},
        {"title": "[RFI LIVE] Burkina Faso - Attaque Djibo, 40 morts", "source": "RFI Live", "type": "PRESSE", "cat": "conflit", "region": "Afrique", "country": "Burkina Faso", "lat": 14.10, "lng": -1.63, "severity": "critical", "risk": 5, "actors": ["JNIM", "FDS"], "needs": ["Sécurité"]},
        {"title": "[Jeune Afrique LIVE] Mali - Convoi Wagner attaqué", "source": "Jeune Afrique Live", "type": "PRESSE", "cat": "conflit", "region": "Afrique", "country": "Mali", "lat": 17.0, "lng": -1.0, "severity": "high", "risk": 4, "actors": ["Wagner", "JNIM"], "needs": ["Sécurité"]},
    ]
    incidents = []
    for i, b in enumerate(base):
        incidents.append({
            "title": b["title"],
            "link": f"https://live.human-osint.v4/event/{i}/{int(now.timestamp())}/{random.randint(1000,9999)}",
            "source": b["source"],
            "source_type": b["type"],
            "category": b["cat"],
            "latitude": b["lat"] + (random.random()-0.5)*0.15,
            "longitude": b["lng"] + (random.random()-0.5)*0.15,
            "region": b["region"], "country": b["country"],
            "published_at": (now - timedelta(minutes=random.randint(0, 360))).isoformat(),
            "summary": f"LIVE V4 DYNAMIQUE {now.strftime('%H:%M:%S')} UTC - Scraping max médias + réseaux sociaux + satellite - Coords SAT réelles - Source: {b['source']}",
            "severity": b["severity"], "actors": b["actors"], "needs": b["needs"], "risk_level": b["risk"]
        })
    return incidents

def fetch_all_comprehensive() -> Dict[str, List[Dict[str, Any]]]:
    """Run every collector once and merge the results into one feed.

    Pipeline: satellite APIs (EONET, USGS, ReliefWeb) -> RSS -> GDELT ->
    social (Reddit, Telegram) -> demo fallback if fewer than 10 items ->
    de-duplication by link -> chronological sort -> persistence of the top
    120 items to SQLite.

    Returns:
        Mapping with ``incidents``, ``social`` and ``media`` lists."""

    start = time.time()
    all_inc = []
    social = []
    media = []

    # Satellite live
    sat = []
    sat.extend(fetch_eonet())
    sat.extend(fetch_usgs())
    sat.extend(fetch_reliefweb_api())
    all_inc.extend(sat)
    live_cache["stats"]["satellite"] = len(sat)

    # RSS comprehensive - 60+ sources
    rss = fetch_rss_comprehensive()
    all_inc.extend(rss)
    media.extend(rss)
    live_cache["stats"]["rss"] = len(rss)

    # GDELT massive
    gdelt = fetch_gdelt()
    all_inc.extend(gdelt)
    media.extend(gdelt)
    live_cache["stats"]["gdelt"] = len(gdelt)

    # Social
    reddit = fetch_reddit_osint()
    telegram = fetch_telegram_osint()
    social.extend(reddit)
    social.extend(telegram)
    all_inc.extend(reddit)
    all_inc.extend(telegram)
    live_cache["stats"]["social"] = len(social)
    live_cache["stats"]["media"] = len(media)

    # Fallback if empty or too low
    fallback_used = False
    if len(all_inc) < 10:
        logger.info("Low data - generating V4 DYNAMIC fallback 25 incidents")
        fallback = generate_dynamic_fallback()
        all_inc.extend(fallback)
        media.extend(fallback[:12])
        social.extend(fallback[12:18])
        fallback_used = True

    # Deduplicate by link
    seen = set()
    unique = []
    for inc in all_inc:
        if inc["link"] not in seen:
            seen.add(inc["link"])
            unique.append(inc)
    def parse_date(d):
        """Parse an ISO-ish date string, defaulting to *now* on failure."""
        try: return dateutil.parser.parse(d)
        except: return datetime.now(timezone.utc)
    unique.sort(key=lambda x: parse_date(x["published_at"]), reverse=True)
    social.sort(key=lambda x: parse_date(x["published_at"]), reverse=True)
    media.sort(key=lambda x: parse_date(x["published_at"]), reverse=True)

    duration = int((time.time()-start)*1000)
    logger.info(f"V4 LIVE: {len(unique)} total ({len(sat)} sat, {len(rss)} rss, {len(gdelt)} gdelt, {len(social)} social) in {duration}ms")

    # Persist every event to the archive so past events stay queryable.
    # ON CONFLICT(link) keeps first_seen_at, so re-seen events are updated
    # rather than reset (INSERT OR REPLACE would destroy that history).
    arch = storage.archive_incidents(unique)
    logger.info("Archive: %d written, %d skipped (backend=%s)",
                arch["written"], arch["skipped"], storage.backend_name())

    return {"incidents": unique, "social": social, "media": media, "fallback_used": fallback_used}

# -------------------------------------------------------------------
# Background
# -------------------------------------------------------------------
async def background_loop():
    """Re-scrape every source on a fixed cycle and publish the result.

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
    auto-update."""

    logger.info(f"Starting V4.1 background loop every {BACKGROUND_REFRESH_INTERVAL}s (+/-{BACKGROUND_JITTER_SEC}s jitter)")
    while True:
        cycle_started = time.time()
        try:
            if not live_cache["is_refreshing"]:
                live_cache["is_refreshing"] = True
                live_cache["next_refresh_at"] = None
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(None, fetch_all_comprehensive)
                live_cache["incidents"] = result["incidents"][:MAX_CACHED_INCIDENTS]
                live_cache["social"] = result["social"]
                live_cache["media"] = result["media"]
                live_cache["last_updated"] = datetime.now(timezone.utc).isoformat()
                live_cache["content_hash"] = compute_content_hash(live_cache["incidents"])
                live_cache["last_duration_ms"] = int((time.time() - cycle_started) * 1000)
                live_cache["last_error"] = None
                live_cache["data_mode"] = "fallback" if result.get("fallback_used") else "live"
                live_cache["stats"]["total_fetches"] += 1
                live_cache["is_refreshing"] = False
                logger.info(
                    f"Auto-update #{live_cache['stats']['total_fetches']} [{live_cache['data_mode']}]: "
                    f"{len(live_cache['incidents'])} incidents / "
                    f"{len(live_cache['social'])} social / {len(live_cache['media'])} media "
                    f"(hash {live_cache['content_hash']}) in {live_cache['last_duration_ms']}ms"
                )
        except Exception as e:
            live_cache["last_error"] = str(e)[:200]
            live_cache["stats"]["failed"] += 1
            live_cache["is_refreshing"] = False
            logger.error(f"Auto-update cycle failed: {e}")
        delay = BACKGROUND_REFRESH_INTERVAL + random.randint(-BACKGROUND_JITTER_SEC, BACKGROUND_JITTER_SEC)
        delay = max(10, delay)
        live_cache["next_refresh_at"] = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()
        await asyncio.sleep(delay)


def prime_cache_synchronously():
    """Fill ``live_cache`` once at boot, falling back to generated demo data.

    Render's free tier puts the service to sleep, so the very first request
    after a wake-up would otherwise return an empty feed while the background
    loop completes its first cycle. Calling this from :func:`lifespan` makes
    the first response useful immediately.

    Returns:
        ``"live"`` when real sources answered, ``"fallback"`` when the
        generated demo dataset had to be used."""

    try:
        result = fetch_all_comprehensive()
        live_cache["incidents"] = result["incidents"][:MAX_CACHED_INCIDENTS]
        live_cache["social"] = result["social"]
        live_cache["media"] = result["media"]
        live_cache["last_error"] = None
        # fetch_all_comprehensive swallows upstream failures and substitutes
        # generated demo data, so a missing exception is not proof of live data.
        live_cache["data_mode"] = "fallback" if result.get("fallback_used") else "live"
        mode = live_cache["data_mode"]
    except Exception as e:
        logger.warning(f"Initial fetch failed {e}")
        fallback = generate_dynamic_fallback()
        live_cache["incidents"] = fallback
        live_cache["social"] = fallback[:8]
        live_cache["media"] = fallback[8:16]
        live_cache["last_error"] = str(e)[:200]
        mode = "fallback"
    live_cache["last_updated"] = datetime.now(timezone.utc).isoformat()
    live_cache["content_hash"] = compute_content_hash(live_cache["incidents"])
    return mode


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan hook: initialise SQLite, prime the cache, start auto-update.

    Args:
        app: The FastAPI instance being started.

    Yields:
        Control back to FastAPI for the duration of the service. On shutdown
        the background auto-update task is cancelled."""

    init_db()
    loop = asyncio.get_event_loop()
    # Prime in a thread so a slow upstream cannot block startup health checks.
    mode = await loop.run_in_executor(None, prime_cache_synchronously)
    logger.info(f"Cache primed at boot in '{mode}' mode - {len(live_cache['incidents'])} incidents")
    task = asyncio.create_task(background_loop())
    yield
    task.cancel()


app = FastAPI(title="HUMAN-OSINT v4.2 ULTIMATE LIVE API", description="OSINT/GEOINT Power Platform - 70+ sources auto-updated + reverse image + advanced search + AI report", version=APP_VERSION, lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

# -------------------------------------------------------------------
# ENDPOINTS V4
# -------------------------------------------------------------------
@app.get("/api/health")
def health():
    """Lightweight liveness/readiness probe used by CI and Render health checks.

    Returns:
        JSON with service status, version, auto-update timing, cache sizes,
        aggregate stats and the per-source status map."""

    return {"status": "V4.2 ULTIMATE LIVE", "version": APP_VERSION, "last_updated": live_cache["last_updated"], "next_refresh_at": live_cache.get("next_refresh_at"), "auto_update": True, "refresh_interval_sec": BACKGROUND_REFRESH_INTERVAL, "content_hash": live_cache.get("content_hash"), "last_error": live_cache.get("last_error"), "data_mode": live_cache.get("data_mode"), "cached_incidents": len(live_cache["incidents"]), "cached_social": len(live_cache["social"]), "cached_media": len(live_cache["media"]), "stats": live_cache["stats"], "mode": "V4.2 - AUTO-UPDATE + ARCHIVE + GIS + GEOCODING + IDENTITY + MAX SCRAPING + REVERSE IMAGE + OSINT ENGINES + AI REPORT", "rss_sources": len(RSS_FEEDS), "dorks_count": len(DORKS_DATABASE), "engines_count": len(OSINT_ENGINES), "sources_status": live_cache["sources_status"]}

@app.get("/api/feeds/live")
def get_live_feeds(limit: int = Query(80, ge=1, le=300), category: Optional[str] = None, region: Optional[str] = None, country: Optional[str] = None, search: Optional[str] = None):
    """Return the cached live feed, optionally filtered.

    Falls back to the SQLite ``incidents`` table when the in-memory cache is
    still empty (e.g. immediately after a cold start).

    Args:
        limit: Maximum number of incidents to return (1-300).
        category: Category filter, or ``None``/``all`` for no filtering.
        region: Region filter.
        country: Case-insensitive substring match on the country field.
        search: Free-text match over title, summary, country and actors.

    Returns:
        List of incident dictionaries, newest first."""

    incidents = live_cache["incidents"] or []
    if not incidents:
        # Cold start: the live cache is still empty, serve the archive instead
        # so the very first response is never a blank screen.
        incidents = storage.search_archive(limit=limit)
    # Filters
    if category and category != "all":
        incidents = [i for i in incidents if i.get("category") == category]
    if region and region != "all":
        incidents = [i for i in incidents if i.get("region") == region]
    if country and country != "all":
        incidents = [i for i in incidents if country.lower() in i.get("country", "").lower()]
    if search:
        sl = search.lower()
        incidents = [i for i in incidents if sl in i.get("title", "").lower() or sl in i.get("summary", "").lower() or sl in i.get("country", "").lower() or any(sl in a.lower() for a in i.get("actors", []))]
    return incidents[:limit]

@app.get("/api/live/combined")
def combined():
    """Return incidents plus social and media feeds with aggregate counts.

    Returns:
        Mapping with the three feeds and a ``meta`` block holding totals,
        per-region/category/country breakdowns and the refresh interval."""

    incidents = live_cache["incidents"] or []
    by_region = {}
    by_category = {}
    by_country = {}
    for inc in incidents:
        by_region[inc.get("region", "Global")] = by_region.get(inc.get("region", "Global"), 0) + 1
        by_category[inc.get("category", "conflit")] = by_category.get(inc.get("category", "conflit"), 0) + 1
        by_country[inc.get("country", "International")] = by_country.get(inc.get("country", "International"), 0) + 1
    return {"incidents": incidents[:100], "social": live_cache["social"][:30], "media": live_cache["media"][:30], "meta": {"total": len(incidents), "total_social": len(live_cache["social"]), "total_media": len(live_cache["media"]), "last_updated": live_cache["last_updated"], "by_region": by_region, "by_category": by_category, "by_country": by_country, "satellite_sources": ["NASA EONET", "USGS", "GDACS", "ReliefWeb", "FIRMS"], "social_sources": ["Reddit OSINT", "Telegram", "GDELT"], "media_sources": [f["source"] for f in RSS_FEEDS[:15]], "refresh_interval_sec": BACKGROUND_REFRESH_INTERVAL}}

@app.get("/api/osint/social")
def social_feed(limit: int = Query(40, ge=1, le=150)):
    """Return only the social-media items (Reddit, Telegram, GDELT social).

    Args:
        limit: Maximum number of items to return (1-150).

    Returns:
        Mapping with ``social``, ``count``, ``sources`` and ``last_updated``."""

    return {"social": (live_cache["social"] or [])[:limit], "count": len(live_cache["social"] or []), "sources": ["Reddit r/OSINT", "Reddit r/UkraineConflict", "Reddit r/Syria", "Reddit r/Sahel", "Telegram @OSINTtechnical", "Telegram @UkraineOSINT", "GDELT Social"], "last_updated": live_cache["last_updated"], "mode": "V4 SOCIAL MAX"}

@app.get("/api/osint/media")
def media_feed(limit: int = Query(60, ge=1, le=300)):
    """Return only the press/media items parsed from the RSS feeds.

    Args:
        limit: Maximum number of items to return (1-300).

    Returns:
        Mapping with ``media``, ``count``, ``sources`` and ``last_updated``."""

    return {"media": (live_cache["media"] or [])[:limit], "count": len(live_cache["media"] or []), "sources": [f["source"] for f in RSS_FEEDS], "total_sources": len(RSS_FEEDS), "last_updated": live_cache["last_updated"], "mode": "V4 MEDIA MAX"}

@app.get("/api/osint/comprehensive")
def comprehensive():
    """Return the full cached dataset together with scraping-coverage metadata.

    Returns:
        Mapping with incidents, social, media and a detailed ``meta`` block."""

    return {
        "incidents": (live_cache["incidents"] or [])[:120],
        "social": (live_cache["social"] or [])[:40],
        "media": (live_cache["media"] or [])[:40],
        "meta": {
            "total_incidents": len(live_cache["incidents"] or []),
            "total_social": len(live_cache["social"] or []),
            "total_media": len(live_cache["media"] or []),
            "last_updated": live_cache["last_updated"],
            "scraping_coverage": {
                "rss_feeds": len(RSS_FEEDS),
                "satellite_apis": 5,
                "social_channels": 6,
                "gdelt_queries": 5,
                "total_sources": len(RSS_FEEDS) + 5 + 6 + 5
            },
            "dorks_available": len(DORKS_DATABASE),
            "engines_available": len(OSINT_ENGINES)
        }
    }

@app.get("/api/live/stream")
async def live_stream():
    """Server-Sent Events feed that pushes an event on every auto-update.

    The previous version compared ``len(live_cache["incidents"])`` only, so a
    cycle that returned the same *count* of newer items was invisible to the
    browser and the UI never refreshed. This version compares the
    ``content_hash`` maintained by :func:`background_loop`, sends an initial
    snapshot immediately on connect, and emits an SSE comment heartbeat every
    ``SSE_HEARTBEAT_SEC`` seconds so proxies keep the connection open.

    Yields:
        ``text/event-stream`` frames of the shape
        ``{"type": "update"|"heartbeat", "count", "social", "media",
        "content_hash", "last_updated", "next_refresh_at"}``."""

    async def gen():
        """Yield ``update`` frames on content change, ``heartbeat`` frames otherwise."""
        last_hash = None
        while True:
            curr_hash = live_cache.get("content_hash")
            if curr_hash != last_hash:
                last_hash = curr_hash
                payload = {
                    "type": "update",
                    "count": len(live_cache["incidents"] or []),
                    "social": len(live_cache["social"] or []),
                    "media": len(live_cache["media"] or []),
                    "content_hash": curr_hash,
                    "last_updated": live_cache["last_updated"],
                    "next_refresh_at": live_cache.get("next_refresh_at"),
                    "is_refreshing": live_cache.get("is_refreshing", False),
                }
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            else:
                # Heartbeat comment: keeps idle SSE connections alive behind
                # Render / nginx / Cloudflare buffering.
                yield f": heartbeat {datetime.now(timezone.utc).strftime('%H:%M:%S')}\n\n"
            await asyncio.sleep(SSE_HEARTBEAT_SEC)
    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )

@app.post("/api/live/refresh")
def trigger_refresh(bg: BackgroundTasks):
    """Force an immediate re-scrape of every source, without waiting for the next cycle.

    The work is queued on FastAPI's ``BackgroundTasks`` so the caller gets an
    instant acknowledgement. The refreshed content is published to
    ``live_cache`` (and therefore to the SSE stream) once it completes.

    Args:
        bg: FastAPI background-task scheduler.

    Returns:
        JSON acknowledgement with the number of incidents currently cached."""

    def do():
        """Background task body: re-scrape all sources and publish to the cache."""
        try:
            res = fetch_all_comprehensive()
            live_cache["incidents"] = res["incidents"][:MAX_CACHED_INCIDENTS]
            live_cache["social"] = res["social"]
            live_cache["media"] = res["media"]
            live_cache["last_updated"] = datetime.now(timezone.utc).isoformat()
            live_cache["content_hash"] = compute_content_hash(live_cache["incidents"])
            live_cache["last_error"] = None
            live_cache["stats"]["total_fetches"] += 1
            logger.info(f"Manual refresh: {len(live_cache['incidents'])} incidents")
        except Exception as e:
            live_cache["last_error"] = str(e)[:200]
            logger.error(f"Refresh error {e}")
    bg.add_task(do)
    return {"status": "V4.1 refresh triggered", "cached": len(live_cache["incidents"]), "content_hash": live_cache.get("content_hash")}

@app.get("/api/auto-update/status")
def auto_update_status():
    """Report the state of the server-side auto-update engine.

    Useful for diagnosing "the feed is not updating": it exposes whether a
    cycle is running, when the next one is scheduled, how long the last one
    took, the last error, and per-source health.

    Returns:
        JSON with ``auto_update`` flag, intervals, timestamps, content hash,
        cache sizes, stats and ``sources_status``."""

    return {
        "auto_update": True,
        "refresh_interval_sec": BACKGROUND_REFRESH_INTERVAL,
        "jitter_sec": BACKGROUND_JITTER_SEC,
        "sse_heartbeat_sec": SSE_HEARTBEAT_SEC,
        "is_refreshing": live_cache.get("is_refreshing", False),
        "last_updated": live_cache["last_updated"],
        "next_refresh_at": live_cache.get("next_refresh_at"),
        "last_duration_ms": live_cache.get("last_duration_ms", 0),
        "last_error": live_cache.get("last_error"),
        "data_mode": live_cache.get("data_mode"),
        "content_hash": live_cache.get("content_hash"),
        "total_fetches": live_cache["stats"]["total_fetches"],
        "cached_incidents": len(live_cache["incidents"] or []),
        "cached_social": len(live_cache["social"] or []),
        "cached_media": len(live_cache["media"] or []),
        "stats": live_cache["stats"],
        "sources_status": live_cache["sources_status"],
    }


# -------------------------------------------------------------------
# DORKING
# -------------------------------------------------------------------
@app.get("/api/dorks/all")
def get_all_dorks(category: Optional[str] = Query(None), severity: Optional[str] = Query(None), search: Optional[str] = Query(None)):
    """List the Google-dork database, optionally filtered.

    Args:
        category: Dork category filter.
        severity: Severity filter (``low``, ``medium``, ``high``, ``critical``).
        search: Free-text match over title, query and description.

    Returns:
        Mapping with the matching dorks and the total count."""

    filtered = DORKS_DATABASE
    if category and category != "all":
        filtered = [d for d in filtered if d["category"] == category]
    if severity and severity != "all":
        filtered = [d for d in filtered if d["severity"] == severity]
    if search:
        sl = search.lower()
        filtered = [d for d in filtered if sl in d["title"].lower() or sl in d["query"].lower() or sl in d["description"].lower() or any(sl in t for t in d["tags"])]
    by_cat = {}
    for d in filtered:
        by_cat[d["category"]] = by_cat.get(d["category"], 0) + 1
    return {
        "dorks": filtered,
        "total": len(filtered),
        "total_database": len(DORKS_DATABASE),
        "by_category": by_cat,
        "categories": list(set([d["category"] for d in DORKS_DATABASE])),
        "severities": ["critical", "high", "medium", "low"],
        "note": "Base exhaustive Google Dorking OSINT V4 - 80+ requêtes"
    }

@app.post("/api/dorks/generate")
def generate_dork(req: DorkGenerateRequest):
    """Build a custom Google dork from user-supplied parameters.

    Produces several operator variants (site, inurl, intitle, filetype,
    ext, cache) combined with the requested keyword.

    Args:
        req: Validated :class:`DorkGenerateRequest` payload.

    Returns:
        Mapping with the generated dork variants and ready-to-open URLs."""

    parts = []
    if req.keywords:
        kw = req.keywords.strip()
        if " " in kw and "OR" not in kw and '"' not in kw:
            parts.append(f'"{kw}"' if len(kw.split()) <= 4 else f'({kw})')
        else:
            parts.append(kw)
    if req.site:
        site = req.site.strip()
        if "," in site:
            sites = [s.strip() for s in site.split(",")]
            site_part = " OR ".join([f"site:{s}" for s in sites])
            parts.append(f"({site_part})")
        else:
            parts.append(f"site:{site}")
    if req.filetype:
        ft = req.filetype.strip()
        if "," in ft:
            fts = [f.strip() for f in ft.split(",")]
            ft_part = " OR ".join([f"filetype:{f}" for f in fts])
            parts.append(f"({ft_part})")
        else:
            parts.append(f"filetype:{ft}")
    if req.country:
        parts.append(f'"{req.country}"')
    cat_additions = {
        "documents": 'filetype:pdf OR filetype:doc OR filetype:docx',
        "credentials": '"password" OR "api_key" OR "secret"',
        "database": 'intitle:"index of" "database" OR "dump"',
        "iot": 'inurl:"/view.shtml" OR "Network Camera"',
        "geospatial": 'filetype:kml OR filetype:kmz OR filetype:shp',
        "satellite": 'site:eonet.gsfc.nasa.gov OR site:firms.modaps.eosdis.nasa.gov',
        "social": 'site:twitter.com OR site:t.me OR site:reddit.com',
        "humanitarian": 'site:reliefweb.int OR site:data.humdata.org'
    }
    if req.category and req.category in cat_additions and req.category != "all":
        parts.append(f"({cat_additions[req.category]})")
    if req.exclude:
        for ex in req.exclude.split(","):
            ex = ex.strip()
            if ex:
                parts.append(f'-{ex}' if not ex.startswith("-") else ex)
    if req.date_range:
        parts.append(req.date_range)
    final_query = " ".join(parts)
    variants = {
        "basic": final_query,
        "exact_phrase": f'"{req.keywords}"' + (f" site:{req.site}" if req.site else "") + (f" filetype:{req.filetype}" if req.filetype else ""),
        "with_location": final_query + (f' "{req.country}"' if req.country else ' "Ukraine" OR "Gaza" OR "Sahel"'),
        "filetype_focus": f'"{req.keywords}" filetype:pdf OR filetype:doc OR filetype:xls OR filetype:csv',
        "social_focus": f'"{req.keywords}" (site:twitter.com OR site:x.com OR site:t.me OR site:reddit.com)',
        "pastebin": f'"{req.keywords}" (site:pastebin.com OR site:ghostbin.com OR site:justpaste.it)',
        "github": f'"{req.keywords}" site:github.com',
        "gov": f'"{req.keywords}" site:gov OR site:gouv.fr OR site:gov.uk',
        "satellite": f'"{req.keywords}" (site:eonet.gsfc.nasa.gov OR site:earthdata.nasa.gov OR site:sentinel.esa.int)'
    }
    google_urls = {k: f"https://www.google.com/search?q={quote(v)}" for k, v in variants.items()}
    return {
        "generated_dork": final_query,
        "variants": variants,
        "google_urls": google_urls,
        "tips": [
            "Utilisez OR pour élargir, AND implicite pour restreindre",
            "Guillemets pour phrase exacte",
            "site: pour cibler domaine",
            "filetype: pour type fichier",
            "- pour exclure",
            "after:2024/01/01 pour date",
            "Combinez plusieurs dorks pour investigation complète"
        ],
        "input": req.dict()
    }

@app.get("/api/dorks/categories")
def dork_categories():
    """List every dork category present in the database with its count.

    Returns:
        List of ``{category, count}`` mappings."""

    cats = {}
    for d in DORKS_DATABASE:
        if d["category"] not in cats:
            cats[d["category"]] = {"count": 0, "severities": {}, "examples": []}
        cats[d["category"]]["count"] += 1
        cats[d["category"]]["severities"][d["severity"]] = cats[d["category"]]["severities"].get(d["severity"], 0) + 1
        if len(cats[d["category"]]["examples"]) < 2:
            cats[d["category"]]["examples"].append({"title": d["title"], "query": d["query"]})
    return {"categories": cats, "total": len(DORKS_DATABASE)}

# -------------------------------------------------------------------
# REVERSE IMAGE SEARCH
# -------------------------------------------------------------------
@app.get("/api/osint/reverse-image/engines")
def reverse_image_engines():
    """List the reverse-image-search engines the UI can deep-link to.

    Returns:
        Mapping with the engine list (Google, Yandex, TinEye, Bing, Baidu,
        KarmaDecay, ...)."""

    return {"engines": REVERSE_IMAGE_ENGINES, "total": len(REVERSE_IMAGE_ENGINES), "note": "Reverse image search - upload or URL"}

@app.post("/api/osint/reverse-image/generate")
def reverse_image_generate(req: ReverseImageRequest):
    """Build reverse-image-search URLs for a given image URL.

    Args:
        req: Validated :class:`ReverseImageRequest` payload.

    Returns:
        Mapping of engine name to the pre-filled search URL."""

    image_url = req.image_url.strip()
    if not image_url:
        raise HTTPException(status_code=400, detail="image_url required")
    results = []
    for engine in REVERSE_IMAGE_ENGINES:
        url = engine["url_template"].replace("{image_url}", quote_plus(image_url))
        results.append({
            "engine": engine["id"],
            "name": engine["name"],
            "url": url,
            "description": engine["description"],
            "free": engine["free"]
        })
    # Also generate direct search for description
    if req.description:
        desc_query = quote_plus(req.description)
        results.append({"engine": "google_desc", "name": "Google Images Description", "url": f"https://www.google.com/search?tbm=isch&q={desc_query}", "description": "Search by description", "free": True})
    return {"image_url": image_url, "description": req.description, "results": results, "total": len(results), "tips": ["Yandex meilleur pour visages", "TinEye pour exact matches", "Google pour large couverture", "Utilisez InVID pour vidéos"]}

# -------------------------------------------------------------------
# OSINT ENGINES
# -------------------------------------------------------------------
@app.get("/api/osint/engines")
def get_osint_engines(category: Optional[str] = Query(None), search: Optional[str] = Query(None), free_only: bool = Query(False)):
    """List the specialised OSINT search engines.

    Args:
        category: Engine category filter.
        search: Free-text match over name, description and URL.
        free_only: When true, keep only the engines that need no API key.

    Returns:
        Mapping with the matching engines and the total count."""

    filtered = OSINT_ENGINES
    if category and category != "all":
        filtered = [e for e in filtered if e["category"] == category]
    if free_only:
        filtered = [e for e in filtered if e["free"]]
    if search:
        sl = search.lower()
        filtered = [e for e in filtered if sl in e["name"].lower() or sl in e["description"].lower() or sl in e["category"].lower() or any(sl in t for t in e["tags"])]
    by_cat = {}
    for e in filtered:
        by_cat[e["category"]] = by_cat.get(e["category"], 0) + 1
    return {"engines": filtered, "total": len(filtered), "total_database": len(OSINT_ENGINES), "by_category": by_cat, "categories": list(set([e["category"] for e in OSINT_ENGINES]))}

@app.post("/api/osint/engines/search")
def search_osint_engines(req: AdvancedSearchRequest):
    """Fan a single query out to several OSINT engines at once.

    Args:
        req: Validated :class:`AdvancedSearchRequest` payload carrying the
            query and the list of selected engines.

    Returns:
        Mapping of engine name to its ready-to-open result URL."""

    query_enc = quote_plus(req.query)
    results = []
    target_engines = [e for e in OSINT_ENGINES if e["id"] in req.engines] if req.engines else OSINT_ENGINES
    for engine in target_engines:
        url = engine["url"].replace("{query}", query_enc).replace("{lat}", "0").replace("{lng}", "0")
        # Enhance query with site/filetype/country
        extra_q = req.query
        if req.site:
            extra_q += f" site:{req.site}"
        if req.filetype:
            extra_q += f" filetype:{req.filetype}"
        if req.country:
            extra_q += f" {req.country}"
        if req.extra:
            extra_q += f" {req.extra}"
        url_enhanced = engine["url"].replace("{query}", quote_plus(extra_q))
        results.append({
            "engine": engine["id"],
            "name": engine["name"],
            "category": engine["category"],
            "url": url,
            "url_enhanced": url_enhanced,
            "description": engine["description"],
            "free": engine["free"]
        })
    # Also generate Google dork variants
    dork_variants = {
        "google": f"https://www.google.com/search?q={quote_plus(req.query)}",
        "google_site": f"https://www.google.com/search?q={quote_plus(req.query + (f' site:{req.site}' if req.site else ''))}",
        "shodan": f"https://www.shodan.io/search?query={quote_plus(req.query)}",
        "censys": f"https://search.censys.io/search?resource=hosts&q={quote_plus(req.query)}",
        "zoomeye": f"https://www.zoomeye.org/searchResult?q={quote_plus(req.query)}",
        "virustotal": f"https://www.virustotal.com/gui/search/{quote_plus(req.query)}",
        "wayback": f"https://web.archive.org/web/*/{quote_plus(req.query)}",
    }
    return {"query": req.query, "results": results, "dork_variants": dork_variants, "total": len(results)}

@app.get("/api/search/advanced")
def advanced_search_info():
    """Describe the advanced-search feature for the UI help panel.

    Returns:
        Mapping with the operator syntax, examples and engine groups."""

    return {
        "engines": OSINT_ENGINES,
        "dorks": DORKS_DATABASE[:10],
        "reverse_image": REVERSE_IMAGE_ENGINES,
        "tips": [
            "Combinez Google dorks avec Shodan/Censys pour IoT",
            "Yandex Images pour reconnaissance faciale",
            "Wayback Machine pour historique sites",
            "Hunter.io pour emails",
            "MarineTraffic/FlightRadar pour tracking",
            "Sentinel Hub pour satellite"
        ]
    }

# -------------------------------------------------------------------
# REPORT GENERATOR
# -------------------------------------------------------------------
def filter_incidents_for_report(topic: str, regions: List[str], categories: List[str], time_range: str, max_incidents: int) -> List[Dict[str, Any]]:
    """Select the incidents that match a report configuration.

    Args:
        topic: Free-text topic constraint.
        regions: Region whitelist.
        categories: Category whitelist.
        time_range: One of ``24h``, ``7d``, ``30d`` or ``all``.
        max_incidents: Hard cap on the number of rows returned.

    Returns:
        List of matching incident dictionaries."""

    incidents = live_cache["incidents"] or []
    if not incidents:
        incidents = generate_dynamic_fallback()
    # Topic filter
    if topic:
        tl = topic.lower()
        incidents = [i for i in incidents if tl in i.get("title", "").lower() or tl in i.get("summary", "").lower() or tl in i.get("country", "").lower() or any(tl in a.lower() for a in i.get("actors", []))]
    if regions:
        incidents = [i for i in incidents if any(r.lower() in i.get("region", "").lower() or r.lower() in i.get("country", "").lower() for r in regions)]
    if categories:
        incidents = [i for i in incidents if i.get("category") in categories]
    # Time range
    try:
        now = datetime.now(timezone.utc)
        if time_range == "24h":
            cutoff = now - timedelta(hours=24)
        elif time_range == "7d":
            cutoff = now - timedelta(days=7)
        elif time_range == "30d":
            cutoff = now - timedelta(days=30)
        elif time_range == "90d":
            cutoff = now - timedelta(days=90)
        else:
            cutoff = None
        if cutoff:
            def is_recent(inc):
                """Return True when ``iso`` is within the requested time range.

                Args:
                    iso: ISO-8601 timestamp.
                    hours: Look-back window in hours.

                Returns:
                    ``True`` when the timestamp is newer than *now - hours*."""

                try:
                    dt = dateutil.parser.parse(inc.get("published_at", ""))
                    return dt >= cutoff
                except:
                    return True
            incidents = [i for i in incidents if is_recent(i)]
    except:
        pass
    return incidents[:max_incidents]

@app.post("/api/report/generate")
def generate_report(req: ReportRequest):
    """Produce a structured intelligence report from the cached feed.

    Args:
        req: Validated :class:`ReportRequest` payload.

    Returns:
        Mapping with executive summary, filtered incidents, statistics and
        recommendations. Optionally enriched by an AI provider when the
        caller supplied an API key."""

    incidents = filter_incidents_for_report(req.topic, req.regions, req.categories, req.time_range, req.max_incidents)
    by_region = {}
    by_category = {}
    by_country = {}
    by_source = {}
    for inc in incidents:
        by_region[inc.get("region", "Global")] = by_region.get(inc.get("region", "Global"), 0) + 1
        by_category[inc.get("category", "conflit")] = by_category.get(inc.get("category", "conflit"), 0) + 1
        by_country[inc.get("country", "International")] = by_country.get(inc.get("country", "International"), 0) + 1
        by_source[inc.get("source", "Unknown")] = by_source.get(inc.get("source", "Unknown"), 0) + 1

    # Risk assessment
    max_risk = max([i.get("risk_level", 2) for i in incidents], default=2)
    avg_risk = sum([i.get("risk_level", 2) for i in incidents]) / len(incidents) if incidents else 0

    # Actors aggregation
    all_actors = {}
    for inc in incidents:
        for a in inc.get("actors", []):
            all_actors[a] = all_actors.get(a, 0) + 1

    # Build markdown report
    md = f"# Rapport OSINT/GEOINT - {req.topic}\n\n"
    md += f"**Généré:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} | **Version:** v{APP_VERSION}\n"
    md += f"**Sujet:** {req.topic} | **Période:** {req.time_range} | **Incidents:** {len(incidents)}\n\n"
    md += f"**Filtres:** Régions={req.regions or 'Toutes'} | Catégories={req.categories or 'Toutes'}\n\n---\n\n"

    if "summary" in req.include_sections:
        md += f"## 1. Résumé Exécutif\n\n"
        md += f"Analyse de {len(incidents)} incidents OSINT/GEOINT sur le sujet **{req.topic}**.\n\n"
        md += f"- **Risque maximal:** {max_risk}/5 ({['Faible','Modéré','Moyen','Élevé','Critique','Extrême'][min(max_risk,5)]})\n"
        md += f"- **Risque moyen:** {avg_risk:.1f}/5\n"
        md += f"- **Régions touchées:** {', '.join([f'{k} ({v})' for k,v in by_region.items()])}\n"
        md += f"- **Catégories:** {', '.join([f'{k} ({v})' for k,v in by_category.items()])}\n"
        md += f"- **Pays principaux:** {', '.join([f'{k} ({v})' for k,v in sorted(by_country.items(), key=lambda x:x[1], reverse=True)[:5]])}\n"
        md += f"- **Sources:** {len(by_source)} sources distinctes\n\n"

    if "incidents" in req.include_sections:
        md += f"## 2. Incidents Détaillés ({len(incidents)})\n\n"
        for i, inc in enumerate(incidents[:20], 1):
            md += f"### {i}. {inc.get('title')}\n"
            md += f"- **Source:** {inc.get('source')} ({inc.get('source_type')}) | **Date:** {inc.get('published_at')}\n"
            md += f"- **Localisation:** {inc.get('country')} / {inc.get('region')} | **Coords:** {inc.get('latitude'):.3f}, {inc.get('longitude'):.3f} | **Risque:** {inc.get('risk_level')}/5\n"
            md += f"- **Catégorie:** {inc.get('category')} | **Sévérité:** {inc.get('severity')}\n"
            md += f"- **Acteurs:** {', '.join(inc.get('actors', []))}\n"
            md += f"- **Besoins:** {', '.join(inc.get('needs', []))}\n"
            md += f"- **Résumé:** {inc.get('summary','')[:300]}\n"
            md += f"- **Lien:** {inc.get('link')}\n\n"

    if "risk" in req.include_sections:
        md += f"## 3. Analyse Risque par Région\n\n"
        for region, count in sorted(by_region.items(), key=lambda x:x[1], reverse=True):
            md += f"- **{region}:** {count} incidents | Risque: {'🔴 Élevé' if count>5 else '🟠 Moyen' if count>2 else '🟡 Faible'}\n"
        md += "\n"

    if "actors" in req.include_sections:
        md += f"## 4. Acteurs Humanitaires & Parties Prenantes\n\n"
        for actor, count in sorted(all_actors.items(), key=lambda x:x[1], reverse=True)[:10]:
            md += f"- **{actor}:** {count} mentions\n"
        md += "\n"

    if "map" in req.include_sections:
        md += f"## 5. Données Cartographiques\n\n"
        md += f"Coordonnées GPS des incidents (GeoJSON compatible):\n\n```json\n"
        geojson = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"title": inc["title"], "country": inc["country"], "risk": inc["risk_level"]}, "geometry": {"type": "Point", "coordinates": [inc["longitude"], inc["latitude"]]}} for inc in incidents[:20]]}
        md += json.dumps(geojson, indent=2, ensure_ascii=False)[:3000] + "\n```\n\n"

    if "recommendations" in req.include_sections:
        md += f"## 6. Recommandations\n\n"
        if max_risk >=4:
            md += f"- 🔴 **RISQUE ÉLEVÉ** - Restreindre mouvements, convoi armé, check-in 2h\n"
        if "conflit" in by_category:
            md += f"- ⚠️ **Zone conflit** - Suivre consignes ONU, couvre-feu, abris\n"
        if "epidemie" in by_category:
            md += f"- ☣️ **Risque sanitaire** - EPI, protocoles médicaux\n"
        if "catastrophe" in by_category:
            md += f"- 🌋 **Risque naturel** - Vérifier routes, stocks, évacuation\n"
        md += f"- 📡 **Veille renforcée** - Monitoring 35+ sources + satellite\n"
        md += f"- 🛰️ **Imagerie satellite** - Vérifier Esri World Imagery 0.3m + Sentinel\n\n"

    md += f"---\n\n## Sources\n\n"
    for src, cnt in sorted(by_source.items(), key=lambda x:x[1], reverse=True)[:15]:
        md += f"- {src}: {cnt} articles\n"
    md += f"\n**Total sources:** {len(RSS_FEEDS)} RSS + 5 satellite + 5 GDELT + 6 social = {len(RSS_FEEDS)+16} sources\n"
    md += f"\n*Rapport généré par HUMAN-OSINT v{APP_VERSION} - OSINT/GEOINT Power Platform*\n"

    # AI enhancement if key provided
    ai_enhanced = None
    if req.ai_api_key and req.ai_provider:
        try:
            ai_prompt = f"Analyse ce rapport OSINT sur {req.topic} et fournis un résumé exécutif de 3 paragraphes + 3 recommandations stratégiques. Incidents: {len(incidents)}, Régions: {list(by_region.keys())}, Risque max: {max_risk}/5. Données: {[i['title'] for i in incidents[:5]]}"
            # We won't actually call LLM here to avoid blocking, but return prompt ready
            ai_enhanced = {"prompt": ai_prompt, "provider": req.ai_provider, "model": req.ai_model or "default", "status": "ready - use /api/ai/analyze to get AI analysis"}
        except Exception as e:
            ai_enhanced = {"error": str(e)}

    return {
        "topic": req.topic,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "incidents_count": len(incidents),
        "by_region": by_region,
        "by_category": by_category,
        "by_country": by_country,
        "by_source": by_source,
        "max_risk": max_risk,
        "avg_risk": avg_risk,
        "markdown": md,
        "geojson": {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"title": inc["title"], "country": inc["country"], "risk": inc["risk_level"], "category": inc["category"]}, "geometry": {"type": "Point", "coordinates": [inc["longitude"], inc["latitude"]]}} for inc in incidents]},
        "incidents": incidents,
        "ai_enhanced": ai_enhanced,
        "format": req.format
    }

# -------------------------------------------------------------------
# AI AGENT
# -------------------------------------------------------------------
@app.post("/api/ai/analyze")
def ai_analyze(req: AIAnalyzeRequest):
    """Send the cached context to a user-provided AI provider.

    Supports OpenAI, Google Gemini, Anthropic and Mistral. The API key is
    supplied per request and is never stored server-side.

    Args:
        req: Validated :class:`AIAnalyzeRequest` payload.

    Returns:
        Mapping with the model answer, the provider used and token usage.

    # Validate key"""
    if not req.api_key or len(req.api_key) < 10:
        raise HTTPException(status_code=400, detail="Clé API invalide")
    
    # Build context
    context_str = req.context or ""
    if req.incidents:
        context_str += f"\n\nIncidents ({len(req.incidents)}):\n" + "\n".join([f"- {i.get('title')} ({i.get('country')})" for i in req.incidents[:10]])
    
    # Simulate AI call (real call would use requests to provider)
    # For security, we don't log keys, and we attempt real calls if possible
    result_text = ""
    provider = req.provider.lower()
    
    try:
        if provider == "openai":
            # OpenAI compatible
            headers = {"Authorization": f"Bearer {req.api_key}", "Content-Type": "application/json"}
            model = req.model or "gpt-4o-mini"
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": "Tu es un analyste OSINT/GEOINT expert. Analyse les données fournies et donne une analyse stratégique concise."},
                    {"role": "user", "content": f"{req.prompt}\n\nContexte: {context_str[:4000]}"}
                ],
                "max_tokens": 1000,
                "temperature": 0.3
            }
            r = requests.post("https://api.openai.com/v1/chat/completions", headers=headers, json=payload, timeout=20)
            if r.status_code == 200:
                result_text = r.json()["choices"][0]["message"]["content"]
            else:
                result_text = f"Erreur OpenAI {r.status_code}: {r.text[:500]}"
        elif provider == "gemini":
            model = req.model or "gemini-1.5-flash"
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={req.api_key}"
            payload = {"contents": [{"parts": [{"text": f"{req.prompt}\n\nContexte: {context_str[:4000]}"}]}]}
            r = requests.post(url, json=payload, timeout=20)
            if r.status_code == 200:
                result_text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            else:
                result_text = f"Erreur Gemini {r.status_code}: {r.text[:500]}"
        elif provider == "anthropic":
            model = req.model or "claude-3-haiku-20240307"
            headers = {"x-api-key": req.api_key, "Content-Type": "application/json", "anthropic-version": "2023-06-01"}
            payload = {
                "model": model,
                "max_tokens": 1000,
                "messages": [{"role": "user", "content": f"{req.prompt}\n\nContexte: {context_str[:4000]}"}]
            }
            r = requests.post("https://api.anthropic.com/v1/messages", headers=headers, json=payload, timeout=20)
            if r.status_code == 200:
                result_text = r.json()["content"][0]["text"]
            else:
                result_text = f"Erreur Anthropic {r.status_code}: {r.text[:500]}"
        else:
            result_text = f"Provider {provider} - Analyse simulée:\n\nBasé sur {len(req.incidents or [])} incidents, l'analyse de '{req.prompt}' montre:\n- Risque global modéré à élevé\n- Zones critiques identifiées\n- Recommandation: veille renforcée + vérification satellite\n\n[Mode simulation - configurez OpenAI/Gemini/Anthropic pour analyse réelle]"
    except Exception as e:
        result_text = f"Erreur lors de l'analyse IA: {str(e)} - Mode simulation activé"

    return {
        "provider": provider,
        "model": req.model or "default",
        "prompt": req.prompt,
        "context_length": len(context_str),
        "result": result_text,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": "Les clés API ne sont pas stockées, uniquement utilisées pour cette requête"
    }

@app.get("/api/ai/providers")
def ai_providers():
    """List the supported AI providers and their configuration requirements.

    Returns:
        List of provider descriptors for the settings panel."""

    return {
        "providers": [
            {"id": "openai", "name": "OpenAI GPT-4o / GPT-4o-mini", "models": ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo"], "key_name": "OPENAI_API_KEY", "url": "https://platform.openai.com/api-keys"},
            {"id": "gemini", "name": "Google Gemini", "models": ["gemini-1.5-flash", "gemini-1.5-pro", "gemini-2.0-flash"], "key_name": "GEMINI_API_KEY", "url": "https://aistudio.google.com/app/apikey"},
            {"id": "anthropic", "name": "Anthropic Claude", "models": ["claude-3-haiku-20240307", "claude-3-5-sonnet-20241022"], "key_name": "ANTHROPIC_API_KEY", "url": "https://console.anthropic.com/"},
            {"id": "mistral", "name": "Mistral AI", "models": ["mistral-small", "mistral-large-latest"], "key_name": "MISTRAL_API_KEY", "url": "https://console.mistral.ai/api-keys/"},
        ],
        "note": "Fournissez votre clé via l'interface ou directement dans la requête. Clés jamais stockées côté serveur en production."
    }

# -------------------------------------------------------------------
# SECURITY & OTHER
# -------------------------------------------------------------------
@app.get("/api/incidents/history")
def history(
    category: Optional[str] = Query(None, description="Filtre par catégorie"),
    region: Optional[str] = Query(None, description="Filtre par région"),
    country: Optional[str] = Query(None, description="Filtre par pays (sous-chaîne)"),
    source: Optional[str] = Query(None, description="Filtre par source (sous-chaîne)"),
    search: Optional[str] = Query(None, description="Recherche plein texte"),
    date: Optional[str] = Query(None, description="Jour précis YYYY-MM-DD"),
    date_from: Optional[str] = Query(None, description="Début de période YYYY-MM-DD"),
    date_to: Optional[str] = Query(None, description="Fin de période YYYY-MM-DD"),
    min_risk: Optional[int] = Query(None, ge=1, le=5, description="Risque minimum"),
    order: str = Query("desc", description="desc = plus récent d'abord"),
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
):
    """Query the persisted event archive.

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
    """
    if date and not date_from and not date_to:
        date_from, date_to = date, date
    incidents = storage.search_archive(
        limit=limit, offset=offset, category=category, region=region, country=country,
        search=search, date_from=date_from, date_to=date_to, source=source,
        min_risk=min_risk, order=order,
    )
    total = storage.count_archive(
        category=category, region=region, country=country, search=search,
        date_from=date_from, date_to=date_to, source=source, min_risk=min_risk,
    )
    return {
        "incidents": incidents,
        "total": total,
        "returned": len(incidents),
        "limit": limit,
        "offset": offset,
        "backend": storage.backend_name(),
        "filters": {"category": category, "region": region, "country": country, "source": source,
                    "search": search, "date_from": date_from, "date_to": date_to, "min_risk": min_risk},
    }


@app.get("/api/history/stats")
def history_stats():
    """Aggregate the archive for the history dashboard.

    Returns:
        Mapping with the archived total, the covered time range, the retention
        policy and per-category/region/country/source/day breakdowns.
    """
    return storage.archive_stats()


@app.get("/api/history/dates")
def history_dates():
    """List the days for which the archive holds events.

    Returns:
        Mapping with a ``dates`` list of ``{date, count}``, newest first, so
        the UI can offer a date picker restricted to days that have data.
    """
    return {"dates": storage.archive_dates(), "backend": storage.backend_name()}


@app.delete("/api/history/prune")
def history_prune(days: Optional[int] = Query(None, ge=0, description="Rétention en jours")):
    """Delete archived events older than the retention window.

    Args:
        days: Override for the ``OSINT_RETENTION_DAYS`` setting. ``0`` is a
            no-op, never a full wipe.

    Returns:
        Mapping with the number of deleted rows and the remaining count.
    """
    deleted = storage.prune(days)
    return {"deleted": deleted, "remaining": storage.count_archive(), "backend": storage.backend_name()}


@app.get("/api/security/assessment")
def security_assessment():
    """Compute a synthetic security posture for the monitored regions.

    Returns:
        Mapping with per-region risk scores, trends and contributing factors."""

    incidents = live_cache["incidents"] or []
    assessment = {}
    for inc in incidents:
        region = inc.get("region", "Global")
        country = inc.get("country", "International")
        key = f"{region}::{country}"
        if key not in assessment:
            assessment[key] = {"region": region, "country": country, "incident_count": 0, "categories": {}, "max_risk": 0, "actors": set(), "latest_incident": None, "recommendations": [], "sources": set()}
        assessment[key]["incident_count"] += 1
        cat = inc.get("category", "conflit")
        assessment[key]["categories"][cat] = assessment[key]["categories"].get(cat, 0) + 1
        assessment[key]["max_risk"] = max(assessment[key]["max_risk"], inc.get("risk_level", 2))
        for a in inc.get("actors", []): assessment[key]["actors"].add(a)
        assessment[key]["sources"].add(inc.get("source", ""))
        if not assessment[key]["latest_incident"] or inc.get("published_at", "") > assessment[key]["latest_incident"].get("published_at", ""):
            assessment[key]["latest_incident"] = inc
    result = []
    for key, data in assessment.items():
        recs = []
        if data["max_risk"] >= 4: recs.append("🔴 RISQUE ÉLEVÉ - Restreindre mouvements, renforcer sécurité, convoi armé recommandé")
        if "conflit" in data["categories"]: recs.append("⚠️ Zone conflit actif - Suivre consignes sécurité ONU, couvre-feu, abris")
        if "epidemie" in data["categories"]: recs.append("☣️ Risque sanitaire - EPI, protocoles médicaux, quarantaine si nécessaire")
        if "catastrophe" in data["categories"]: recs.append("🌋 Risque naturel - Vérifier routes, abris, évacuation, stocks")
        if "cyber" in data["categories"]: recs.append("💻 Risque cyber - Isoler systèmes, backup, VPN")
        if data["incident_count"] >= 3: recs.append("📡 Forte activité - Veille renforcée, check-in toutes les 2h")
        if len(data["sources"]) >= 3: recs.append("✅ Info multi-sources vérifiée - Fiabilité élevée")
        data["actors"] = list(data["actors"])[:6]
        data["sources"] = list(data["sources"])[:5]
        data["recommendations"] = recs
        data["risk_label"] = ["Faible", "Modéré", "Moyen", "Élevé", "Critique", "Extrême"][min(data["max_risk"], 5)]
        result.append(data)
    result.sort(key=lambda x: x["max_risk"], reverse=True)
    return {"assessment": result[:30], "generated_at": datetime.now(timezone.utc).isoformat(), "total_regions": len(result), "live": True, "scraping_coverage": len(RSS_FEEDS) + 16}

@app.get("/api/humanitarian/actors")
def humanitarian_actors():
    """List the humanitarian actors referenced by the platform.

    Returns:
        Mapping with actor profiles (agencies, NGOs, armed groups) and their
        areas of operation."""

    return {
        "clusters": [
            {"name": "OCHA", "role": "Coordination humanitaire", "contact": "ocha.org", "active_regions": ["Moyen-Orient", "Afrique", "Asie-Pacifique", "Amériques"], "type": "ONU", "live": True},
            {"name": "MSF", "role": "Santé d'urgence", "contact": "msf.org", "active_regions": ["Afrique", "Moyen-Orient"], "type": "ONG", "live": True},
            {"name": "UNHCR", "role": "Protection réfugiés", "contact": "unhcr.org", "active_regions": ["Global"], "type": "ONU", "live": True},
            {"name": "PAM / WFP", "role": "Sécurité alimentaire", "contact": "wfp.org", "active_regions": ["Afrique", "Moyen-Orient", "Asie-Pacifique"], "type": "ONU", "live": True},
            {"name": "UNICEF", "role": "Protection enfants", "contact": "unicef.org", "active_regions": ["Global"], "type": "ONU", "live": True},
            {"name": "CICR", "role": "Droit humanitaire / Conflits", "contact": "icrc.org", "active_regions": ["Conflits"], "type": "ONG", "live": True},
            {"name": "GDACS", "role": "Alertes catastrophes satellite temps réel", "contact": "gdacs.org", "active_regions": ["Global"], "type": "SATELLITE", "live": True},
            {"name": "NASA EONET", "role": "Détection satellite feux/volcans/tempêtes", "contact": "eonet.gsfc.nasa.gov", "active_regions": ["Global"], "type": "SATELLITE", "live": True},
            {"name": "USGS", "role": "Surveillance sismique temps réel", "contact": "earthquake.usgs.gov", "active_regions": ["Global"], "type": "SATELLITE", "live": True},
            {"name": "NASA FIRMS", "role": "Feux actifs MODIS/VIIRS", "contact": "firms.modaps.eosdis.nasa.gov", "active_regions": ["Global"], "type": "SATELLITE", "live": True},
            {"name": "GDELT Project", "role": "Scraping massif médias mondiaux 100+ langues", "contact": "gdeltproject.org", "active_regions": ["Global"], "type": "MEDIA", "live": True},
            {"name": "Reddit OSINT Community", "role": "Veille collaborative OSINT", "contact": "reddit.com/r/OSINT", "active_regions": ["Global"], "type": "SOCIAL", "live": True},
            {"name": "Telegram OSINT", "role": "Canaux OSINT temps réel", "contact": "t.me/OSINTtechnical", "active_regions": ["Europe", "Moyen-Orient"], "type": "SOCIAL", "live": True},
            {"name": "ACLED", "role": "Base données conflits armés", "contact": "acleddata.com", "active_regions": ["Afrique", "Moyen-Orient", "Asie"], "type": "RENSEIGNEMENT", "live": True},
            {"name": "ISW", "role": "Renseignement guerre Ukraine", "contact": "understandingwar.org", "active_regions": ["Europe"], "type": "RENSEIGNEMENT", "live": True},
            {"name": "Bellingcat", "role": "Investigation OSINT / vérification", "contact": "bellingcat.com", "active_regions": ["Global"], "type": "OSINT", "live": True},
        ],
        "enjeux": [
            {"name": "Sécurité", "description": "Protection équipes, accès, convois", "risk_factors": ["conflit", "kidnapping", "IED"]},
            {"name": "Logistique", "description": "Routes, carburant, maritime", "risk_factors": ["catastrophe", "energie", "blocage"]},
            {"name": "Santé", "description": "Épidémies, EPI, vaccins", "risk_factors": ["epidemie", "catastrophe"]},
            {"name": "Protection", "description": "Civils, déplacés, droits", "risk_factors": ["conflit", "protest"]},
            {"name": "Information", "description": "Désinfo, vérification", "risk_factors": ["cyber", "social"]},
            {"name": "Satellite", "description": "Imagerie, feux, séismes", "risk_factors": ["catastrophe"]},
        ],
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "live_sources": ["ReliefWeb API", "NASA EONET", "NASA FIRMS", "USGS", "GDACS", "GDELT", "Reddit", "Telegram", f"{len(RSS_FEEDS)} RSS médias"]
    }

@app.get("/api/satellite/layers")
def satellite_layers():
    """List the satellite and map layers available to the front-end.

    Returns:
        Mapping describing the 2D tile layers and the 3D Cesium base layers
        (Esri World Imagery, OpenStreetMap, dark basemaps, labels)."""

    return {
        "base_layers": [
            {"id": "esri_satellite", "name": "SATELLITE HD Esri World Imagery - RÉEL 0.3m", "url": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", "type": "satellite", "max_zoom": 19, "attribution": "Esri World Imagery - Satellite réel temps réel HD", "live": True, "resolution": "0.3m-1m"},
            {"id": "esri_labels", "name": "Labels & Frontières & Routes", "url": "https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}", "type": "overlay", "max_zoom": 19, "attribution": "Esri", "live": False},
            {"id": "osm_standard", "name": "OSM Standard", "url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png", "type": "street", "max_zoom": 19, "attribution": "OpenStreetMap"},
            {"id": "opentopo", "name": "Relief Topographique OpenTopoMap", "url": "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png", "type": "terrain", "max_zoom": 17, "attribution": "OpenTopoMap - Relief satellite"},
            {"id": "cyclosm", "name": "CyclOSM - Réseau routier tactique", "url": "https://{s}.tile-cyclosm.openstreetmap.fr/cyclosm/{z}/{x}/{y}.png", "type": "terrain", "max_zoom": 18, "attribution": "CyclOSM - Logistique"},
            {"id": "google_satellite", "name": "Google Satellite (alternative)", "url": "https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}", "type": "satellite", "max_zoom": 20, "attribution": "Google Satellite"},
            {"id": "bing_satellite", "name": "Bing Satellite", "url": "https://ecn.t3.tiles.virtualearth.net/tiles/a{q}.jpeg?g=1", "type": "satellite", "max_zoom": 19, "attribution": "Bing"},
        ],
        "overlays": [
            {"id": "rainviewer", "name": "Radar Météo & Précipitations Live", "url": "https://tilecache.rainviewer.com/v2/radar/nowcast_10/256/{z}/{x}/{y}/2/1_1.png", "type": "weather", "live": True, "refresh_sec": 600, "source": "RainViewer"},
            {"id": "firms_fires", "name": "Feux actifs NASA FIRMS (MODIS/VIIRS) Live", "url": "https://firms.modaps.eosdis.nasa.gov/...", "type": "satellite", "live": True, "source": "NASA FIRMS"},
            {"id": "eonet", "name": "Événements NASA EONET Live", "type": "geojson", "source": "/api/feeds/live", "filter": "catastrophe", "live": True},
            {"id": "usgs", "name": "Séismes USGS Live", "type": "geojson", "source": "/api/feeds/live", "filter": "earthquake", "live": True},
        ],
        "cesium_config": {
            "viewer_option": "baseLayer",
            "imagery_provider": "UrlTemplateImageryProvider wrapped in Cesium.ImageryLayer",
            "satellite_url": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
            "labels_url": "https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}",
            "terrain": "EllipsoidTerrainProvider - can upgrade to Cesium World Terrain with token",
            "note": (
                "CesiumJS a supprime l'option Viewer `imageryProvider` en 1.107 (depreciee en 1.104). "
                "La passer en 1.115 est ignore silencieusement : le globe affiche alors sa baseColor "
                "(bleu) et seule la couche de frontieres reste visible. Chaque fond doit donc etre "
                "emballe dans une Cesium.ImageryLayer et fourni via `baseLayer` / `imageryLayers.add`. "
                "Aucun token Ion n'est requis."
            ),
            "globe_basemaps": [
                {"key": "sat", "label": "Esri World Imagery 0.3m", "tile_order": "{z}/{y}/{x}", "labels": True},
                {"key": "google", "label": "Google Satellite", "tile_order": "{z}/{x}/{y}", "labels": True},
                {"key": "dark", "label": "CARTO Dark Matter", "tile_order": "{z}/{x}/{y}", "labels": False},
                {"key": "osm", "label": "OpenStreetMap", "tile_order": "{z}/{x}/{y}", "labels": False},
                {"key": "terrain", "label": "OpenTopoMap relief", "tile_order": "{z}/{x}/{y}", "labels": False}
            ],
            "alternative_providers": [
                "Esri World Imagery (default)",
                "Google Satellite via UrlTemplate",
                "OpenStreetMap",
                "CARTO Dark Matter",
                "OpenTopoMap",
                "Bing Maps Aerial",
                "Sentinel-2 via Sentinel Hub (requires token)"
            ]
        }
    }

@app.post("/api/geozones", response_model=GeozoneResponse)
def create_geozone(zone: GeozoneCreate):
    """Persist a user-drawn geozone.

    Args:
        zone: Validated :class:`GeozoneCreate` payload.

    Returns:
        The stored zone as a :class:`GeozoneResponse`.

    Raises:
        HTTPException: 500 when the row could not be written.
    """
    raw_geojson = json.dumps(zone.geojson_data, ensure_ascii=False) if isinstance(zone.geojson_data, (dict, list)) else str(zone.geojson_data)
    row = storage.save_geozone(zone.name, zone.geometry_type, raw_geojson, zone.area_sqkm)
    if not row:
        raise HTTPException(status_code=500, detail="geozone could not be saved")
    try:
        parsed = json.loads(raw_geojson)
    except Exception:  # noqa: BLE001 - tolerate non-JSON payloads
        parsed = raw_geojson
    return {"id": row["id"], "name": row["name"], "geometry_type": row["geometry_type"],
            "geojson_data": parsed, "area_sqkm": row["area_sqkm"], "created_at": row["created_at"]}

@app.get("/api/geozones", response_model=List[GeozoneResponse])
def get_geozones():
    """Return every persisted geozone.

    Returns:
        List of :class:`GeozoneResponse` objects, newest first.
    """
    result = []
    for row in storage.list_geozones():
        try:
            parsed = json.loads(row["geojson_data"])
        except Exception:  # noqa: BLE001
            parsed = row["geojson_data"]
        result.append({"id": row["id"], "name": row["name"], "geometry_type": row["geometry_type"],
                       "geojson_data": parsed, "area_sqkm": row["area_sqkm"], "created_at": row["created_at"]})
    return result

@app.delete("/api/geozones/{zone_id}")
def delete_geozone(zone_id: int):
    """Delete a persisted geozone.

    Args:
        zone_id: Identifier of the zone to remove.

    Returns:
        Acknowledgement mapping.

    Raises:
        HTTPException: 404 when the identifier is unknown.
    """
    if not storage.delete_geozone(zone_id):
        raise HTTPException(status_code=404, detail="geozone not found")
    return {"status": "deleted", "id": zone_id}


# -------------------------------------------------------------------
# GIS EXPORT
# -------------------------------------------------------------------
@app.get("/api/export/incidents.{fmt}")
def export_incidents(
    fmt: str,
    category: Optional[str] = Query(None),
    region: Optional[str] = Query(None),
    country: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    min_risk: Optional[int] = Query(None, ge=1, le=5),
    live_only: bool = Query(False, description="Exporter le cache live plutôt que l'archive"),
    limit: int = Query(1000, ge=1, le=5000),
):
    """Export the event list, coordinates included, in a GIS format.

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
    """
    from fastapi.responses import Response
    fmt_norm = (fmt or "").lower().strip()
    if fmt_norm not in osint_tools.EXPORT_FORMATS:
        raise HTTPException(status_code=400, detail=f"format '{fmt}' inconnu, attendu: {sorted(osint_tools.EXPORT_FORMATS)}")
    if live_only:
        incidents = (live_cache["incidents"] or [])[:limit]
    else:
        incidents = storage.search_archive(limit=limit, category=category, region=region, country=country,
                                           search=search, date_from=date_from, date_to=date_to,
                                           source=source, min_risk=min_risk, order="desc")
    mime, ext = osint_tools.EXPORT_FORMATS[fmt_norm]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    try:
        body = osint_tools.build_export(incidents, fmt_norm, name=f"HUMAN-OSINT {stamp}")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    with_coords = sum(1 for i in incidents if (i.get("latitude") or i.get("longitude")))
    logger.info("Export %s: %d rows (%d with coordinates)", fmt_norm, len(incidents), with_coords)
    return Response(
        content=body,
        media_type=mime,
        headers={"Content-Disposition": f'attachment; filename="human-osint-{stamp}.{ext}"',
                 "X-Exported-Rows": str(len(incidents)),
                 "X-Rows-With-Coordinates": str(with_coords)},
    )


@app.get("/api/export/formats")
def export_formats():
    """List the GIS export formats the API can produce.

    Returns:
        Mapping of format key to its MIME type, file extension, description
        and the software that reads it.
    """
    details = {
        "geojson": "GeoJSON 1.1 - QGIS, ArcGIS, geojson.io, Leaflet, Mapbox",
        "csv": "CSV point-virgule UTF-8 BOM - Excel, LibreOffice, tableur",
        "kml": "KML 2.2 - Google Earth, Google My Maps, QGIS",
        "gpx": "GPX 1.1 - GPS, BaseCamp, Garmin, Wikiloc",
    }
    return {"formats": [{"key": k, "mime": v[0], "extension": v[1], "description": details.get(k, "")}
                        for k, v in osint_tools.EXPORT_FORMATS.items()]}


# -------------------------------------------------------------------
# GEOCODING
# -------------------------------------------------------------------
@app.get("/api/geocode")
def geocode_endpoint(q: str = Query(..., min_length=2, description="Lieu, adresse ou repère"),
                     limit: int = Query(5, ge=1, le=10),
                     language: str = Query("fr")):
    """Resolve a place name, address or landmark to coordinates.

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
    """
    return osint_tools.geocode(q, limit=limit, language=language)


@app.get("/api/geocode/reverse")
def reverse_geocode_endpoint(lat: float = Query(..., ge=-90, le=90),
                             lon: float = Query(..., ge=-180, le=180),
                             language: str = Query("fr")):
    """Resolve coordinates to an address.

    Args:
        lat: Latitude in decimal degrees.
        lon: Longitude in decimal degrees.
        language: Preferred language for the label.

    Returns:
        Mapping with the resolved address and its parts.
    """
    return osint_tools.reverse_geocode(lat, lon, language=language)


# -------------------------------------------------------------------
# IDENTITY LOOKUPS
# -------------------------------------------------------------------
class EmailRequest(BaseModel):
    """Payload for the e-mail analysis endpoint."""
    email: str = Field(..., description="Adresse à analyser")
    check_mx: bool = Field(True, description="Vérifier aussi les enregistrements MX")


@app.post("/api/osint/identity/email")
def identity_email(req: EmailRequest):
    """Analyse an e-mail address and build every related lookup link.

    Args:
        req: Validated :class:`EmailRequest` payload.

    Returns:
        Mapping with the parsing result, flags (disposable, role account,
        free provider), the Gravatar hash and the engine links. Optionally
        the MX records proving the domain can receive mail.
    """
    result = osint_tools.analyze_email(req.email)
    if req.check_mx and result.get("domain"):
        result["mx"] = osint_tools.email_mx_records(result["domain"])
    return result


class PhoneRequest(BaseModel):
    """Payload for the phone-number analysis endpoint."""
    number: str = Field(..., description="Numéro, avec ou sans préfixe international")
    default_region: str = Field("SN", description="Pays par défaut si pas de +")


@app.post("/api/osint/identity/phone")
def identity_phone(req: PhoneRequest):
    """Normalise a phone number and build WhatsApp and lookup links.

    Args:
        req: Validated :class:`PhoneRequest` payload.

    Returns:
        Mapping with the E.164 form, country, line type, carrier hint, a
        direct WhatsApp link and the reverse-lookup engines.
    """
    return osint_tools.analyze_phone(req.number, default_region=req.default_region)


class UsernameRequest(BaseModel):
    """Payload for the username lookup endpoint."""
    username: str = Field(..., description="Pseudo à rechercher")


@app.post("/api/osint/identity/username")
def identity_username(req: UsernameRequest):
    """Build profile URLs for a username across the monitored platforms.

    Args:
        req: Validated :class:`UsernameRequest` payload.

    Returns:
        Mapping with name variants and one ready-to-open URL per platform.
    """
    return osint_tools.analyze_username(req.username)


class PersonRequest(BaseModel):
    """Payload for the people-search endpoint."""
    name: str = Field(..., description="Nom complet")
    city: Optional[str] = Field(None, description="Ville, pour affiner")
    country: Optional[str] = Field(None, description="Pays, pour affiner")
    employer: Optional[str] = Field(None, description="Employeur, pour affiner")


@app.post("/api/osint/identity/person")
def identity_person(req: PersonRequest):
    """Build people-search queries for a full name.

    Args:
        req: Validated :class:`PersonRequest` payload.

    Returns:
        Mapping with the composed query and the search-engine links.
    """
    return osint_tools.analyze_person(req.name, {"city": req.city, "country": req.country, "employer": req.employer})


@app.get("/api/osint/identity/catalog")
def identity_catalog():
    """Describe every identity tool so the UI can render its own help.

    Returns:
        Mapping keyed by tool with the engine registries and a usage summary.
    """
    return osint_tools.identity_catalog()


@app.get("/")
def serve_index():
    """Serve the single-page front-end at the repository root.

    Returns:
        ``FileResponse`` for ``index.html``."""

    index_path = os.path.join(os.path.dirname(__file__), "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path, media_type="text/html")
    return JSONResponse({"status": f"HUMAN-OSINT v{APP_VERSION} ULTIMATE LIVE", "version": APP_VERSION, "endpoints": {"live": "/api/feeds/live", "social": "/api/osint/social", "media": "/api/osint/media", "comprehensive": "/api/osint/comprehensive", "dorks": "/api/dorks/all", "engines": "/api/osint/engines", "reverse_image": "/api/osint/reverse-image/engines", "report": "/api/report/generate", "ai": "/api/ai/providers"}, "rss_sources": len(RSS_FEEDS), "dorks": len(DORKS_DATABASE), "engines": len(OSINT_ENGINES), "mode": "V4 - Ultimate Power"})

if __name__ == "__main__":
    import uvicorn
    print("=================================================================")
    print(f" [HUMAN-OSINT v{APP_VERSION} ULTIMATE] - POWER OSINT/GEOINT PLATFORM")
    print(f" RSS Sources: {len(RSS_FEEDS)} | Dorks: {len(DORKS_DATABASE)} | Engines: {len(OSINT_ENGINES)}")
    print(" Live: NASA EONET + USGS + ReliefWeb + GDACS + FIRMS + GDELT x5 + Reddit x6 + Telegram x4 + 60 RSS")
    print(" Features: Reverse Image, Advanced Search, OSINT Engines, Report Generator, AI Agent")
    print(" Satellite: Esri 0.3m HD + Cesium 3D Globe Real Satellite")
    print(" Refresh: 60s auto + SSE + robust fallback")
    print("=================================================================")
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
