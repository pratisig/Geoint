#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OSINT toolkit: GIS export, geocoding and identity lookups.

Split out of ``main.py`` so that the pure, offline-testable logic (URL
building, format conversion, phone and email parsing) stays separate from the
HTTP endpoints and from the handful of functions that actually hit the network.

Everything here is deterministic except :func:`geocode`,
:func:`reverse_geocode` and :func:`email_mx_records`, which call external
services and therefore degrade to an explicit error payload instead of raising.

Contact for the third-party usage policies honoured below:
PratiSIG Consulting Services - pratisg.consulting@gmail.com
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence
from xml.sax.saxutils import escape as xml_escape

logger = logging.getLogger("HUMAN-OSINT-TOOLS")

#: User-Agent required by the OpenStreetMap/Nominatim usage policy.
USER_AGENT = "HUMAN-OSINT/4.2 (PratiSIG Consulting Services; pratisg.consulting@gmail.com)"
NOMINATIM_URL = "https://nominatim.openstreetmap.org"
HTTP_TIMEOUT = 12

# Nominatim asks for at most one request per second per client.
_throttle_lock = threading.Lock()
_last_request = 0.0


def _throttled_get(url: str, params: Dict[str, Any]) -> Any:
    """Perform a GET against a third-party API, respecting a 1 req/s budget.

    Args:
        url: Endpoint to call.
        params: Query parameters.

    Returns:
        Decoded JSON body.

    Raises:
        RuntimeError: With a human-readable message when the call fails, so
            callers can surface the reason to the user instead of a stack
            trace.
    """
    global _last_request
    import requests
    with _throttle_lock:
        wait = 1.1 - (time.time() - _last_request)
        if wait > 0:
            time.sleep(wait)
        _last_request = time.time()
    try:
        resp = requests.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=HTTP_TIMEOUT)
        if resp.status_code == 429:
            raise RuntimeError("rate limit reached on the upstream service, retry in a few seconds")
        resp.raise_for_status()
        return resp.json()
    except RuntimeError:
        raise
    except Exception as exc:  # noqa: BLE001 - network failures are expected
        raise RuntimeError(str(exc)[:200]) from exc


# ===========================================================================
# GIS EXPORT
# ===========================================================================
def _coords(inc: Dict[str, Any]) -> Optional[List[float]]:
    """Return ``[longitude, latitude]`` for an incident, or None if absent.

    GeoJSON uses longitude-first ordering, the opposite of almost every other
    geospatial format, so this is centralised to avoid a silent axis swap.
    """
    try:
        lat = float(inc.get("latitude"))
        lon = float(inc.get("longitude"))
    except (TypeError, ValueError):
        return None
    if lat == 0.0 and lon == 0.0:
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    return [lon, lat]


def incidents_to_geojson(incidents: Sequence[Dict[str, Any]], name: str = "HUMAN-OSINT") -> Dict[str, Any]:
    """Build a GeoJSON FeatureCollection from incidents.

    Incidents without usable coordinates are reported in the collection
    metadata rather than silently dropped, so an export never looks complete
    when part of the list had no position.

    Args:
        incidents: Incident dictionaries.
        name: Collection name recorded in the properties.

    Returns:
        A GeoJSON 1.1 FeatureCollection dictionary.
    """
    features = []
    skipped = 0
    for inc in incidents:
        pos = _coords(inc)
        if pos is None:
            skipped += 1
            continue
        actors = inc.get("actors") or []
        needs = inc.get("needs") or []
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": pos},
            "properties": {
                "id": inc.get("id"),
                "title": inc.get("title", ""),
                "summary": inc.get("summary", ""),
                "link": inc.get("link", ""),
                "source": inc.get("source", ""),
                "source_type": inc.get("source_type", ""),
                "category": inc.get("category", ""),
                "region": inc.get("region", ""),
                "country": inc.get("country", ""),
                "severity": inc.get("severity", ""),
                "risk_level": inc.get("risk_level", 0),
                "published_at": inc.get("published_at", ""),
                "first_seen_at": inc.get("first_seen_at", ""),
                "actors": ", ".join(actors) if isinstance(actors, list) else str(actors),
                "needs": ", ".join(needs) if isinstance(needs, list) else str(needs),
            },
        })
    return {
        "type": "FeatureCollection",
        "name": name,
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
        "features": features,
        "metadata": {
            "generator": "HUMAN-OSINT v4.2 - PratiSIG Consulting Services",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total_input": len(incidents),
            "with_coordinates": len(features),
            "without_coordinates": skipped,
        },
    }


