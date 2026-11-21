"""
Couche « prix de l'immobilier » par commune (open data uniquement), appelée par build_data.py.

- Achat : DVF géolocalisées (Etalab / DGFiP), recalcul des médianes au m² :
    ventes uniquement, un seul local (maison ou appartement) par mutation, prix/m² =
    valeur_fonciere / surface_reelle_bati, valeurs hors 5e–95e percentiles retirées par
    commune et par type, médiane. Types : appartements, maisons, tous biens.
    Période principale : les 2 dernières années complètes ; si < 10 ventes, période élargie à
    toutes les années complètes du millésime (ex. 2021–2025).
- Location : « Carte des loyers » (ANIL / ministère du Logement), dernière année, avec une
  fiabilité en 3 niveaux dérivée du guide ANIL.
- Contours : Etalab (d'après IGN ADMIN EXPRESS), communes simplifiées 100 m.
- Périmètre : communes à moins de 15 km d'une gare retenue + commune de chaque gare
  + communes citées dans le nom de la gare.
- Contrôle bloquant : rapport médian avec les statistiques DVF d'Etalab dans [0,9 ; 1,1].
"""
from __future__ import annotations

import csv
import gzip
import io
import json
import math
import re
import shutil
import statistics
import unicodedata
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

UA = "gares-paris-1h30/1.0"

CONTOURS = {
    "title": "Contours des communes 2025, simplifiés 100 m (Etalab, d'après IGN ADMIN EXPRESS)",
    "page": "https://www.data.gouv.fr/fr/datasets/contours-administratifs/",
    "url": "https://etalab-datasets.geo.data.gouv.fr/contours-administratifs/2025/geojson/communes-100m.geojson.gz",
    "file": "immo/communes-100m-2025.geojson.gz",
}
GEO_DVF_ROOT = "https://files.data.gouv.fr/geo-dvf/"
DVF = {
    "title": "Demandes de valeurs foncières géolocalisées (DVF, Etalab / DGFiP)",
    "page": "https://www.data.gouv.fr/fr/datasets/demandes-de-valeurs-foncieres-geolocalisees/",
    "url": GEO_DVF_ROOT + "latest/csv/",
    "file": "immo/dvf",
}
DVF_STATS = {
    "title": "Statistiques DVF (Etalab) – contrôle croisé",
    "page": "https://www.data.gouv.fr/fr/datasets/statistiques-dvf/",
    "url": "https://data-pipeline-open.s3.sbg.io.cloud.ovh.net/dvf/stats_whole_period.csv",
    "file": "immo/stats_dvf_whole_period.csv",
}
LOYERS_YEAR = 2025
LOYERS = {
    "title": f"Carte des loyers – indicateurs de loyers d'annonce par commune en {LOYERS_YEAR} (ANIL / ministère du Logement)",
    "page": "https://www.data.gouv.fr/fr/datasets/carte-des-loyers-indicateurs-de-loyers-dannonce-par-commune-en-2025/",
    "credit": "Estimations ANIL, à partir des données du Groupe SeLoger et de leboncoin",
    "files": {
        "appart": ("https://static.data.gouv.fr/resources/carte-des-loyers-indicateurs-de-loyers-dannonce-par-commune-en-2025/20251211-145010/pred-app-mef-dhup.csv",
                   "immo/loyers-2025-appartements.csv"),
        "maison": ("https://static.data.gouv.fr/resources/carte-des-loyers-indicateurs-de-loyers-dannonce-par-commune-en-2025/20251211-145039/pred-mai-mef-dhup.csv",
                   "immo/loyers-2025-maisons.csv"),
    },
}

RADIUS_KM = 15
NAME_COMMUNE_KM = 12                # communes citées dans le nom d'une gare : à moins de 12 km
MIN_SALES = 10                      # en dessous : période élargie, puis « données insuffisantes »
RECENT_YEARS = 2                    # période principale : 2 dernières années complètes
TRIM = (0.05, 0.95)                 # percentiles conservés par commune et par type
TYPES = ("appart", "maison", "tous")
DVF_NOT_COVERED = {"57", "67", "68", "976"}   # Moselle, Bas-Rhin, Haut-Rhin, Mayotte
ETALAB_RATIO_OK = (0.9, 1.1)        # contrôle croisé bloquant
ETALAB_MIN_SALES = 50
# Fiabilité loyer (guide + note méthodologique ANIL 2025, voir README) :
#   peu fiable  : R² ajusté < 0,5 ou intervalle de prédiction « très large » (au-delà du 95e
#                 percentile national de la largeur relative, par type de bien) ;
#   précaution  : < 30 annonces dans la commune -> l'indicateur est celui de la maille
#                 (groupe de communes voisines comparables, >= 500 annonces) ;
#   fiable      : sinon.
LOYER_MIN_OBS = 30
LOYER_MIN_R2 = 0.5
LOYER_IP_PCTL = 0.95
COORD_DECIMALS = 3                  # ~ 75 m x 110 m : cohérent avec la simplification 100 m
SIMPLIFY_TOL = 0.0014               # tolérance Douglas-Peucker topologique (~ 100–155 m)


