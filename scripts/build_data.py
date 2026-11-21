#!/usr/bin/env python3
"""
Gares françaises hors Île-de-France atteignables en train en <= 1h30 depuis Paris.

Usage (depuis la racine du projet) :
    python scripts/build_data.py                 # télécharge les données fraîches puis calcule
    python scripts/build_data.py --no-download   # réutilise data/raw/
    python scripts/build_data.py --date 2026-12-01

Uniquement la bibliothèque standard Python (>= 3.9).

Sorties :
    public/data/gares.geojson   (gares atteignables + métadonnées + gares parisiennes)
    public/data/gares.csv       (même contenu, une ligne par gare)
    data/gares_limites.csv      (gares entre 85 et 95 min, pour contrôle manuel)
"""
from __future__ import annotations

import argparse
import bisect
import csv
import datetime as dt
import gzip
import io
import json
import math
import re
import shutil
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
OUT = ROOT / "public" / "data"

# --------------------------------------------------------------------------------------
# Sources
# --------------------------------------------------------------------------------------
# GTFS : SNCF publie depuis 2025 un flux unique TGV INOUI / OUIGO / Intercités / TER.
# Les anciens flux séparés (export_gtfs_voyages.zip, export-ter-gtfs-last.zip,
# export-intercites-gtfs-last.zip) ne sont plus mis à jour depuis janvier 2025 ; on peut
# néanmoins en ajouter ici, ils seront fusionnés (identifiants préfixés, trains dédoublonnés).
GTFS_FEEDS = [
    {
        "id": "sncf",
        "title": "Réseau SNCF TGV, Intercités et TER (GTFS) – transport.data.gouv.fr",
        "page": "https://transport.data.gouv.fr/datasets/reseau-sncf-tgv-intercites-et-ter",
        "url": "https://eu.ftp.opendatasoft.com/sncf/plandata/Export_OpenData_SNCF_GTFS_NewTripId.zip",
        "file": "sncf_gtfs.zip",
    },
]
SRC_GARES = {
    "title": "Liste des gares – SNCF Open Data",
    "page": "https://ressources.data.sncf.com/explore/dataset/liste-des-gares/",
    "url": "https://ressources.data.sncf.com/api/explore/v2.1/catalog/datasets/liste-des-gares/exports/csv?delimiter=%3B",
    "file": "liste-des-gares.csv",
}
SRC_GARES_VOY = {
    "title": "Gares de voyageurs – SNCF Open Data (complément)",
    "page": "https://ressources.data.sncf.com/explore/dataset/gares-de-voyageurs/",
    "url": "https://ressources.data.sncf.com/api/explore/v2.1/catalog/datasets/gares-de-voyageurs/exports/csv?delimiter=%3B",
    "file": "gares-de-voyageurs.csv",
}
SRC_IDF = {
    "title": "Contours administratifs des régions 2025 (Etalab, d'après IGN ADMIN EXPRESS)",
    "page": "https://www.data.gouv.fr/fr/datasets/contours-administratifs/",
    "url": "https://etalab-datasets.geo.data.gouv.fr/contours-administratifs/2025/geojson/regions-50m.geojson.gz",
    "file": "regions-50m.geojson.gz",
}
SRC_COMMUNES = {
    "title": "Découpage administratif (communes, départements) – API Géo / INSEE",
    "page": "https://geo.api.gouv.fr/decoupage-administratif",
    "url": "https://geo.api.gouv.fr/communes?fields=code,nom,codeDepartement&format=json",
    "file": "communes.json",
}
SRC_DEPTS = {
    "title": "Départements – API Géo / INSEE",
    "page": "https://geo.api.gouv.fr/decoupage-administratif",
    "url": "https://geo.api.gouv.fr/departements?fields=code,nom&format=json",
    "file": "departements.json",
}
SRC_CALENDRIER = {
    "title": "Calendrier scolaire – Ministère de l'Éducation nationale",
    "page": "https://data.education.gouv.fr/explore/dataset/fr-en-calendrier-scolaire/",
    "url": "https://data.education.gouv.fr/api/explore/v2.1/catalog/datasets/fr-en-calendrier-scolaire/exports/json"
           "?where=zones%20in%20(%22Zone%20A%22%2C%22Zone%20B%22%2C%22Zone%20C%22)"
           "&select=description%2Cstart_date%2Cend_date%2Czones",
    "file": "calendrier-scolaire.json",
}

# --------------------------------------------------------------------------------------
# Paramètres de calcul
# --------------------------------------------------------------------------------------
MAX_MIN = 90            # seuil d'export
ANALYSIS_MAX_MIN = 95   # on calcule un peu au-delà pour lister les gares limites
MIN_TRANSFER_MIN = 5    # temps de correspondance minimal
IDF_REGION_CODE = "11"
IDF_DEPTS = {"75", "77", "78", "91", "92", "93", "94", "95"}

# Gares parisiennes de départ (UIC -> code court). Montparnasse a deux zones d'arrêt
# dans le GTFS (Hall 1-2 et Vaugirard) : même gare.
PARIS_STATIONS = {
    "NORD": {"label": "Paris Nord", "uics": ["87271007"]},
    "EST": {"label": "Paris Est", "uics": ["87113001"]},
    "LYON": {"label": "Paris Gare de Lyon", "uics": ["87686006"]},
    "BERCY": {"label": "Paris Bercy", "uics": ["87686667"]},
    "AUSTERLITZ": {"label": "Paris Austerlitz", "uics": ["87547000"]},
    "MONTPARNASSE": {"label": "Paris Montparnasse", "uics": ["87391003", "87391102"]},
    "SAINT-LAZARE": {"label": "Paris Saint-Lazare", "uics": ["87384008"]},
}
UIC_TO_PARIS = {u: code for code, s in PARIS_STATIONS.items() for u in s["uics"]}

# Le mode est encodé dans le stop_id GTFS : "StopPoint:OCE<mode>-<UIC>".
MODE_LABELS = {
    "TGV INOUI": "TGV INOUI",
    "OUIGO": "OUIGO",
    "INTERCITES": "Intercités",
    "INTERCITES de nuit": "Intercités de nuit",
    "Train TER": "TER",
    "TramTrain": "Tram-train",
    "ICE": "ICE",
    "Lyria": "TGV Lyria",
    "Train": "Train",
}
# Exclus : autocars ("Car TER", "Car à réservation") et navettes routières ("Navette").
EXCLUDED_MODE_PREFIXES = ("Car", "Navette")

# --------------------------------------------------------------------------------------
# Voiture (sans trafic) : service de calcul d'itinéraire / isochrones de l'IGN (Géoplateforme)
# Vitesses théoriques par type de route (BD TOPO), sans congestion.
# --------------------------------------------------------------------------------------
CAR_ORIGIN = {"nom": "Paris – point zéro des routes de France (parvis de Notre-Dame)",
              "lat": 48.853410, "lon": 2.348800}