def incidents_to_csv(incidents: Sequence[Dict[str, Any]]) -> str:
    """Serialise incidents to CSV with a semicolon delimiter.

    Semicolons are used because the data is French and contains many commas;
    Excel in a French locale opens semicolon CSV correctly on double-click.

    Args:
        incidents: Incident dictionaries.

    Returns:
        The CSV document as text, prefixed with a UTF-8 BOM so Excel detects
        the encoding.
    """
    fields = ["id", "published_at", "title", "category", "risk_level", "severity",
              "country", "region", "latitude", "longitude", "source", "source_type",
              "actors", "needs", "summary", "link", "first_seen_at"]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fields, delimiter=";", extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for inc in incidents:
        row = dict(inc)
        for key in ("actors", "needs"):
            val = row.get(key)
            row[key] = ", ".join(val) if isinstance(val, list) else (val or "")
        writer.writerow({k: row.get(k, "") for k in fields})
    return "\ufeff" + buf.getvalue()


def incidents_to_kml(incidents: Sequence[Dict[str, Any]], name: str = "HUMAN-OSINT") -> str:
    """Build a KML document, colour-coded by category.

    Args:
        incidents: Incident dictionaries.
        name: Document name shown in Google Earth.

    Returns:
        A KML 2.2 document as text.
    """
    styles = {
        "conflit": ("ff2a5f", "Conflit"),
        "catastrophe": ("00e5ff", "Catastrophe"),
        "epidemie": ("bd34fe", "Épidémie"),
        "energie": ("ff9900", "Énergie"),
        "cyber": ("00ff9d", "Cyber"),
        "protest": ("ffe600", "Protestation"),
    }
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<kml xmlns="http://www.opengis.net/kml/2.2">',
        "<Document>",
        f"<name>{xml_escape(name)}</name>",
        "<description>Généré par HUMAN-OSINT v4.2 - PratiSIG Consulting Services</description>",
    ]
    for cat, (colour, label) in styles.items():
        parts.append(
            f'<Style id="{cat}"><IconStyle><color>ff{colour[4:6]}{colour[2:4]}{colour[0:2]}</color>'
            f"<scale>1.1</scale><Icon><href>http://maps.google.com/mapfiles/kml/shapes/placemark_circle.png</href>"
            f"</Icon></IconStyle><LabelStyle><scale>0.8</scale></LabelStyle></Style>"
        )
    count = 0
    for inc in incidents:
        pos = _coords(inc)
        if pos is None:
            continue
        count += 1
        cat = str(inc.get("category", "conflit")).lower()
        if cat not in styles:
            cat = "conflit"
        desc = (
            f"<![CDATA[<b>{xml_escape(str(inc.get('title','')))}</b><br/>"
            f"Source : {xml_escape(str(inc.get('source','')))}<br/>"
            f"Pays : {xml_escape(str(inc.get('country','')))}<br/>"
            f"Risque : {inc.get('risk_level', 0)}/5<br/>"
            f"Publié : {xml_escape(str(inc.get('published_at','')))}<br/>"
            f"{xml_escape(str(inc.get('summary','')))}<br/>"
            f"<a href=\"{xml_escape(str(inc.get('link','')))}\">Source originale</a>]]>"
        )
        parts.append(
            "<Placemark>"
            f"<name>{xml_escape(str(inc.get('title',''))[:80])}</name>"
            f"<styleUrl>#{cat}</styleUrl>"
            f"<description>{desc}</description>"
            f"<Point><coordinates>{pos[0]},{pos[1]},0</coordinates></Point>"
            "</Placemark>"
        )
    parts += ["</Document>", "</kml>"]
    logger.info("KML export: %d placemarks", count)
    return "\n".join(parts)