def log(*a):
    print(*a, flush=True)


def _download(url, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(req, timeout=600) as r, open(tmp, "wb") as f:
        shutil.copyfileobj(r, f)
    tmp.replace(dest)


def _fetch(raw: Path, url, rel, refresh):
    p = raw / rel
    if refresh or not p.exists():
        log(f"  ↓ {url}")
        _download(url, p)
    return p


def _get_text(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8", "replace")


def _etag(url):
    """ETag d'un fichier sans le télécharger (requête GET limitée au premier octet)."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Range": "bytes=0-0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.headers.get("ETag")
    except Exception:
        return None


def _dep_of(code: str) -> str:
    return code[:3] if code.startswith("97") else code[:2]


def _commune_parent(code: str) -> str:
    """Arrondissements municipaux -> commune (Paris, Lyon, Marseille)."""
    if code.startswith("751") and len(code) == 5 and code != "75100":
        return "75056"
    if code.startswith("6938"):
        return "69123"
    if code.startswith("132") and 13201 <= int(code) <= 13216:
        return "13055"
    return code


def _tokens(s: str):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    toks = re.findall(r"[a-z0-9]+", s)
    return ["saint" if t == "st" else "sainte" if t == "ste" else t for t in toks]


def _contains_seq(hay, needle):
    n = len(needle)
    return n > 0 and any(hay[i:i + n] == needle for i in range(len(hay) - n + 1))


# ----------------------------------------------------------------------------- géométrie
def _rings(geom):
    polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
    return polys


def _in_ring(x, y, ring):
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _contains(polys, x, y):
    return any(_in_ring(x, y, p[0]) and not any(_in_ring(x, y, h) for h in p[1:]) for p in polys)


def _round_polys(geom):
    """Arrondi des coordonnées (les sommets partagés restent partagés) + suppression des doublons."""
    out = []
    for poly in _rings(geom):
        rings = []
        for ring in poly:
            r = []
            for x, y in ring:
                pt = (round(x, COORD_DECIMALS), round(y, COORD_DECIMALS))
                if not r or r[-1] != pt:
                    r.append(pt)
            if len(r) >= 4 and r[0] == r[-1]:
                rings.append(r)
        if rings:
            out.append(rings)
    return out


def _dp(pts, tol):
    n = len(pts)
    if n <= 2:
        return list(pts)
    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    t2 = tol * tol
    while stack:
        a, b = stack.pop()
        ax, ay = pts[a]
        dx, dy = pts[b][0] - ax, pts[b][1] - ay
        l2 = dx * dx + dy * dy
        imax, dmax = -1, 0.0
        for i in range(a + 1, b):
            px, py = pts[i][0] - ax, pts[i][1] - ay
            t = 0.0 if l2 == 0 else max(0.0, min(1.0, (px * dx + py * dy) / l2))
            d = (px - t * dx) ** 2 + (py - t * dy) ** 2
            if d > dmax:
                imax, dmax = i, d
        if dmax > t2:
            keep[imax] = True
            stack += [(a, imax), (imax, b)]
    return [p for p, k in zip(pts, keep) if k]


def topo_simplify(geoms: dict, tol: float):
    """
    Simplification Douglas-Peucker *topologique* : les anneaux sont découpés en arcs aux
    jonctions (sommets dont l'ensemble des communes propriétaires change), chaque arc partagé
    est simplifié une seule fois et réutilisé (à l'envers) par la commune voisine.
    -> pas de trous ni de chevauchements entre communes. geoms : code -> polys (anneaux fermés).
    """
    owners = defaultdict(set)
    for code, polys in geoms.items():
        for poly in polys:
            for ring in poly:
                for pt in ring[:-1]:
                    owners[pt].add(code)
    cache = {}

    def arc(seq):
        key = tuple(seq)
        if key in cache:
            return cache[key]
        rk = key[::-1]
        if rk in cache:
            return cache[rk][::-1]
        res = _dp(seq, tol)
        cache[key] = res
        return res

    out = {}
    for code, polys in geoms.items():
        new_polys = []
        for poly in polys:
            new_rings = []
            for ri, ring in enumerate(poly):
                pts = ring[:-1]
                n = len(pts)
                junc = [i for i in range(n)
                        if owners[pts[i]] != owners[pts[i - 1]] or owners[pts[i]] != owners[pts[(i + 1) % n]]]
                if not junc:
                    # anneau sans jonction (île, enclave) : départ canonique au plus petit sommet
                    s = min(range(n), key=lambda i: pts[i])
                    rot = pts[s:] + pts[:s]
                    simp = arc(rot + [rot[0]])
                else:
                    s = junc[0]
                    rot = pts[s:] + pts[:s]
                    cuts = [(j - s) % n for j in junc] + [n]
                    rot = rot + [rot[0]]
                    simp = []
                    for a, b in zip(cuts, cuts[1:]):
                        part = arc(rot[a:b + 1])
                        simp.extend(part if not simp else part[1:])
                if len(simp) < 4:
                    if ri > 0:
                        continue        # petit trou disparu
                    simp = ring         # petite commune : on garde l'anneau arrondi
                new_rings.append([list(p) for p in simp])
            if new_rings:
                new_polys.append(new_rings)
        if new_polys:
            out[code] = ({"type": "Polygon", "coordinates": new_polys[0]} if len(new_polys) == 1
                         else {"type": "MultiPolygon", "coordinates": new_polys})
    return out


# ----------------------------------------------------------------------------- DVF
def _dvf_millesime(years, sample_dep):
    """Identifie le millésime daté (ex. « 2025-12 ») correspondant à geo-dvf/latest (même ETag)."""
    try:
        dated = sorted(set(re.findall(r'href="/geo-dvf/(\d{4}-\d{2})/"', _get_text(GEO_DVF_ROOT))), reverse=True)
    except Exception:
        return None
    probe = f"csv/{max(years)}/departements/{sample_dep}.csv.gz"
    latest = _etag(f"{GEO_DVF_ROOT}latest/{probe}")
    for d in dated:
        if latest and _etag(f"{GEO_DVF_ROOT}{d}/{probe}") == latest:
            return d
    return None


def _dvf_file(raw: Path, year: int, dep: str, refresh: bool):
    rel = f"{DVF['file']}/{year}/{dep}.csv.gz"
    p = raw / rel
    if p.exists() and not refresh:
        return p
    url = f"{DVF['url']}{year}/departements/{dep}.csv.gz"
    try:
        log(f"  ↓ {url}")
        _download(url, p)
        return p
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def _dvf_prices(raw: Path, years, deps, valid_codes, refresh):
    """
    Retourne prices[code][année][type] -> liste de prix/m², months[année][mois] -> nb de
    mutations « Vente » (toutes, pour contrôler la complétude), et des stats de filtrage.
    """
    prices = defaultdict(lambda: defaultdict(lambda: {"appart": [], "maison": []}))
    months = defaultdict(Counter)
    stats = Counter()
    for y in years:
        for dep in sorted(deps):
            if dep in DVF_NOT_COVERED:
                continue
            p = _dvf_file(raw, y, dep, refresh)
            if p is None:
                stats["fichiers_absents"] += 1
                continue
            muts = {}
            with gzip.open(p, "rt", encoding="utf-8", newline="") as f:
                rd = csv.reader(f)
                h = {k: i for i, k in enumerate(next(rd))}
                i_id, i_date, i_nat, i_val = h["id_mutation"], h["date_mutation"], h["nature_mutation"], h["valeur_fonciere"]
                i_type, i_surf, i_code, i_old = h["type_local"], h["surface_reelle_bati"], h["code_commune"], h["ancien_code_commune"]
                for row in rd:
                    if row[i_nat] != "Vente":
                        continue
                    m = muts.get(row[i_id])
                    if m is None:
                        m = muts[row[i_id]] = {"val": row[i_val], "month": row[i_date][5:7], "locaux": [], "autre": False}
                    t = row[i_type]
                    if t in ("Maison", "Appartement"):
                        m["locaux"].append((t, row[i_surf], row[i_code], row[i_old]))
                    elif t and t != "Dépendance":
                        m["autre"] = True     # local commercial / industriel dans la mutation
            for m in muts.values():
                months[y][m["month"]] += 1
                stats["mutations_vente"] += 1
                if len(m["locaux"]) != 1 or m["autre"]:
                    stats["exclues_multi_locaux"] += 1
                    continue
                t, surf, code, old = m["locaux"][0]
                try:
                    val, surf = float(m["val"]), float(surf)
                except ValueError:
                    stats["exclues_valeur_manquante"] += 1
                    continue
                if surf < 9 or val < 1000:
                    stats["exclues_surface_ou_prix_invalide"] += 1
                    continue
                code = _commune_parent(code)
                if code not in valid_codes:
                    alt = _commune_parent(old) if old else None
                    if alt in valid_codes:
                        code = alt
                        stats["jointure_ancien_code"] += 1
                    else:
                        stats["hors_perimetre"] += 1
                        continue
                prices[code][y]["appart" if t == "Appartement" else "maison"].append(val / surf)
                stats["ventes_retenues"] += 1
    return prices, months, stats


def _trimmed_median(values):
    if not values:
        return None
    v = sorted(values)
    n = len(v)
    lo, hi = v[int(math.floor(TRIM[0] * (n - 1)))], v[int(math.ceil(TRIM[1] * (n - 1)))]
    kept = [x for x in v if lo <= x <= hi]
    return statistics.median(kept)


def _values(by_year, years, t):
    out = []
    for y in years:
        d = by_year.get(y)
        if d:
            out += d["appart"] + d["maison"] if t == "tous" else d[t]
    return out


# ----------------------------------------------------------------------------- loyers
def _load_loyers(raw: Path, refresh):
    out = {}
    thresholds = {}
    for kind, (url, rel) in LOYERS["files"].items():
        p = _fetch(raw, url, rel, refresh)
        rows = list(csv.DictReader(io.StringIO(p.read_bytes().decode("cp1252")), delimiter=";"))
        f = lambda r, k: float(r[k].replace(",", ".")) if r.get(k) not in (None, "", "NA") else None
        rels = sorted((f(r, "upr.IPm2") - f(r, "lwr.IPm2")) / f(r, "loypredm2") for r in rows
                      if f(r, "loypredm2") and f(r, "lwr.IPm2") is not None and f(r, "upr.IPm2") is not None)
        thr = rels[int(LOYER_IP_PCTL * (len(rels) - 1))]
        thresholds[kind] = round(thr, 3)
        for r in rows:
            pred, lwr, upr = f(r, "loypredm2"), f(r, "lwr.IPm2"), f(r, "upr.IPm2")
            if pred is None:
                continue
            nobs = int(float(r["nbobs_com"] or 0))
            nmail = int(float(r["nbobs_mail"] or 0))
            r2 = f(r, "R2_adj")
            wide = lwr is not None and upr is not None and (upr - lwr) / pred > thr
            if (r2 is not None and r2 < LOYER_MIN_R2) or wide:
                level = "peu_fiable"
            elif nobs < LOYER_MIN_OBS:
                level = "precaution"
            else:
                level = "fiable"
            out.setdefault(r["INSEE_C"], {})[kind] = {
                "m2": round(pred, 2), "min": round(lwr, 2) if lwr else None, "max": round(upr, 2) if upr else None,
                "nobs": nobs, "nmail": nmail, "r2": round(r2, 2) if r2 is not None else None,
                "niveau": (r.get("TYPPRED") or "").lower(), "level": level, "wide": wide,
            }
    return out, thresholds


# ----------------------------------------------------------------------------- principal
def build(stations, raw: Path, out_dir: Path, refresh=False, extraction_date=None):
    """
    stations : liste de dicts {uic, lon, lat, code_insee, nom}.
    Écrit out_dir/communes_prix.geojson ; renvoie (prix_par_uic, communes_nom_par_uic, meta,
    sources, contrôles_ok). Lève SystemExit si le contrôle croisé Etalab échoue.
    """
    log("8. Prix de l'immobilier par commune (DVF + carte des loyers)")
    p = _fetch(raw, CONTOURS["url"], CONTOURS["file"], refresh)
    with gzip.open(p, "rt", encoding="utf-8") as f:
        fc = json.load(f)
    communes = []
    for feat in fc["features"]:
        pr = feat["properties"]
        polys = _rings(feat["geometry"])
        xs = [pt[0] for poly in polys for pt in poly[0]]
        ys = [pt[1] for poly in polys for pt in poly[0]]
        communes.append({"code": pr["code"], "nom": pr["nom"], "polys": polys,
                         "bbox": (min(xs), min(ys), max(xs), max(ys)), "geom": feat["geometry"]})
    log(f"   {len(communes)} contours de communes chargés")

    # --- périmètre : communes à <= 15 km d'une gare (sommet le plus proche) + commune de la gare
    cell = 0.2
    grid = defaultdict(list)
    for s in stations:
        grid[(int(math.floor(s["lat"] / cell)), int(math.floor(s["lon"] / cell)))].append(s)
    dlat = RADIUS_KM / 110.57
    selected = {}
    station_commune = {}
    for c in communes:
        x0, y0, x1, y1 = c["bbox"]
        dlon = RADIUS_KM / (111.32 * math.cos(math.radians((y0 + y1) / 2)))
        near = []
        for gy in range(int(math.floor((y0 - dlat) / cell)), int(math.floor((y1 + dlat) / cell)) + 1):
            for gx in range(int(math.floor((x0 - dlon) / cell)), int(math.floor((x1 + dlon) / cell)) + 1):
                for s in grid.get((gy, gx), ()):
                    if x0 - dlon <= s["lon"] <= x1 + dlon and y0 - dlat <= s["lat"] <= y1 + dlat:
                        near.append(s)
        if not near:
            continue
        for s in near:
            if x0 <= s["lon"] <= x1 and y0 <= s["lat"] <= y1 and _contains(c["polys"], s["lon"], s["lat"]):
                station_commune[s["uic"]] = c["code"]
                selected[c["code"]] = c
        if c["code"] in selected:
            continue
        coslat = math.cos(math.radians((y0 + y1) / 2))
        r2 = RADIUS_KM ** 2
        found = False
        for s in near:
            for poly in c["polys"]:
                for x, y in poly[0]:
                    if ((x - s["lon"]) * 111.32 * coslat) ** 2 + ((y - s["lat"]) * 110.57) ** 2 <= r2:
                        found = True
                        break
                if found:
                    break
            if found:
                break
        if found:
            selected[c["code"]] = c
    # Commune de la gare : code INSEE officiel SNCF s'il existe dans le COG des contours,
    # sinon commune dont le polygone contient la gare (gares près d'une limite communale).
    by_code = {c["code"]: c for c in communes}
    pip_differs = []
    for s in stations:
        code = s.get("code_insee")
        if code in by_code:
            if station_commune.get(s["uic"]) not in (None, code):
                pip_differs.append(f"{s['nom']} ({by_code[code]['nom']} / polygone : {by_code[station_commune[s['uic']]]['nom']})")
            station_commune[s["uic"]] = code
            selected[code] = by_code[code]

    # --- communes citées dans le nom de la gare (ex. « Compiègne », gare située à Margny-lès-Compiègne)
    #     nom complet de commune présent tel quel dans le nom de la gare, ou à défaut son premier
    #     mot (« Coye » pour Coye-la-Forêt) s'il est unique ; < 12 km ; hors commune de la gare.
    name_communes = {}
    for s in stations:
        own = station_commune.get(s["uic"])
        stoks = _tokens(s["nom"])
        own_toks = _tokens(by_code[own]["nom"]) if own in by_code else []
        # on retire du nom de la gare le nom de SA commune s'il y figure en entier
        # (« Margny-lès-Compiègne » dans Margny-lès-Compiègne ne cite pas Compiègne ;
        #  « Compiègne », gare située à Margny-lès-Compiègne, la cite)
        if _contains_seq(stoks, own_toks):
            i = next(i for i in range(len(stoks)) if stoks[i:i + len(own_toks)] == own_toks)
            stoks = stoks[:i] + ["|"] + stoks[i + len(own_toks):]
        coslat = math.cos(math.radians(s["lat"]))
        full, first = [], []
        for c in communes:
            if c["code"] == own:
                continue
            x0, y0, x1, y1 = c["bbox"]
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            if ((cx - s["lon"]) * 111.32 * coslat) ** 2 + ((cy - s["lat"]) * 110.57) ** 2 > NAME_COMMUNE_KM ** 2:
                continue
            ctoks = _tokens(c["nom"])
            if len("".join(ctoks)) < 3:
                continue
            if _contains_seq(stoks, ctoks):
                full.append(c)
            elif len(ctoks[0]) >= 4 and ctoks[0] in stoks:
                first.append(c)
        found = full or (first if len(first) == 1 else [])
        # ne garder que les correspondances les plus longues (évite « Villiers » si « Villiers-sur-Loir »)
        found = [c for c in found if not any(o is not c and _contains_seq(_tokens(o["nom"]), _tokens(c["nom"]))
                                             and len(_tokens(o["nom"])) > len(_tokens(c["nom"])) for o in found)]
        if found:
            name_communes[s["uic"]] = [c["code"] for c in found]
            for c in found:
                selected[c["code"]] = c
    deps = {_dep_of(code) for code in selected}
    log(f"   périmètre : {len(selected)} communes dans {len(deps)} départements "
        f"({', '.join(sorted(deps))}) ; gares rattachées à une commune : {len(station_commune)}/{len(stations)}")
    if pip_differs:
        log(f"   commune SNCF retenue ≠ polygone simplifié (gare en limite de commune) : {len(pip_differs)} : "
            + ", ".join(pip_differs))
    st_by_uic = {s["uic"]: s for s in stations}
    log(f"   communes citées dans le nom de la gare ({len(name_communes)} gares) : "
        + ", ".join(f"{st_by_uic[u]['nom']} → {by_code[station_commune[u]]['nom']} + "
                    f"{' / '.join(by_code[c]['nom'] for c in cs)}" for u, cs in name_communes.items()))

    # --- DVF : années du millésime, complétude
    sample = sorted(deps - DVF_NOT_COVERED)[0]
    years_avail = sorted({int(y) for y in re.findall(r"/csv/(\d{4})/", _get_text(DVF["url"]))})
    millesime = _dvf_millesime(years_avail, sample)
    log(f"   DVF : geo-dvf/latest = millésime {millesime or '?'} ; années publiées {years_avail}")
    prices, months, dvf_stats = _dvf_prices(raw, years_avail, deps, set(selected), refresh)
    complete = []
    for y in years_avail:
        m = months[y]
        vols = [m.get(f"{i:02d}", 0) for i in range(1, 13)]
        med = statistics.median(vols)
        ok_y = all(v > 0 for v in vols) and min(vols) >= 0.4 * med
        log(f"   DVF {y} : {sum(vols)} ventes, mois présents {sum(1 for v in vols if v)}/12, "
            f"min/médiane mensuelle {min(vols)}/{med:.0f}, décembre {vols[11]} → {'complète' if ok_y else 'INCOMPLÈTE'}")
        if ok_y:
            complete.append(y)
    recent = complete[-RECENT_YEARS:]
    if len(recent) < RECENT_YEARS or recent[-1] != years_avail[-1]:
        log(f"   ! dernière année publiée ({years_avail[-1]}) incomplète : période principale = {recent}")
    period = f"{recent[0]}-{recent[-1]}"
    period_ext = f"{complete[0]}-{complete[-1]}"
    log(f"   DVF : période principale {period}, période élargie {period_ext} ; {dict(dvf_stats)}")

    # --- loyers
    loyers, ip_thr = _load_loyers(raw, refresh)
    log(f"   loyers : seuil « intervalle très large » = largeur relative > {ip_thr} (95e pct national, par type)")

    # --- assemblage
    geoms = topo_simplify({code: _round_polys(c["geom"]) for code, c in selected.items()}, SIMPLIFY_TOL)
    feats = []
    props_by_code = {}
    recent_values = {}
    for code, c in selected.items():
        dep = _dep_of(code)
        covered = dep not in DVF_NOT_COVERED
        by_year = prices.get(code, {})
        vals, ns, pe = {}, [], []
        for t in TYPES:
            rv = _values(by_year, recent, t)
            recent_values[(code, t)] = (_trimmed_median(rv), len(rv))
            if len(rv) >= MIN_SALES:
                vals[t], n, e = round(_trimmed_median(rv)), len(rv), 0
            else:
                ev = _values(by_year, complete, t)
                vals[t] = round(_trimmed_median(ev)) if len(ev) >= MIN_SALES else None
                n, e = len(ev), 1
            ns.append(n)
            pe.append(e)
        la, lm = loyers.get(code, {}).get("appart"), loyers.get(code, {}).get("maison")
        # Champs demandés (noms explicites) + détails compacts pour le site :
        #   n  : nb de ventes [appart, maison, tous] sur la période utilisée pour chaque type ;
        #   pe : [0/1 x 3] période élargie utilisée (absent si aucune) ;
        #   ipa/ipm : [borne basse, borne haute, annonces dans la commune, R², niveau de prédiction] ;
        #   fiabilite_loyer : "fiable" | "precaution" | "peu_fiable" par type.
        props = {
            "code": code, "nom": c["nom"],
            "achat_appart_m2": vals["appart"] if covered else None,
            "achat_maison_m2": vals["maison"] if covered else None,
            "achat_tous_m2": vals["tous"] if covered else None,
            "nb_ventes": recent_values[(code, "tous")][1] if covered else None,
            "n": ns if covered else None,
            "pe": pe if covered and any(pe) else None,
            "loyer_appart_m2": la["m2"] if la else None,
            "loyer_maison_m2": lm["m2"] if lm else None,
            "ipa": [la["min"], la["max"], la["nobs"], la["r2"], la["niveau"]] if la else None,
            "ipm": [lm["min"], lm["max"], lm["nobs"], lm["r2"], lm["niveau"]] if lm else None,
            "fiabilite_loyer": {"appart": la["level"] if la else None, "maison": lm["level"] if lm else None},
            "annee_donnees": f"{period}/{LOYERS_YEAR}",
        }
        if not covered:
            props["dvf"] = 0
        props = {k: v for k, v in props.items() if v is not None}
        props_by_code[code] = props
        if code in geoms:
            feats.append({"type": "Feature", "properties": props, "geometry": geoms[code]})

    # --- contrôle croisé Etalab (BLOQUANT) : médianes Etalab sur tout le millésime (10 semestres),
    #     comparées à nos médianes sur la période principale, communes avec >= 50 ventes Etalab.
    log("CONTRÔLE CROISÉ ETALAB (bloquant si rapport médian hors "
        f"[{ETALAB_RATIO_OK[0]} ; {ETALAB_RATIO_OK[1]}]) :")
    sp = _fetch(raw, DVF_STATS["url"], DVF_STATS["file"], refresh)
    ratios = {t: [] for t in TYPES}
    with open(sp, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            if r["echelle_geo"] != "commune" or r["code_geo"] not in props_by_code:
                continue
            for t, et in (("appart", "appartement"), ("maison", "maison"), ("tous", "apt_maison")):
                nb, med = r[f"nb_ventes_whole_{et}"], r[f"med_prix_m2_whole_{et}"]
                ours, n_ours = recent_values.get((r["code_geo"], t), (None, 0))
                if ours and n_ours >= MIN_SALES and nb and med and int(float(nb)) >= ETALAB_MIN_SALES:
                    ratios[t].append(ours / float(med))
    failures = []
    for t, rs in ratios.items():
        if not rs:
            failures.append(f"{t} : aucune commune comparable")
            continue
        rs.sort()
        m = statistics.median(rs)
        good = ETALAB_RATIO_OK[0] <= m <= ETALAB_RATIO_OK[1]
        log(f"  {'✓' if good else '✗'} {t:<7} n={len(rs):>5} communes, rapport médian {m:.3f} "
            f"(10e–90e pct {rs[len(rs) // 10]:.2f}–{rs[9 * len(rs) // 10]:.2f})")
        if not good:
            failures.append(f"{t} : rapport médian {m:.3f}")
    if failures:
        raise SystemExit("ÉCHEC du contrôle croisé avec les statistiques DVF d'Etalab ("
                         + "; ".join(failures) + "). Fichier des prix non écrit : chercher l'erreur "
                         "(filtres DVF, jointure des communes, surfaces) avant de publier.")

    meta = {
        "rayon_km": RADIUS_KM, "ventes_min": MIN_SALES, "percentiles": list(TRIM),
        "dvf_annees": period, "dvf_annees_elargies": period_ext, "dvf_millesime": millesime,
        "dvf_non_couverts": sorted(DVF_NOT_COVERED),
        "loyers_annee": LOYERS_YEAR, "loyers_credit": LOYERS["credit"],
        "fiabilite_loyer": {"obs_min": LOYER_MIN_OBS, "r2_min": LOYER_MIN_R2,
                            "ip_relatif_max": ip_thr, "ip_percentile": LOYER_IP_PCTL},
    }
    out = {"type": "FeatureCollection", "metadata": meta, "features": feats}
    path = out_dir / "communes_prix.geojson"
    path.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    size_mb = path.stat().st_size / 1e6

    # --- prix par gare : commune de la gare + communes citées dans son nom
    keys = ("code", "nom", "dvf", "achat_appart_m2", "achat_maison_m2", "achat_tous_m2", "nb_ventes", "n", "pe",
            "loyer_appart_m2", "loyer_maison_m2", "ipa", "ipm", "fiabilite_loyer", "annee_donnees")
    pick = lambda code: {k: props_by_code[code][k] for k in keys if k in props_by_code[code]}
    by_uic = {uic: pick(code) for uic, code in station_commune.items() if code in props_by_code}
    names_by_uic = {uic: [pick(c) for c in cs if c in props_by_code] for uic, cs in name_communes.items()}

    # --- résumé / contrôles
    vals = list(props_by_code.values())
    n = len(vals)
    log("")
    log("=" * 78)
    log(f"PRIX IMMOBILIERS – {n} communes, fichier {size_mb:.2f} Mo "
        f"(DVF {period}, élargi {period_ext}, millésime {millesime} ; loyers {LOYERS_YEAR})")
    log("=" * 78)
    cov = [v for v in vals if "dvf" not in v]
    for i, t in enumerate(TYPES):
        k = f"achat_{t}_m2"
        main = sum(1 for v in cov if k in v and not v.get("pe", [0, 0, 0])[i])
        ext = sum(1 for v in cov if k in v and v.get("pe", [0, 0, 0])[i])
        insuf = sum(1 for v in cov if k not in v)
        log(f"  achat {t:<7}: {main:>5} communes période {period} + {ext:>4} période élargie {period_ext}"
            f" ; {insuf:>5} insuffisantes (< {MIN_SALES} ventes sur {period_ext})")
    log(f"  DVF non couvert (Alsace-Moselle/Mayotte) : {n - len(cov)}")
    for t in ("appart", "maison"):
        lv = Counter(v.get("fiabilite_loyer", {}).get(t) for v in vals)
        tot = sum(lv.values())
        log(f"  loyer {t:<7}: " + ", ".join(f"{lab} {lv[k]} ({100 * lv[k] / tot:.1f} %)" for k, lab in
                                           (("fiable", "fiable"), ("precaution", "à prendre avec précaution"),
                                            ("peu_fiable", "peu fiable"))))
    log(f"  gares avec prix de commune : {len(by_uic)}/{len(stations)}")

    ok = True
    log("CONTRÔLES PRIX (achat) :")
    ref = {}
    for name, code in (("Reims", "51454"), ("Orléans", "45234"), ("Le Mans", "72181"), ("Amiens", "80021"),
                       ("Lille", "59350"), ("Rouen", "76540")):
        v = props_by_code.get(code, {})
        ref[name] = v.get("achat_appart_m2")
        log(f"  {name:<8} : appart {v.get('achat_appart_m2')} · maison {v.get('achat_maison_m2')} · "
            f"tous biens {v.get('achat_tous_m2')} €/m² ; loyer appart {v.get('loyer_appart_m2')} €/m²/mois")
    # « environ 2 000 à 3 500 € » : tolérance de 10 % sur les bornes (le vrai contrôle est Etalab)
    for name in ("Reims", "Orléans", "Le Mans", "Amiens"):
        good = ref[name] is not None and 1800 <= ref[name] <= 3850
        ok &= good
        log(f"  {'✓' if good else '✗'} {name} appart ≈ 2 000–3 500 €/m² (tolérance 10 % : 1 800–3 850)")
    for name in ("Lille", "Rouen"):
        good = None not in (ref[name], ref["Le Mans"]) and ref[name] > ref["Le Mans"]
        ok &= good
        log(f"  {'✓' if good else '✗'} {name} plus cher que Le Mans (appart)")
    high = sorted(((max(v.get(f"achat_{t}_m2") or 0 for t in TYPES), v["nom"]) for v in vals
                   if max(v.get(f"achat_{t}_m2") or 0 for t in TYPES) > 10000), reverse=True)
    log(f"  {'✓' if not high else '!'} communes > 10 000 €/m² : {high or 'aucune'}")

    sources = [
        {"titre": DVF["title"] + f" – millésime {millesime or '?'}, ventes {period} (élargi {period_ext})",
         "url": DVF["page"], "telechargement": DVF["url"],
         "extrait_le": extraction_date(raw / DVF["file"]) if extraction_date else None},
        {"titre": LOYERS["title"], "url": LOYERS["page"], "telechargement": LOYERS["files"]["appart"][0],
         "extrait_le": extraction_date(raw / LOYERS["files"]["appart"][1]) if extraction_date else None,
         "credit": LOYERS["credit"]},
        {"titre": CONTOURS["title"], "url": CONTOURS["page"], "telechargement": CONTOURS["url"],
         "extrait_le": extraction_date(raw / CONTOURS["file"]) if extraction_date else None},
    ]
    return by_uic, names_by_uic, {**meta, "fichier": "data/communes_prix.geojson", "communes": n,
                                  "taille_mo": round(size_mb, 2)}, sources, ok