CAR_API = "https://data.geopf.fr/navigation"
CAR_RESOURCE = "bdtopo-valhalla"      # même moteur pour les isochrones et les itinéraires
CAR_MAX_MIN = 105                     # seuil voiture : 1h45 (le train reste à MAX_MIN = 1h30)
ISO_STEPS = list(range(30, CAR_MAX_MIN + 1, 5))   # isochrones affichées (curseur du site)
ISO_CANDIDATES_MIN = CAR_MAX_MIN + 10  # gares candidates : dans l'isochrone seuil + 10 min
CAR_DIR = RAW / "voiture"             # cache des réponses de l'API
CAR_REQUEST_PAUSE_S = 0.25            # ~4 requêtes/s max, pour ménager le service public
SRC_ROUTAGE = {
    "title": "Itinéraires et isochrones voiture – IGN Géoplateforme (BD TOPO, Valhalla)",
    "page": "https://geoservices.ign.fr/documentation/services/services-geoplateforme/itineraire",
    "url": f"{CAR_API}/itineraire",
    "file": "voiture/routes.json",
}
# Contrôles d'ordre de grandeur voiture (distance routière depuis Paris) : jusqu'à ~145 km
# d'autoroute on doit arriver en 1h45 (Reims, Orléans) ; au-delà de ~150 km ou avec une
# sortie de Paris lente (Rouen par l'A13, Amiens), non.
CAR_EXPECTED = [("chartres", True), ("creil", True), ("compiegne", True), ("beauvais", True),
                ("reims", True), ("orleans", True),
                ("rouen rive droite", False), ("amiens", False), ("le mans", False), ("lille", False)]

# Contrôles de cohérence (étape 3) : (motif de nom normalisé, durée attendue ~min)
EXPECTED_PRESENT = [
    ("creil", 25), ("compiegne", 40), ("reims", 45), ("lille flandres", 60),
    ("lille europe", 60), ("arras", 50), ("amiens", 65), ("rouen rive droite", 70),
    ("orleans", 60), ("chartres", 60), ("le mans", 55), ("vendome", None),
    ("saint pierre des corps", 60),
]
EXPECTED_ABSENT = ["lyon part dieu", "strasbourg", "nantes", "versailles", "melun",
                   "marne la vallee"]