def incidents_to_gpx(incidents: Sequence[Dict[str, Any]], name: str = "HUMAN-OSINT") -> str:
    """Build a GPX 1.1 document of waypoints.

    Args:
        incidents: Incident dictionaries.
        name: Document metadata name.

    Returns:
        A GPX 1.1 document as text.
    """
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<gpx version="1.1" creator="HUMAN-OSINT v4.2 - PratiSIG Consulting Services" '
        'xmlns="http://www.topografix.com/GPX/1/1" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xsi:schemaLocation="http://www.topografix.com/GPX/1/1 http://www.topografix.com/GPX/1/1/gpx.xsd">',
        f"<metadata><name>{xml_escape(name)}</name>"
        f"<time>{datetime.now(timezone.utc).isoformat()}</time></metadata>",
    ]
    for i, inc in enumerate(incidents, start=1):
        pos = _coords(inc)
        if pos is None:
            continue
        parts.append(
            f'<wpt lat="{pos[1]}" lon="{pos[0]}">'
            f"<time>{xml_escape(str(inc.get('published_at','')))}</time>"
            f"<name>{xml_escape(str(inc.get('title',''))[:80])}</name>"
            f"<desc>{xml_escape(str(inc.get('summary',''))[:300])}</desc>"
            f"<sym>{xml_escape(str(inc.get('category','conflit')))}</sym>"
            f"<type>{xml_escape(str(inc.get('source','')))}</type>"
            f"<link href=\"{xml_escape(str(inc.get('link','')))}\"><text>Source</text></link>"
            "</wpt>"
        )
    parts.append("</gpx>")
    return "\n".join(parts)


#: Export formats the API exposes, mapped to their MIME type and extension.
EXPORT_FORMATS = {
    "geojson": ("application/geo+json", "geojson"),
    "csv": ("text/csv; charset=utf-8", "csv"),
    "kml": ("application/vnd.google-earth.kml+xml", "kml"),
    "gpx": ("application/gpx+xml", "gpx"),
}


def build_export(incidents: Sequence[Dict[str, Any]], fmt: str, name: str = "HUMAN-OSINT") -> str:
    """Serialise incidents in the requested GIS format.

    Args:
        incidents: Incident dictionaries.
        fmt: One of the keys of :data:`EXPORT_FORMATS`.
        name: Document/collection name.

    Returns:
        The serialised document as text.

    Raises:
        ValueError: When ``fmt`` is not a supported format.
    """
    fmt = (fmt or "").lower().strip()
    if fmt == "geojson":
        return json.dumps(incidents_to_geojson(incidents, name), ensure_ascii=False, indent=2)
    if fmt == "csv":
        return incidents_to_csv(incidents)
    if fmt == "kml":
        return incidents_to_kml(incidents, name)
    if fmt == "gpx":
        return incidents_to_gpx(incidents, name)
    raise ValueError(f"unsupported export format '{fmt}', expected one of {sorted(EXPORT_FORMATS)}")


# ===========================================================================
# GEOCODING
# ===========================================================================
def geocode(query: str, limit: int = 5, language: str = "fr") -> Dict[str, Any]:
    """Resolve a place name or address to coordinates via OSM Nominatim.

    Args:
        query: Free-text place, address or landmark.
        limit: Maximum number of results (1-10).
        language: Preferred language for the returned labels.

    Returns:
        Mapping with ``query``, ``count`` and ``results`` (each with
        ``display_name``, ``latitude``, ``longitude``, ``type``, ``country``,
        ``boundingbox``), or an ``error`` key when the call failed.
    """
    query = (query or "").strip()
    if not query:
        return {"query": "", "count": 0, "results": [], "error": "empty query"}
    try:
        raw = _throttled_get(f"{NOMINATIM_URL}/search", {
            "q": query, "format": "jsonv2", "limit": max(1, min(10, int(limit))),
            "addressdetails": 1, "accept-language": language,
        })
    except RuntimeError as exc:
        return {"query": query, "count": 0, "results": [], "error": str(exc)}
    results = [_shape_nominatim(r) for r in (raw if isinstance(raw, list) else [])]
    return {"query": query, "count": len(results), "results": results, "source": "OpenStreetMap Nominatim"}


def reverse_geocode(latitude: float, longitude: float, language: str = "fr") -> Dict[str, Any]:
    """Resolve coordinates to an address via OSM Nominatim.

    Args:
        latitude: Decimal degrees.
        longitude: Decimal degrees.
        language: Preferred language for the returned label.

    Returns:
        Mapping with the resolved ``display_name``, address parts and the
        input coordinates, or an ``error`` key when the call failed.
    """
    try:
        lat = float(latitude)
        lon = float(longitude)
    except (TypeError, ValueError):
        return {"error": "latitude and longitude must be numbers"}
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return {"error": "coordinates out of range"}
    try:
        raw = _throttled_get(f"{NOMINATIM_URL}/reverse", {
            "lat": lat, "lon": lon, "format": "jsonv2", "zoom": 14,
            "addressdetails": 1, "accept-language": language,
        })
    except RuntimeError as exc:
        return {"latitude": lat, "longitude": lon, "error": str(exc)}
    if not isinstance(raw, dict) or raw.get("error"):
        return {"latitude": lat, "longitude": lon, "error": str(raw.get("error", "no result"))}
    shaped = _shape_nominatim(raw)
    shaped.update({"latitude": lat, "longitude": lon})
    return shaped


def _shape_nominatim(item: Dict[str, Any]) -> Dict[str, Any]:
    """Normalise one Nominatim record into the shape the front-end expects."""
    addr = item.get("address") or {}
    try:
        box = [float(v) for v in (item.get("boundingbox") or [])]
    except (TypeError, ValueError):
        box = []
    return {
        "display_name": item.get("display_name", ""),
        "latitude": float(item.get("lat", 0.0)),
        "longitude": float(item.get("lon", 0.0)),
        "type": item.get("type", ""),
        "class": item.get("class", ""),
        "importance": item.get("importance"),
        "country": addr.get("country", ""),
        "country_code": addr.get("country_code", ""),
        "city": addr.get("city") or addr.get("town") or addr.get("village") or addr.get("municipality") or "",
        "postcode": addr.get("postcode", ""),
        "boundingbox": box,
        "osm_type": item.get("osm_type", ""),
        "osm_id": item.get("osm_id"),
    }


# ===========================================================================
# IDENTITY LOOKUPS
# ===========================================================================
#: Common single-use email domains, used to flag throwaway addresses.
DISPOSABLE_DOMAINS = {
    "mailinator.com", "guerrillamail.com", "10minutemail.com", "yopmail.com", "tempmail.com",
    "throwawaymail.com", "sharklasers.com", "trashmail.com", "dispostable.com", "getnada.com",
    "temp-mail.org", "mintemail.com", "maildrop.cc", "fakeinbox.com", "spamgourmet.com",
}

#: Generic/functional mailboxes, as opposed to a personal address.
ROLE_ACCOUNTS = {"info", "contact", "admin", "support", "webmaster", "noreply",
                 "no-reply", "service", "presse", "rh", "hello", "bonjour", "sales"}

#: Email-oriented OSINT services. ``{email}`` and ``{domain}`` are substituted.
EMAIL_ENGINES = [
    {"id": "google", "name": "Google", "category": "moteur", "free": True,
     "url": "https://www.google.com/search?q=%22{email}%22",
     "description": "Recherche exacte de l'adresse entre guillemets : forums, CV, fuites indexées."},
    {"id": "hibp", "name": "Have I Been Pwned", "category": "fuite", "free": True,
     "url": "https://haveibeenpwned.com/account/{email}",
     "description": "Vérifie si l'adresse apparaît dans des fuites de données connues."},
    {"id": "emailrep", "name": "EmailRep", "category": "réputation", "free": True,
     "url": "https://emailrep.io/{email}",
     "description": "Score de réputation, ancienneté du domaine, présence sur les réseaux."},
    {"id": "hunter", "name": "Hunter.io", "category": "entreprise", "free": False,
     "url": "https://hunter.io/search/{domain}",
     "description": "Retrouve les adresses et le schéma de nommage d'un domaine d'entreprise."},
    {"id": "gravatar", "name": "Gravatar", "category": "profil", "free": True,
     "url": "https://en.gravatar.com/{md5}.json",
     "description": "Profil et avatar associés au hash MD5 de l'adresse (utilisé par WordPress)."},
    {"id": "thatsThem", "name": "That's Them", "category": "annuaire", "free": True,
     "url": "https://thatsthem.com/email/{email}",
     "description": "Annuaire inverse : nom, localisation et âge associés à l'adresse."},
    {"id": "epieos", "name": "Epieos", "category": "agrégateur", "free": False,
     "url": "https://tools.epieos.com/",
     "description": "Agrégateur OSINT : Google, LinkedIn, Skype, Adobe et dizaines d'autres."},
    {"id": "holehe", "name": "Holehe (local)", "category": "agrégateur", "free": True,
     "url": "https://github.com/megadose/holehe",
     "description": "Outil local qui teste l'existence du compte sur 120+ sites sans alerter la cible."},
]