# --------------------------------------------------------------------------------------
# Utilitaires
# --------------------------------------------------------------------------------------
def log(*a):
    print(*a, flush=True)


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\bst\b", "saint", s)
    s = re.sub(r"\bste\b", "sainte", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


SMALL_WORDS = {"de", "des", "du", "la", "le", "les", "sur", "sous", "en", "et", "aux", "au", "lès", "lez"}


def nice_commune(s: str) -> str:
    """'SAINT-PIERRE-DES-CORPS' -> 'Saint-Pierre-des-Corps'."""
    if not s:
        return s
    parts = re.split(r"([\s\-'])", s.lower())
    out = []
    for i, p in enumerate(parts):
        if p in SMALL_WORDS and i > 0:
            out.append(p)
        elif p in ("l", "d") and i + 1 < len(parts) and parts[i + 1] == "'":
            out.append(p if i > 0 else p.upper())
        else:
            out.append(p[:1].upper() + p[1:])
    return "".join(out)


def hms(t: str) -> int:
    h, m, s = t.split(":")
    return int(h) * 3600 + int(m) * 60 + int(s)


def fmt_hm(sec: int) -> str:
    sec = int(sec)
    return f"{(sec // 3600) % 24:02d}:{(sec % 3600) // 60:02d}"


def haversine_m(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def download(src: dict, today: str) -> None:
    dest = RAW / src["file"]
    log(f"  ↓ {src['url']}")
    req = urllib.request.Request(src["url"], headers={"User-Agent": "gares-paris-1h30/1.0"})
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(req, timeout=300) as r, open(tmp, "wb") as f:
        shutil.copyfileobj(r, f)
    tmp.replace(dest)


def extraction_date(path: Path) -> str:
    return dt.datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d")


# --------------------------------------------------------------------------------------
# Point dans polygone (ray casting, trous gérés)
# --------------------------------------------------------------------------------------
def _in_ring(x, y, ring):
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


class Region:
    def __init__(self, geometry: dict):
        polys = geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]
        self.polys = polys
        xs = [p[0] for poly in polys for p in poly[0]]
        ys = [p[1] for poly in polys for p in poly[0]]
        self.bbox = (min(xs), min(ys), max(xs), max(ys))

    def contains(self, lon, lat) -> bool:
        x0, y0, x1, y1 = self.bbox
        if not (x0 <= lon <= x1 and y0 <= lat <= y1):
            return False
        for poly in self.polys:
            if _in_ring(lon, lat, poly[0]) and not any(_in_ring(lon, lat, h) for h in poly[1:]):
                return True
        return False


def load_idf() -> Region:
    with gzip.open(RAW / SRC_IDF["file"], "rt", encoding="utf-8") as f:
        fc = json.load(f)
    for feat in fc["features"]:
        if str(feat["properties"].get("code")) == IDF_REGION_CODE:
            return Region(feat["geometry"])
    raise SystemExit("Région Île-de-France (code 11) introuvable dans le fichier de contours")


# --------------------------------------------------------------------------------------
# Référentiel des gares SNCF
# --------------------------------------------------------------------------------------
def load_gares():
    """Retourne dict UIC(8) -> {nom, commune, departement, lat, lon, source}."""
    gares = {}
    with open(RAW / SRC_GARES["file"], encoding="utf-8-sig", newline="") as f:
        rows = sorted(csv.DictReader(f, delimiter=";"), key=lambda r: r["voyageurs"] != "O")
    for r in rows:
        uic = (r.get("code_uic") or "").strip()[:8]
        if not uic or uic in gares:
            continue
        try:
            lat, lon = float(r["y_wgs84"]), float(r["x_wgs84"])
        except (TypeError, ValueError):
            continue
        gares[uic] = {
            "nom": r["libelle"], "commune": nice_commune(r["commune"]),
            "departement": nice_commune(r["departemen"]), "lat": lat, "lon": lon,
            "insee": None, "source": "liste-des-gares",
        }
    # Complément : gares-de-voyageurs (plus récent, contient le code INSEE)
    with open(RAW / SRC_GARES_VOY["file"], encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f, delimiter=";"):
            try:
                lat, lon = [float(v) for v in r["position_geographique"].split(",")]
            except (ValueError, AttributeError):
                continue
            for uic in re.split(r"[;,\s]+", r.get("codes_uic") or ""):
                uic = uic.strip()[:8]
                if not uic:
                    continue
                if uic in gares:
                    gares[uic]["insee"] = gares[uic]["insee"] or r["codeinsee"]
                else:
                    gares[uic] = {"nom": r["nom"], "commune": None, "departement": None,
                                  "lat": lat, "lon": lon, "insee": r["codeinsee"],
                                  "source": "gares-de-voyageurs"}
    # Noms officiels (accentués) de commune / département via le code INSEE
    communes, depts = {}, {}
    if (RAW / SRC_COMMUNES["file"]).exists():
        communes = {c["code"]: c for c in json.loads((RAW / SRC_COMMUNES["file"]).read_text(encoding="utf-8"))}
    if (RAW / SRC_DEPTS["file"]).exists():
        depts = {d["code"]: d["nom"] for d in json.loads((RAW / SRC_DEPTS["file"]).read_text(encoding="utf-8"))}
    for g in gares.values():
        c = communes.get(g["insee"] or "")
        if c:
            g["commune"] = c["nom"]
            g["dept_code"] = c.get("codeDepartement")
            g["departement"] = depts.get(c.get("codeDepartement"), g["departement"])
        else:
            g["dept_code"] = None
    return gares


def pareto(journeys):
    """Trajets non dominés : aucun autre ne part plus tard (ou en même temps) ET arrive
    plus tôt (ou en même temps). Un seul trajet conservé par couple (départ, arrivée)."""
    best = {}
    for j in journeys:
        k = (j["dep"], j["arr"])
        if k not in best or (j["transfer"] is not None) < (best[k]["transfer"] is not None):
            best[k] = j
    out, min_arr = [], math.inf
    for j in sorted(best.values(), key=lambda j: (-j["dep"], j["arr"])):
        if j["arr"] < min_arr:
            out.append(j)
            min_arr = j["arr"]
    return sorted(out, key=lambda j: j["dep"])


# --------------------------------------------------------------------------------------
# GTFS
# --------------------------------------------------------------------------------------
STOP_RE = re.compile(r"^StopPoint:OCE(.+)-(\w+)$")


class Trip:
    __slots__ = ("key", "mode", "number", "stops")

    def __init__(self, key, mode, number):
        self.key, self.mode, self.number = key, mode, number
        self.stops = []  # (seq, uic, arr, dep, pickup_ok, drop_ok)


def open_feed(feed):
    z = zipfile.ZipFile(RAW / feed["file"])

    def reader(name):
        return csv.DictReader(io.TextIOWrapper(z.open(name), encoding="utf-8-sig", newline=""))
    return z, reader


def feed_info(feed):
    z, reader = open_feed(feed)
    info = {}
    if "feed_info.txt" in z.namelist():
        info = next(reader("feed_info.txt"), {}) or {}
    active_by_date = defaultdict(set)
    if "calendar_dates.txt" in z.namelist():
        for r in reader("calendar_dates.txt"):
            if r["exception_type"] == "1":
                active_by_date[r["date"]].add(r["service_id"])
    # calendar.txt (absent du flux SNCF actuel, géré pour les autres flux)
    calendar = []
    if "calendar.txt" in z.namelist():
        calendar = list(reader("calendar.txt"))
    removed = defaultdict(set)
    if "calendar_dates.txt" in z.namelist() and calendar:
        for r in reader("calendar_dates.txt"):
            if r["exception_type"] == "2":
                removed[r["date"]].add(r["service_id"])
    trips_per_service = Counter(r["service_id"] for r in reader("trips.txt"))
    info["_trips_per_service"] = trips_per_service
    return z, reader, info, active_by_date, calendar, removed


def services_on(date: dt.date, active_by_date, calendar, removed):
    ds = date.strftime("%Y%m%d")
    wd = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"][date.weekday()]
    s = set(active_by_date.get(ds, set()))
    for c in calendar:
        if c["start_date"] <= ds <= c["end_date"] and c.get(wd) == "1":
            s.add(c["service_id"])
    return s - removed.get(ds, set())


def load_trips_for_date(feeds_ctx, date: dt.date):
    """Charge les trips ferroviaires actifs à la date donnée, tous flux fusionnés."""
    trips = []
    stop_names = {}
    stop_coords = {}
    mode_counter = Counter()
    for feed, (z, reader, info, abd, cal, rem) in feeds_ctx:
        services = services_on(date, abd, cal, rem)
        # stops : stop_id -> (uic, mode)
        stop_map = {}
        parent_uic = {}
        stop_rows = list(reader("stops.txt"))
        for r in stop_rows:
            if r.get("location_type") == "1":
                m = re.search(r"(\d{8})$", r["stop_id"])
                if m:
                    parent_uic[r["stop_id"]] = m.group(1)
                    stop_names.setdefault(m.group(1), r["stop_name"])
                    stop_coords.setdefault(m.group(1), (float(r["stop_lat"]), float(r["stop_lon"])))
        for r in stop_rows:
            if r.get("location_type", "0") in ("0", ""):
                m = STOP_RE.match(r["stop_id"])
                mode = m.group(1) if m else "?"
                uic = parent_uic.get(r.get("parent_station")) or (m.group(2)[-8:] if m else None)
                if uic:
                    stop_map[r["stop_id"]] = (uic, mode)
                    stop_names.setdefault(uic, r["stop_name"])
                    stop_coords.setdefault(uic, (float(r["stop_lat"]), float(r["stop_lon"])))
        route_type = {r["route_id"]: r["route_type"] for r in reader("routes.txt")}
        active = {}
        for r in reader("trips.txt"):
            if r["service_id"] in services:
                active[r["trip_id"]] = (r["route_id"], r.get("trip_headsign") or r.get("trip_short_name") or "")
        by_trip = {}
        for r in reader("stop_times.txt"):
            tid = r["trip_id"]
            if tid not in active:
                continue
            sm = stop_map.get(r["stop_id"])
            if not sm:
                continue
            t = by_trip.get(tid)
            if t is None:
                t = by_trip[tid] = Trip(f"{feed['id']}:{tid}", sm[1], active[tid][1])
            arr = hms(r["arrival_time"] or r["departure_time"])
            dep = hms(r["departure_time"] or r["arrival_time"])
            t.stops.append((int(r["stop_sequence"]), sm[0], arr, dep,
                            r.get("pickup_type", "0") != "1", r.get("drop_off_type", "0") != "1"))
        for tid, t in by_trip.items():
            mode_counter[t.mode] += 1
            if t.mode.startswith(EXCLUDED_MODE_PREFIXES):
                continue
            if route_type.get(active[tid][0]) not in ("0", "1", "2", "100", "101", "102", "103",
                                                      "105", "106", "107", "109", "116", "117"):
                # route_type non ferroviaire (3 = bus) : exclu
                continue
            t.stops.sort()
            trips.append(t)
    return trips, stop_names, stop_coords, mode_counter


def dedupe_trips(trips):
    """En cas de flux fusionnés : supprime les doublons (même n° de train, mêmes horaires)."""
    seen, out = set(), []
    for t in trips:
        sig = (t.number, tuple((s[1], s[3]) for s in t.stops))
        if sig in seen:
            continue
        seen.add(sig)
        out.append(t)
    return out


# --------------------------------------------------------------------------------------
# Choix du mardi type
# --------------------------------------------------------------------------------------
def load_holidays():
    path = RAW / SRC_CALENDRIER["file"]
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for r in data:
        try:
            # dates en UTC (minuit heure de Paris -> 22h/23h UTC la veille)
            s = (dt.datetime.fromisoformat(r["start_date"]) + dt.timedelta(hours=3)).date()
            e = (dt.datetime.fromisoformat(r["end_date"]) + dt.timedelta(hours=3)).date()
        except Exception:
            continue
        if s < e:  # ignore les entrées "début des vacances" ponctuelles
            out.append((s, e, r.get("description"), r.get("zones")))
    return out


def in_holidays(d, holidays):
    for s, e, desc, z in holidays or []:
        if s <= d < e:
            return f"{desc} ({z})"
    return None


def count_active_trips(feeds_ctx, date):
    n = 0
    for feed, (z, reader, info, abd, cal, rem) in feeds_ctx:
        tps = info["_trips_per_service"]
        n += sum(tps[s] for s in services_on(date, abd, cal, rem))
    return n


# --------------------------------------------------------------------------------------
# Calcul des trajets
# --------------------------------------------------------------------------------------
def compute(trips, limit_s, min_transfer_s):
    """
    Pour chaque trip quittant une gare parisienne : trajets directs et trajets avec une
    correspondance (même gare, >= min_transfer_s, gare de correspondance hors Paris).
    Retourne dest_uic -> {(paris_code, final_trip_key): meilleur trajet}.
    """
    # Index des départs par gare : uic -> liste triée (dep, trip_idx, pos)
    deps = defaultdict(list)
    for ti, t in enumerate(trips):
        for pos, s in enumerate(t.stops[:-1]):
            if s[4]:
                deps[s[1]].append((s[3], ti, pos))
    for v in deps.values():
        v.sort()

    results = defaultdict(dict)

    def record(dest, paris, final_key, j):
        cur = results[dest].get((paris, final_key))
        if cur is None or (j["dur"], j["transfer"] is not None, -j["dep"]) < \
                (cur["dur"], cur["transfer"] is not None, -cur["dep"]):
            results[dest][(paris, final_key)] = j

    n_first = Counter()
    for puic, paris in UIC_TO_PARIS.items():
        for t0, ti, pos in deps.get(puic, []):
            trip = trips[ti]
            # un train qui repasse par une autre gare parisienne plus loin : on part de la dernière
            if any(s[1] in UIC_TO_PARIS for s in trip.stops[pos + 1:]):
                continue
            n_first[paris] += 1
            for k in range(pos + 1, len(trip.stops)):
                _, uic, arr, dep, pk, dr = trip.stops[k]
                dur = arr - t0
                if dur > limit_s:
                    break
                if not dr or uic in UIC_TO_PARIS:
                    continue
                leg1 = {"trip": ti, "from": puic, "to": uic, "dep": t0, "arr": arr}
                record(uic, paris, trip.key, {"dur": dur, "dep": t0, "arr": arr, "paris": paris,
                                              "paris_uic": puic, "legs": [leg1], "transfer": None})
                # correspondance à `uic`
                if dur + min_transfer_s >= limit_s:
                    continue
                lst = deps.get(uic, [])
                i = bisect.bisect_left(lst, (arr + min_transfer_s, -1, -1))
                while i < len(lst) and lst[i][0] < t0 + limit_s:
                    d2, tj, posj = lst[i]
                    i += 1
                    if tj == ti:
                        continue
                    trip2 = trips[tj]
                    for m in range(posj + 1, len(trip2.stops)):
                        _, uic2, arr2, _, _, dr2 = trip2.stops[m]
                        dur2 = arr2 - t0
                        if dur2 > limit_s:
                            break
                        if not dr2 or uic2 in UIC_TO_PARIS or uic2 == uic:
                            continue
                        leg2 = {"trip": tj, "from": uic, "to": uic2, "dep": d2, "arr": arr2}
                        record(uic2, paris, trip2.key, {"dur": dur2, "dep": t0, "arr": arr2,
                                                        "paris": paris, "paris_uic": puic,
                                                        "legs": [leg1, leg2], "transfer": uic,
                                                        "wait": d2 - arr})
    return results, n_first


# --------------------------------------------------------------------------------------
# Voiture
# --------------------------------------------------------------------------------------
UA = "gares-paris-1h30/1.0"


def http_json(url, retries=4):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise
        except (urllib.error.URLError, TimeoutError):
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise


def car_isochrone(minutes, refresh):
    path = CAR_DIR / f"isochrone_{minutes}.json"
    if path.exists() and not refresh:
        return json.loads(path.read_text(encoding="utf-8"))
    url = f"{CAR_API}/isochrone?" + urllib.parse.urlencode({
        "resource": CAR_RESOURCE, "point": f"{CAR_ORIGIN['lon']},{CAR_ORIGIN['lat']}",
        "costType": "time", "costValue": minutes, "timeUnit": "minute", "profile": "car",
        "direction": "departure", "geometryFormat": "geojson"})
    j = http_json(url)
    path.write_text(json.dumps(j), encoding="utf-8")
    time.sleep(CAR_REQUEST_PAUSE_S)
    return j


class CarRouter:
    """Temps de trajet voiture origine -> point, avec cache disque (data/raw/voiture/routes.json)."""

    def __init__(self, refresh):
        self.path = CAR_DIR / "routes.json"
        self.cache = {}
        if self.path.exists() and not refresh:
            self.cache = json.loads(self.path.read_text(encoding="utf-8"))
        self.requests = 0
        self.failures = []

    def route(self, lon, lat, label=""):
        k = f"{CAR_RESOURCE}|{CAR_ORIGIN['lon']},{CAR_ORIGIN['lat']}|{lon:.5f},{lat:.5f}"
        if k in self.cache:
            return self.cache[k]
        url = f"{CAR_API}/itineraire?" + urllib.parse.urlencode({
            "resource": CAR_RESOURCE, "start": f"{CAR_ORIGIN['lon']},{CAR_ORIGIN['lat']}",
            "end": f"{lon:.6f},{lat:.6f}", "profile": "car", "optimization": "fastest",
            "getSteps": "false", "timeUnit": "minute", "distanceUnit": "kilometer"})
        try:
            j = http_json(url)
            val = {"min": float(j["duration"]), "km": float(j["distance"]), "version": j.get("resourceVersion")}
        except Exception as e:
            self.failures.append((label, str(e)))
            return None
        self.cache[k] = val
        self.requests += 1
        if self.requests % 50 == 0:
            self.save()
            log(f"     … {self.requests} itinéraires calculés")
        time.sleep(CAR_REQUEST_PAUSE_S)
        return val

    def save(self):
        self.path.write_text(json.dumps(self.cache, ensure_ascii=False), encoding="utf-8")


def simplify_ring(pts, tol):
    """Douglas-Peucker (tolérance en degrés) ; renvoie None si l'anneau dégénère."""
    n = len(pts)
    if n < 5:
        return pts
    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        a, b = stack.pop()
        ax, ay = pts[a][0], pts[a][1]
        dx, dy = pts[b][0] - ax, pts[b][1] - ay
        l2 = dx * dx + dy * dy
        imax, dmax = -1, 0.0
        for i in range(a + 1, b):
            px, py = pts[i][0] - ax, pts[i][1] - ay
            t = 0.0 if l2 == 0 else max(0.0, min(1.0, (px * dx + py * dy) / l2))
            d = (px - t * dx) ** 2 + (py - t * dy) ** 2
            if d > dmax:
                imax, dmax = i, d
        if dmax > tol * tol:
            keep[imax] = True
            stack += [(a, imax), (imax, b)]
    out = [[round(p[0], 4), round(p[1], 4)] for p, k in zip(pts, keep) if k]
    return out if len(out) >= 4 else None


def simplify_geometry(geom, tol=0.002):
    polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
    out = []
    for poly in polys:
        rings = [simplify_ring(r, tol) for r in poly]
        if rings[0] is None:
            continue
        out.append([r for r in rings if r is not None])
    return {"type": "MultiPolygon", "coordinates": out}


def load_station_groups():
    """Gares de voyageurs actuelles (une ligne = une gare, éventuellement plusieurs codes UIC)."""
    groups = []
    with open(RAW / SRC_GARES_VOY["file"], encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f, delimiter=";"):
            codes = [c.strip()[:8] for c in re.split(r"[;,\s]+", r.get("codes_uic") or "") if c.strip()]
            if codes:
                groups.append(codes)
    return groups


# --------------------------------------------------------------------------------------
# Programme principal
# --------------------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-download", action="store_true", help="réutiliser les fichiers de data/raw/")
    ap.add_argument("--date", help="date AAAA-MM-JJ à utiliser (défaut : mardi type choisi automatiquement)")
    ap.add_argument("--today", help="date de référence pour le choix du mardi (défaut : aujourd'hui)")
    ap.add_argument("--refresh-car", action="store_true",
                    help="recalculer isochrones et temps voiture (sinon cache data/raw/voiture/)")
    ap.add_argument("--no-car", action="store_true", help="ne pas calculer le volet voiture")
    ap.add_argument("--refresh-immo", action="store_true",
                    help="retélécharger DVF, loyers et contours (sinon cache data/raw/immo/)")
    ap.add_argument("--no-immo", action="store_true", help="ne pas calculer la couche prix immobiliers")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    RAW.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    today = dt.date.fromisoformat(args.today) if args.today else dt.date.today()

    all_sources = GTFS_FEEDS + [SRC_GARES, SRC_GARES_VOY, SRC_IDF, SRC_COMMUNES, SRC_DEPTS, SRC_CALENDRIER]
    if not args.no_download:
        log("1. Téléchargement des données brutes → data/raw/")
        for src in all_sources:
            try:
                download(src, today.isoformat())
            except Exception as e:
                if (RAW / src["file"]).exists():
                    log(f"    ! échec ({e}) – on garde la copie existante")
                elif src in (SRC_CALENDRIER, SRC_COMMUNES, SRC_DEPTS):
                    log(f"    ! source optionnelle indisponible ({e}) – on continue sans")
                else:
                    raise
    else:
        log("1. Réutilisation des données de data/raw/")
    for src in all_sources:
        p = RAW / src["file"]
        src["extracted"] = extraction_date(p) if p.exists() else None

    # ---- Référentiels
    log("2. Chargement des référentiels")
    gares = load_gares()
    idf = load_idf()
    log(f"   {len(gares)} gares SNCF (UIC) chargées ; contour IDF : {sum(len(p[0]) for p in idf.polys)} sommets")

    feeds_ctx = [(f, feed_info(f)) for f in GTFS_FEEDS]
    for f, ctx in feeds_ctx:
        info = ctx[2]
        f["feed_version"] = info.get("feed_version")
        f["feed_start"], f["feed_end"] = info.get("feed_start_date"), info.get("feed_end_date")
        log(f"   GTFS {f['id']} : version {f['feed_version']}, validité {f['feed_start']} → {f['feed_end']}")

    # ---- Choix du mardi type
    holidays = load_holidays()
    if args.date:
        date = dt.date.fromisoformat(args.date)
        log(f"3. Date imposée : {date}")
    else:
        log("3. Choix du mardi type (3 prochaines semaines, hors vacances scolaires A/B/C)")
        cands = []
        d = today + dt.timedelta(days=1)
        while d <= today + dt.timedelta(days=21):
            if d.weekday() == 1:
                hol = in_holidays(d, holidays)
                n = count_active_trips(feeds_ctx, d)
                log(f"   {d} : {n} circulations au plan de transport" + (f" – EXCLU : vacances {hol}" if hol else ""))
                if not hol and n > 0:
                    cands.append((n, d))
            d += dt.timedelta(days=1)
        if not cands:
            raise SystemExit("Aucun mardi exploitable dans les 3 prochaines semaines")
        # le mardi avec le plus de circulations (le moins touché par des travaux)
        date = max(cands)[1]
        log(f"   → mardi retenu : {date}")

    # ---- Trips
    log("4. Chargement des horaires du jour")
    trips, stop_names, stop_coords, mode_counter = load_trips_for_date(feeds_ctx, date)
    trips = dedupe_trips(trips)
    log(f"   {len(trips)} trains ferroviaires retenus ; modes du jour : {dict(mode_counter)}")
    missing_paris = [u for u in UIC_TO_PARIS if not any(s[1] == u for t in trips for s in t.stops[:1])]
    if missing_paris:
        log(f"   ! aucune origine de train à : {missing_paris}")

    # ---- Calcul
    log("5. Calcul des trajets (direct + 1 correspondance ≥ 5 min)")
    results, n_first = compute(trips, ANALYSIS_MAX_MIN * 60, MIN_TRANSFER_MIN * 60)
    log(f"   départs depuis Paris explorés : {dict(n_first)} ; {len(results)} gares atteintes en ≤ {ANALYSIS_MAX_MIN} min")

    # ---- Rapprochement des gares GTFS -> référentiel SNCF
    by_norm = defaultdict(list)
    for uic, g in gares.items():
        by_norm[norm(g["nom"])].append(uic)

    match_stats = Counter()

    def station_info(uic):
        g = gares.get(uic)
        if g:
            match_stats["uic"] += 1
            return g, "uic"
        lat, lon = stop_coords.get(uic, (None, None))
        name = stop_names.get(uic, uic)
        if lat is not None:
            best = None
            n = norm(name)
            for u, gg in gares.items():
                nn = norm(gg["nom"])
                if nn == n or nn.startswith(n) or n.startswith(nn):
                    dist = haversine_m(lat, lon, gg["lat"], gg["lon"])
                    if dist < 300 and (best is None or dist < best[0]):
                        best = (dist, u)
            if best:
                match_stats["nom+distance"] += 1
                return gares[best[1]], "nom+distance"
        match_stats["gtfs"] += 1
        return {"nom": name, "commune": None, "departement": None, "lat": lat, "lon": lon,
                "insee": None, "source": "gtfs"}, "gtfs"

    def trip_label(ti):
        t = trips[ti]
        return MODE_LABELS.get(t.mode, t.mode)

    def gare_name(uic):
        if uic in gares:
            return gares[uic]["nom"]
        return stop_names.get(uic, uic)

    # ---- Construction des enregistrements
    log("6. Filtre hors Île-de-France (point dans polygone) et export")
    records = []
    excluded_idf = []
    dept_disagree = []
    for dest, journeys in results.items():
        info, how = station_info(dest)
        if info["lat"] is None:
            continue
        in_idf = idf.contains(info["lon"], info["lat"])
        dept_code = info.get("dept_code") or (info.get("insee") or "")[:2]
        if dept_code and (dept_code in IDF_DEPTS) != in_idf:
            dept_disagree.append((gare_name(dest), dept_code, in_idf))
        if in_idf:
            excluded_idf.append(gare_name(dest))
            continue
        js = sorted(journeys.values(), key=lambda j: (j["dur"], j["transfer"] is not None, -j["dep"]))
        best = js[0]
        direct_js = [j for j in js if j["transfer"] is None]

        def describe(j):
            legs = [{
                "train": f"{trip_label(l['trip'])} {trips[l['trip']].number}".strip(),
                "type": trip_label(l["trip"]),
                "de": gare_name(l["from"]), "a": gare_name(l["to"]),
                "dep": fmt_hm(l["dep"]), "arr": fmt_hm(l["arr"]),
            } for l in j["legs"]]
            types = []
            for l in legs:
                if l["type"] not in types:
                    types.append(l["type"])
            return {
                "p": j["paris"], "d": round(j["dur"] / 60), "m": j["dep"] // 60, "dep": fmt_hm(j["dep"]),
                "arr": fmt_hm(j["arr"]), "t": " + ".join(types),
                "v": gare_name(j["transfer"]) if j["transfer"] else None,
                "w": round(j["wait"] / 60) if j["transfer"] else None,
                "n": " / ".join(l["train"] for l in legs),
            }

        # Trajets "utiles" (non dominés), par gare parisienne, tous trajets et directs seuls.
        # Le site recalcule à partir de cette liste selon les filtres choisis.
        ok90 = [j for j in js if j["dur"] <= MAX_MIN * 60]
        useful = {}
        for p in PARIS_STATIONS:
            jp = [j for j in ok90 if j["paris"] == p]
            for j in pareto(jp) + pareto([j for j in jp if j["transfer"] is None]):
                useful[id(j)] = j
        all_j = [describe(j) for j in sorted(useful.values(), key=lambda j: (j["dep"], j["dur"]))]
        n_all = len(pareto(ok90))
        n_direct = len(pareto([j for j in ok90 if j["transfer"] is None]))
        bd = describe(best)
        rec = {
            "uic": dest,
            "nom": info["nom"],
            "commune": info.get("commune") or "",
            "departement": info.get("departement") or "",
            "code_insee": info.get("insee") or "",
            "lat": round(info["lat"], 6), "lon": round(info["lon"], 6),
            "rapprochement": how,
            "duree_min": bd["d"],
            "gare_paris": PARIS_STATIONS[best["paris"]]["label"],
            "gare_paris_code": best["paris"],
            "depart": bd["dep"], "arrivee": bd["arr"],
            "type_train": bd["t"],
            "direct": best["transfer"] is None,
            "correspondance": bd["v"],
            "attente_correspondance_min": bd["w"],
            "trains": bd["n"],
            "trains_par_jour": n_all,
            "trains_directs_par_jour": n_direct,
            "duree_direct_min": round(direct_js[0]["dur"] / 60) if direct_js else None,
            "trajets": all_j,
        }
        records.append(rec)

    records.sort(key=lambda r: (r["duree_min"], r["nom"]))
    # duree en minutes entières arrondies ; le filtre se fait sur les secondes (dur <= 5400 s)
    kept = [r for r in records if r["trajets"]]
    borderline = [r for r in records if 85 <= r["duree_min"] <= 95]
    for r in kept:
        r["accessible_train"] = True
        r["voiture_min"] = r["voiture_km"] = None
        r["accessible_voiture"] = False

    # ---- Voiture
    car_only, car_all, iso_out, car_meta, car_issues = [], [], None, None, []
    if not args.no_car:
        log("7. Voiture sans trafic : isochrones et itinéraires (IGN Géoplateforme)")
        try:
            CAR_DIR.mkdir(parents=True, exist_ok=True)
            isos = {m: car_isochrone(m, args.refresh_car) for m in sorted(set(ISO_STEPS + [ISO_CANDIDATES_MIN]))}
            iso_regions = {m: Region(isos[m]["geometry"]) for m in isos}
            router = CarRouter(args.refresh_car)

            # cibles : gares accessibles en train + gares de voyageurs dans l'isochrone 100 min, hors IDF
            rec_by_uic = {r["uic"]: r for r in kept}
            targets = {r["uic"]: r for r in kept}
            for codes in load_station_groups():
                if any(c in rec_by_uic for c in codes):
                    continue
                key = next((c for c in codes if c in gares), None)
                if key is None or key in targets:
                    continue
                g = gares[key]
                if idf.contains(g["lon"], g["lat"]) or not iso_regions[ISO_CANDIDATES_MIN].contains(g["lon"], g["lat"]):
                    continue
                targets[key] = {
                    "uic": key, "nom": g["nom"], "commune": g.get("commune") or "",
                    "departement": g.get("departement") or "", "code_insee": g.get("insee") or "",
                    "lat": round(g["lat"], 6), "lon": round(g["lon"], 6), "rapprochement": "uic",
                    "duree_min": None, "gare_paris": None, "gare_paris_code": None, "depart": None,
                    "arrivee": None, "type_train": None, "direct": None, "correspondance": None,
                    "attente_correspondance_min": None, "trains": None, "trains_par_jour": 0,
                    "trains_directs_par_jour": 0, "duree_direct_min": None, "trajets": [],
                    "accessible_train": False, "voiture_min": None, "voiture_km": None,
                    "accessible_voiture": False,
                }
            n_cached = len(router.cache)
            log(f"   {len(isos)} isochrones ; {len(targets)} gares à évaluer "
                f"({len(kept)} accessibles en train + {len(targets) - len(kept)} dans l'isochrone {ISO_CANDIDATES_MIN} min)")
            for uic, r in targets.items():
                res = router.route(r["lon"], r["lat"], r["nom"])
                if res is None:
                    continue
                r["voiture_min"] = round(res["min"])
                r["voiture_km"] = round(res["km"], 1)
                r["accessible_voiture"] = r["voiture_min"] <= CAR_MAX_MIN
                # cohérence itinéraire / isochrone 90 min (généralisation des polygones)
                in_iso = iso_regions[CAR_MAX_MIN].contains(r["lon"], r["lat"])
                if in_iso != r["accessible_voiture"]:
                    car_issues.append((r["nom"], r["voiture_min"], in_iso))
            router.save()
            log(f"   itinéraires : {router.requests} nouvelles requêtes, {n_cached} en cache"
                + (f" ; échecs : {router.failures}" if router.failures else ""))
            car_all = [r for r in targets.values() if r["voiture_min"] is not None]
            car_only = sorted((r for r in car_all if r["accessible_voiture"] and not r["accessible_train"]),
                              key=lambda r: (r["voiture_min"], r["nom"]))

            iso_out = {
                "type": "FeatureCollection",
                "metadata": {"origine": CAR_ORIGIN, "moteur": f"IGN Géoplateforme – {CAR_RESOURCE}",
                             "version": isos[CAR_MAX_MIN].get("resourceVersion"), "profil": "voiture, sans trafic"},
                "features": [{"type": "Feature", "properties": {"minutes": m},
                              "geometry": simplify_geometry(isos[m]["geometry"])} for m in ISO_STEPS],
            }
            car_meta = {"origine": CAR_ORIGIN, "moteur": CAR_RESOURCE, "seuil_min": CAR_MAX_MIN,
                        "version": isos[CAR_MAX_MIN].get("resourceVersion"),
                        "isochrones": "data/isochrones_voiture.geojson"}
            SRC_ROUTAGE["extracted"] = extraction_date(CAR_DIR / "routes.json")
            SRC_ROUTAGE["version"] = car_meta["version"]
        except Exception as e:
            log(f"   ! volet voiture indisponible ({e}) – export sans données voiture")

    all_recs = sorted(kept + car_only, key=lambda r: (min(x for x in (r["duree_min"], r["voiture_min"]) if x is not None), r["nom"]))

    # ---- Prix de l'immobilier (communes autour des gares retenues)
    immo_meta, immo_sources, immo_ok = None, [], True
    if not args.no_immo:
        try:
            import prix_immo
            by_uic, names_by_uic, immo_meta, immo_sources, immo_ok = prix_immo.build(
                [{"uic": r["uic"], "lon": r["lon"], "lat": r["lat"], "code_insee": r["code_insee"], "nom": r["nom"]}
                 for r in all_recs],
                RAW, OUT, refresh=args.refresh_immo, extraction_date=extraction_date)
            for r in all_recs:
                r["prix_commune"] = by_uic.get(r["uic"])
                if names_by_uic.get(r["uic"]):
                    r["prix_communes_nom"] = names_by_uic[r["uic"]]
        except Exception as e:
            import traceback
            traceback.print_exc()
            log(f"   ! volet prix immobilier indisponible ({e}) – export sans prix")

    # ---- Exports
    meta = {
        "genere_le": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "date_reference": date.isoformat(),
        "seuil_min": MAX_MIN,
        "correspondance_min": MIN_TRANSFER_MIN,
        "sources": [
            {"titre": s["title"], "url": s.get("page") or s["url"], "telechargement": s["url"],
             "extrait_le": s.get("extracted"),
             **({"version": s.get("feed_version"), "validite": f"{s.get('feed_start')}–{s.get('feed_end')}"}
                if s in GTFS_FEEDS else {}),
             **({"version": s.get("version")} if s is SRC_ROUTAGE else {})}
            for s in all_sources + ([SRC_ROUTAGE] if car_meta else [])
        ] + immo_sources,
        "voiture": car_meta,
        "immo": immo_meta,
    }
    paris_feats = []
    for code, s in PARIS_STATIONS.items():
        g = gares.get(s["uics"][0])
        lat, lon = (g["lat"], g["lon"]) if g else stop_coords.get(s["uics"][0])
        paris_feats.append({"code": code, "nom": s["label"], "uic": s["uics"], "lat": round(lat, 6), "lon": round(lon, 6),
                            "gares_atteintes": sum(1 for r in kept if any(j["p"] == code for j in r["trajets"]))})

    features = []
    for r in all_recs:
        props = {k: v for k, v in r.items() if k not in ("lat", "lon")}
        features.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [r["lon"], r["lat"]]},
                         "properties": props})
    geo = {"type": "FeatureCollection", "metadata": meta, "gares_paris": paris_feats, "features": features}
    (OUT / "gares.geojson").write_text(json.dumps(geo, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    iso_path = OUT / "isochrones_voiture.geojson"
    if iso_out:
        iso_path.write_text(json.dumps(iso_out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    elif iso_path.exists() and args.no_car:
        iso_path.unlink()

    csv_cols = ["uic", "nom", "commune", "departement", "code_insee", "lat", "lon",
                "accessible_train", "duree_min", "gare_paris",
                "depart", "arrivee", "type_train", "direct", "correspondance", "attente_correspondance_min",
                "trains", "trains_par_jour", "trains_directs_par_jour", "duree_direct_min",
                "accessible_voiture", "voiture_min", "voiture_km",
                "commune_prix", "achat_tous_m2", "achat_appart_m2", "achat_maison_m2", "loyer_appart_m2", "loyer_maison_m2",
                "rapprochement"]

    def csv_row(r):
        yn = lambda v: "" if v is None else ("oui" if v else "non")
        pc = r.get("prix_commune") or {}
        prix = {"commune_prix": pc.get("nom"), **{k: pc.get(k) for k in
                ("achat_tous_m2", "achat_appart_m2", "achat_maison_m2", "loyer_appart_m2", "loyer_maison_m2")}}
        return {**{k: ("" if v is None else v) for k, v in {**r, **prix}.items()}, "direct": yn(r["direct"]),
                "accessible_train": yn(r.get("accessible_train")), "accessible_voiture": yn(r.get("accessible_voiture"))}

    with open(OUT / "gares.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=csv_cols, delimiter=";", extrasaction="ignore")
        w.writeheader()
        for r in all_recs:
            w.writerow(csv_row(r))
    with open(ROOT / "data" / "gares_limites.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=csv_cols, delimiter=";", extrasaction="ignore")
        w.writeheader()
        for r in borderline:
            w.writerow(csv_row({**r, "accessible_train": r["duree_min"] <= MAX_MIN}))

    # ---- Résumé et vérifications
    log("")
    log("=" * 78)
    log(f"RÉSUMÉ – mardi {date:%d/%m/%Y} – {len(kept)} gares hors IDF en ≤ {MAX_MIN} min")
    log("=" * 78)
    buckets = Counter()
    for r in kept:
        d = r["duree_min"]
        buckets["≤ 30 min" if d <= 30 else "31–60 min" if d <= 60 else "61–90 min"] += 1
    for b in ("≤ 30 min", "31–60 min", "61–90 min"):
        log(f"  {b:>10} : {buckets[b]}")
    log(f"  directs : {sum(r['direct'] for r in kept)} ; avec correspondance : {sum(not r['direct'] for r in kept)}")
    log("  par gare parisienne (trajet le plus rapide) : " +
        ", ".join(f"{k}={v}" for k, v in Counter(r['gare_paris'] for r in kept).most_common()))
    log(f"  rapprochement des gares : {dict(match_stats)}")
    log(f"  gares atteintes mais en IDF (exclues) : {len(excluded_idf)} : {', '.join(sorted(excluded_idf))}")
    log(f"  gares sans code INSEE (commune issue de la liste SNCF) : "
        f"{[r['nom'] for r in kept if not r['code_insee']]}")
    if dept_disagree:
        log(f"  ! désaccord département INSEE / polygone : {dept_disagree}")

    log("\nCONTRÔLES – gares attendues :")
    ok_all = True
    for pat, exp in EXPECTED_PRESENT:
        hits = [r for r in kept if pat in norm(r["nom"])]
        if not hits:
            ok_all = False
            log(f"  ✗ {pat!r} ABSENTE")
            continue
        for r in hits[:3]:
            flag = "✓" if exp is None or abs(r["duree_min"] - exp) <= 15 else "?"
            if flag != "✓":
                ok_all = False
            log(f"  {flag} {r['nom']:<32} {r['duree_min']:>3} min (attendu ~{exp or '-'})"
                f"  {r['gare_paris']} – {r['type_train']}{' via ' + r['correspondance'] if r['correspondance'] else ''}"
                f"  [{r['depart']}→{r['arrivee']}]")
    log("CONTRÔLES – gares qui doivent être absentes :")
    for pat in EXPECTED_ABSENT:
        hits = [r for r in kept if pat in norm(r["nom"])]
        if hits:
            ok_all = False
            for r in hits:
                log(f"  ✗ {r['nom']} présente ({r['duree_min']} min)")
        else:
            other = [r for r in records if pat in norm(r["nom"])]
            extra = f" (vue à {other[0]['duree_min']} min, hors seuil)" if other else ""
            log(f"  ✓ {pat!r} absente{extra}")
    in_idf_kept = [r["nom"] for r in kept if idf.contains(r["lon"], r["lat"])]
    log(f"  {'✓' if not in_idf_kept else '✗'} aucune gare exportée dans le polygone IDF {in_idf_kept or ''}")

    log(f"\nGARES LIMITES (85–95 min) – {len(borderline)} gares (data/gares_limites.csv) :")
    for r in borderline:
        log(f"  {r['duree_min']:>3} min  {r['nom']:<34} {r['gare_paris']:<20} {r['type_train']:<22}"
            f" {'direct' if r['direct'] else 'via ' + (r['correspondance'] or '')}  [{r['depart']}→{r['arrivee']}]"
            f"{'  (exclue)' if r['duree_min'] > MAX_MIN else ''}")
    if car_meta:
        car_ok = [r for r in all_recs if r["accessible_voiture"]]
        log("")
        log("=" * 78)
        log(f"VOITURE (sans trafic, depuis {CAR_ORIGIN['nom']}) – {len(car_ok)} gares hors IDF en ≤ {CAR_MAX_MIN} min")
        log("=" * 78)
        cb = Counter("≤ 30 min" if r["voiture_min"] <= 30 else "31–60 min" if r["voiture_min"] <= 60
                     else "61–90 min" if r["voiture_min"] <= 90 else f"91–{CAR_MAX_MIN} min" for r in car_ok)
        for b in ("≤ 30 min", "31–60 min", "61–90 min", f"91–{CAR_MAX_MIN} min"):
            log(f"  {b:>10} : {cb[b]}")
        both = sum(1 for r in all_recs if r["accessible_train"] and r["accessible_voiture"])
        log(f"  train ET voiture : {both} ; train seul : {len(kept) - both} ; voiture seule : {len(car_only)}")
        speeds = sorted((r["voiture_km"] / r["voiture_min"] * 60, r["nom"]) for r in car_all if r["voiture_min"])
        log(f"  vitesse moyenne porte-à-porte : de {speeds[0][0]:.0f} km/h ({speeds[0][1]}) "
            f"à {speeds[-1][0]:.0f} km/h ({speeds[-1][1]})")
        if car_issues:
            log(f"  écarts itinéraire / isochrone {CAR_MAX_MIN} min (bord du polygone) : {len(car_issues)} : "
                + ", ".join(f"{n} {m} min" for n, m, _ in car_issues))
        log("CONTRÔLES VOITURE :")
        for pat, expected in CAR_EXPECTED:
            hits = [r for r in car_all if pat in norm(r["nom"])]
            if not hits:
                log(f"  {'✗' if expected else '✓'} {pat!r} : pas d'itinéraire calculé (hors isochrone {ISO_CANDIDATES_MIN} min)")
                ok_all &= not expected
                continue
            r = min(hits, key=lambda r: r["voiture_min"])
            good = r["accessible_voiture"] == expected
            ok_all &= good
            log(f"  {'✓' if good else '✗'} {r['nom']:<32} {r['voiture_min']:>3} min, {r['voiture_km']:.0f} km "
                f"(attendu : {'≤' if expected else '>'} {CAR_MAX_MIN} min)")
        car_border = sorted((r for r in car_all if CAR_MAX_MIN - 5 <= r["voiture_min"] <= CAR_MAX_MIN + 5), key=lambda r: r["voiture_min"])
        log(f"GARES LIMITES VOITURE ({CAR_MAX_MIN - 5}–{CAR_MAX_MIN + 5} min) – {len(car_border)} :")
        for r in car_border:
            log(f"  {r['voiture_min']:>3} min  {r['voiture_km']:>5.0f} km  {r['nom']}"
                f"{'  (exclue)' if not r['accessible_voiture'] else ''}")

    log(f"\nExports : {OUT / 'gares.geojson'} ({(OUT / 'gares.geojson').stat().st_size // 1024} Ko), {OUT / 'gares.csv'}")
    ok_all &= immo_ok
    if not ok_all:
        log("\n! Certains contrôles ne sont pas conformes – voir ci-dessus.")


if __name__ == "__main__":
    main()