#: Platforms probed for a given username. ``{u}`` is substituted.
USERNAME_ENGINES = [
    {"id": "github", "name": "GitHub", "category": "dev", "url": "https://github.com/{u}", "free": True},
    {"id": "gitlab", "name": "GitLab", "category": "dev", "url": "https://gitlab.com/{u}", "free": True},
    {"id": "x", "name": "X / Twitter", "category": "social", "url": "https://x.com/{u}", "free": True},
    {"id": "instagram", "name": "Instagram", "category": "social", "url": "https://www.instagram.com/{u}/", "free": True},
    {"id": "tiktok", "name": "TikTok", "category": "social", "url": "https://www.tiktok.com/@{u}", "free": True},
    {"id": "youtube", "name": "YouTube", "category": "social", "url": "https://www.youtube.com/@{u}", "free": True},
    {"id": "facebook", "name": "Facebook", "category": "social", "url": "https://www.facebook.com/{u}", "free": True},
    {"id": "linkedin", "name": "LinkedIn", "category": "professionnel", "url": "https://www.linkedin.com/in/{u}", "free": True},
    {"id": "reddit", "name": "Reddit", "category": "forum", "url": "https://www.reddit.com/user/{u}", "free": True},
    {"id": "telegram", "name": "Telegram", "category": "messagerie", "url": "https://t.me/{u}", "free": True},
    {"id": "twitch", "name": "Twitch", "category": "streaming", "url": "https://www.twitch.tv/{u}", "free": True},
    {"id": "steam", "name": "Steam", "category": "gaming", "url": "https://steamcommunity.com/id/{u}", "free": True},
    {"id": "pinterest", "name": "Pinterest", "category": "social", "url": "https://www.pinterest.com/{u}/", "free": True},
    {"id": "medium", "name": "Medium", "category": "publication", "url": "https://medium.com/@{u}", "free": True},
    {"id": "flickr", "name": "Flickr", "category": "photo", "url": "https://www.flickr.com/people/{u}", "free": True},
    {"id": "vimeo", "name": "Vimeo", "category": "vidéo", "url": "https://vimeo.com/{u}", "free": True},
    {"id": "soundcloud", "name": "SoundCloud", "category": "audio", "url": "https://soundcloud.com/{u}", "free": True},
    {"id": "keybase", "name": "Keybase", "category": "dev", "url": "https://keybase.io/{u}", "free": True},
    {"id": "hackernews", "name": "Hacker News", "category": "forum", "url": "https://news.ycombinator.com/user?id={u}", "free": True},
    {"id": "deviantart", "name": "DeviantArt", "category": "art", "url": "https://www.deviantart.com/{u}", "free": True},
    {"id": "namechk", "name": "Namechk (agrégateur)", "category": "agrégateur", "url": "https://namechk.com/", "free": True},
    {"id": "instantusername", "name": "Instant Username Search", "category": "agrégateur", "url": "https://instantusername.com/#/?query={u}", "free": True},
    {"id": "sherlock", "name": "Sherlock (local)", "category": "agrégateur", "url": "https://github.com/sherlock-project/sherlock", "free": True},
]

#: Phone-oriented lookups. ``{e164}`` (with +) and ``{raw}`` (digits only).
PHONE_ENGINES = [
    {"id": "whatsapp", "name": "WhatsApp", "category": "messagerie", "free": True,
     "url": "https://wa.me/{raw}",
     "description": "Ouvre la conversation WhatsApp si un compte existe pour ce numéro."},
    {"id": "whatsapp_web", "name": "WhatsApp Web", "category": "messagerie", "free": True,
     "url": "https://web.whatsapp.com/send?phone={raw}",
     "description": "Variante navigateur de WhatsApp, utile pour un poste de travail."},
    {"id": "google", "name": "Google", "category": "moteur", "free": True,
     "url": "https://www.google.com/search?q=%22{e164}%22",
     "description": "Recherche exacte du numéro : petites annonces, annuaires, forums."},
    # No deep-link: Truecaller documents only its on-site search bar, so the
    # number is copied to the clipboard rather than baked into a guessed URL.
    {"id": "truecaller", "name": "Truecaller", "category": "annuaire", "free": True,
     "url": "https://www.truecaller.com/", "deep_link": False, "copy": "{e164}",
     "description": "Annuaire collaboratif : collez le numéro dans la barre de recherche du site."},
    {"id": "syncme", "name": "Sync.me", "category": "annuaire", "free": True,
     "url": "https://sync.me/", "deep_link": False, "copy": "{e164}",
     "description": "Annuaire inverse adossé aux carnets d'adresses agrégés."},
    {"id": "numverify", "name": "Numverify", "category": "validation", "free": False,
     "url": "https://numverify.com/",
     "description": "Validation en temps réel : opérateur, type de ligne, portabilité."},
    {"id": "hiya", "name": "Hiya", "category": "annuaire", "free": True,
     "url": "https://www.hiya.com/",
     "description": "Identification d'appel et détection de fraude."},
]

#: People-search services. ``{q}`` is the URL-encoded query.
PERSON_ENGINES = [
    {"id": "google", "name": "Google", "category": "moteur", "free": True,
     "url": "https://www.google.com/search?q=%22{q}%22",
     "description": "Recherche exacte du nom complet entre guillemets."},
    {"id": "google_dork_cv", "name": "Google - CV & profils", "category": "dork", "free": True,
     "url": "https://www.google.com/search?q=%22{q}%22+(filetype:pdf+OR+filetype:doc)+CV",
     "description": "Cherche les CV et documents exposés mentionnant la personne."},
    {"id": "linkedin", "name": "LinkedIn", "category": "professionnel", "free": True,
     "url": "https://www.linkedin.com/search/results/all/?keywords={q}",
     "description": "Recherche de profils professionnels."},
    {"id": "facebook", "name": "Facebook", "category": "social", "free": True,
     "url": "https://www.facebook.com/search/top?q={q}",
     "description": "Recherche de personnes sur Facebook."},
    {"id": "x", "name": "X / Twitter", "category": "social", "free": True,
     "url": "https://x.com/search?q=%22{q}%22&src=typed_query&f=user",
     "description": "Recherche de comptes portant ce nom."},
    {"id": "instagram", "name": "Instagram", "category": "social", "free": True,
     "url": "https://www.instagram.com/explore/search/keyword/?q={q}",
     "description": "Recherche de comptes et de publications."},
    {"id": "pipl", "name": "Pipl", "category": "annuaire", "free": False,
     "url": "https://pipl.com/",
     "description": "Moteur de recherche de personnes le plus complet (payant)."},
    {"id": "thatsThem", "name": "That's Them", "category": "annuaire", "free": True,
     "url": "https://thatsthem.com/name/{q}",
     "description": "Annuaire de personnes, surtout efficace sur les États-Unis."},
    {"id": "youtube", "name": "YouTube", "category": "vidéo", "free": True,
     "url": "https://www.youtube.com/results?search_query={q}",
     "description": "Chaînes et vidéos associées au nom."},
]

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@([A-Za-z0-9.\-]+\.[A-Za-z]{2,})$")


def analyze_email(email: str) -> Dict[str, Any]:
    """Validate an email address and build every related lookup link.

    No network call is made here: the analysis is purely local so it stays
    fast and works offline. Use :func:`email_mx_records` separately for the
    DNS check.

    Args:
        email: Address to analyse.

    Returns:
        Mapping with the parsing result (``valid``, ``local_part``, ``domain``,
        ``gravatar_md5``, flags) and the ``engines`` list with ready-to-open
        URLs.
    """
    email = (email or "").strip().lower()
    match = _EMAIL_RE.match(email)
    domain = match.group(1).lower() if match else ""
    local_part = email.split("@")[0] if "@" in email else ""
    md5 = hashlib.md5(email.encode("utf-8")).hexdigest() if match else ""
    free_providers = {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "orange.fr",
                      "free.fr", "wanadoo.fr", "icloud.com", "proton.me", "protonmail.com"}
    result = {
        "input": email,
        "valid": bool(match),
        "local_part": local_part,
        "domain": domain,
        "gravatar_md5": md5,
        "gravatar_url": f"https://www.gravatar.com/avatar/{md5}?s=200&d=404" if md5 else "",
        "is_disposable": any(domain == d or domain.endswith("." + d) for d in DISPOSABLE_DOMAINS),
        "is_free_provider": domain in free_providers,
        "is_role_account": (local_part in ROLE_ACCOUNTS
                            or local_part.split(".")[0] in ROLE_ACCOUNTS
                            or local_part.split("-")[0] in ROLE_ACCOUNTS),
        "derived_usernames": sorted({local_part, local_part.split(".")[0], local_part.replace(".", "")}) if local_part else [],
    }
    if not match:
        result["error"] = "adresse invalide : format attendu nom@domaine.tld"
        result["engines"] = []
        return result
    result["engines"] = [_fill(engine, {"email": email, "domain": domain, "md5": md5}) for engine in EMAIL_ENGINES]
    return result


def email_mx_records(domain: str) -> Dict[str, Any]:
    """Look up the MX records of a domain to test whether it can receive mail.

    Args:
        domain: Domain name to query.

    Returns:
        Mapping with ``domain``, ``exists``, the ``records`` sorted by
        preference, and an ``error`` key when DNS resolution failed.
    """
    domain = (domain or "").strip().lower()
    if not domain or "." not in domain:
        return {"domain": domain, "exists": False, "records": [], "error": "domaine invalide"}
    try:
        import dns.resolver
        answers = dns.resolver.resolve(domain, "MX", lifetime=8)
        records = sorted(
            ({"preference": int(r.preference), "host": str(r.exchange).rstrip(".")} for r in answers),
            key=lambda r: r["preference"],
        )
        return {"domain": domain, "exists": bool(records), "records": records,
                "provider_guess": _guess_mail_provider(records)}
    except Exception as exc:  # noqa: BLE001 - NXDOMAIN and timeouts are normal
        return {"domain": domain, "exists": False, "records": [], "error": type(exc).__name__}


def _guess_mail_provider(records: List[Dict[str, Any]]) -> str:
    """Infer the mail provider from MX host names."""
    joined = " ".join(r["host"].lower() for r in records)
    for marker, name in (("google", "Google Workspace"), ("outlook", "Microsoft 365"),
                         ("hotmail", "Microsoft 365"), ("zoho", "Zoho Mail"),
                         ("protonmail", "Proton Mail"), ("ovh", "OVH"), ("orange", "Orange")):
        if marker in joined:
            return name
    return ""


def analyze_phone(number: str, default_region: str = "SN") -> Dict[str, Any]:
    """Parse a phone number and build every related lookup link.

    Args:
        number: Number in any common format, with or without country code.
        default_region: ISO country code assumed when the number has no ``+``.
            Defaults to Senegal.

    Returns:
        Mapping with the E.164 form, country, number type, national format and
        the ``engines`` list, or an ``error`` key when the number is unusable.
    """
    number = (number or "").strip()
    if not number:
        return {"input": "", "valid": False, "error": "numéro vide", "engines": []}
    try:
        import phonenumbers
        from phonenumbers import NumberParseException
        try:
            parsed = phonenumbers.parse(number, default_region.upper() if default_region else None)
        except NumberParseException:
            parsed = phonenumbers.parse(number, None)
        e164 = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
        raw = e164.lstrip("+")
        region = phonenumbers.region_code_for_number(parsed) or ""
        types = {0: "fixe ou mobile", 1: "mobile", 2: "fixe", 3: "numéro court",
                 4: "surcoût (premium)", 5: "gratuit", 6: "partagé", 7: "voip", 9: "inconnu"}
        carrier = ""
        try:
            from phonenumbers import carrier as pn_carrier
            carrier = pn_carrier.name_for_number(parsed, "fr") or ""
        except Exception:  # noqa: BLE001 - the carrier metadata is optional
            carrier = ""
        result = {
            "input": number,
            "valid": phonenumbers.is_valid_number(parsed),
            "possible": phonenumbers.is_possible_number(parsed),
            "e164": e164,
            "raw": raw,
            "national": phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.NATIONAL),
            "international": phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.INTERNATIONAL),
            "country_code": parsed.country_code,
            "region": region,
            "type": types.get(phonenumbers.number_type(parsed), "inconnu"),
            "carrier_guess": carrier,
            "whatsapp_url": f"https://wa.me/{raw}",
        }
        result["engines"] = [_fill(engine, {"e164": e164, "raw": raw,
                                            "countrycode": str(parsed.country_code)}) for engine in PHONE_ENGINES]
        return result
    except Exception as exc:  # noqa: BLE001
        return {"input": number, "valid": False, "error": f"numéro illisible : {exc}", "engines": []}


def analyze_username(username: str) -> Dict[str, Any]:
    """Build profile URLs for a username across the monitored platforms.

    Args:
        username: Handle to probe.

    Returns:
        Mapping with the normalised username, a few derived variants and the
        ``engines`` list, or an ``error`` key when the input is unusable.
    """
    username = (username or "").strip().lstrip("@")
    if not username:
        return {"input": "", "valid": False, "error": "pseudo vide", "engines": []}
    if not re.match(r"^[A-Za-z0-9._\-]{2,40}$", username):
        return {"input": username, "valid": False,
                "error": "caractères inattendus : un pseudo contient des lettres, chiffres, . _ ou -",
                "engines": []}
    variants = sorted({username, username.lower(), username.replace(".", "_"), username.replace(".", "")})
    return {
        "input": username,
        "valid": True,
        "variants": variants,
        "engines": [_fill(engine, {"u": username}) for engine in USERNAME_ENGINES],
    }


def analyze_person(name: str, extras: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Build people-search queries for a full name, optionally narrowed.

    Args:
        name: Full name of the person.
        extras: Optional narrowing hints such as ``city``, ``country``,
            ``employer`` or ``email``; each is appended to the query.

    Returns:
        Mapping with the composed query and the ``engines`` list, or an
        ``error`` key when the name is empty.
    """
    from urllib.parse import quote_plus
    name = (name or "").strip()
    if not name:
        return {"input": "", "valid": False, "error": "nom vide", "engines": []}
    hints = [str(v).strip() for v in (extras or {}).values() if v and str(v).strip()]
    query = " ".join([name] + hints)
    return {
        "input": name,
        "query": query,
        "hints": hints,
        "valid": True,
        "engines": [_fill(engine, {"q": quote_plus(query)}) for engine in PERSON_ENGINES],
    }


def _fill(engine: Dict[str, Any], values: Dict[str, str]) -> Dict[str, Any]:
    """Copy an engine descriptor and substitute its URL placeholders.

    Args:
        engine: Descriptor from one of the ``*_ENGINES`` registries.
        values: Placeholder replacements.

    Returns:
        A new descriptor with ``url`` filled in. The registries are never
        mutated, so concurrent requests cannot interfere.
    """
    out = dict(engine)
    for field in ("url", "copy"):
        if field not in out:
            continue
        text = out[field]
        for key, val in values.items():
            text = text.replace("{" + key + "}", str(val))
        out[field] = text
    return out


def identity_catalog() -> Dict[str, Any]:
    """Describe every identity tool so the UI can render its own help.

    Returns:
        Mapping keyed by tool with the engine lists and a usage summary.
    """
    return {
        "email": {"title": "Adresse e-mail", "engines": EMAIL_ENGINES,
                  "how": "Validation locale du format, détection des domaines jetables et des comptes de rôle, "
                         "hash Gravatar, puis vérification DNS des MX pour savoir si le domaine reçoit du courrier."},
        "phone": {"title": "Numéro de téléphone / WhatsApp", "engines": PHONE_ENGINES,
                  "how": "Normalisation E.164, pays et type de ligne via libphonenumber, lien direct WhatsApp "
                         "et annuaires inverses. Sans préfixe +, le pays par défaut est le Sénégal."},
        "username": {"title": "Pseudo / nom d'utilisateur", "engines": USERNAME_ENGINES,
                     "how": "Un même pseudo est souvent réutilisé : la liste ouvre le profil correspondant sur "
                            "chaque plateforme. Les agrégateurs testent des centaines de sites d'un coup."},
        "person": {"title": "Nom de personne", "engines": PERSON_ENGINES,
                   "how": "Recherche exacte du nom, éventuellement restreinte par ville, pays ou employeur."},
    }
