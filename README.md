# Gares à 1h30 de Paris

Carte interactive des gares françaises **hors Île-de-France** atteignables depuis Paris :

- **en train, en 1h30 maximum**, depuis une gare parisienne grandes lignes (Nord, Est,
  Gare de Lyon, Bercy, Austerlitz, Montparnasse, Saint-Lazare) ;
- **en voiture, sans trafic, en 1h45 maximum**, depuis le point zéro des routes de France
  (parvis de Notre-Dame). Le seuil se règle avec `CAR_MAX_MIN` dans `scripts/build_data.py`.

Une couche **prix de l'immobilier par commune** (achat en €/m², location en €/m²/mois,
appartement ou maison) peut s'afficher sous les gares. Un curseur « prix max » masque les
gares dont la commune est trop chère. Tout vient de données ouvertes : aucun scraping de
site d'annonces ni de MeilleursAgents.

Le site propose trois modes : **Train**, **Voiture** et **Les deux** (mode par défaut).
- **Les deux** : la zone accessible en voiture est ombrée et seules les gares **situées dans
  la zone voiture** sont affichées. Elles sont colorées selon qu'on y va en train et en
  voiture, ou en voiture seulement. La case « Uniquement les gares de la zone voiture »,
  cochée par défaut, permet de réafficher aussi les gares accessibles seulement en train.
- **Voiture** : les gares ne sont **pas affichées**. La carte montre la zone accessible en
  voiture (anneaux 30 / 60 / 90 min / durée max) et les prix. Le compteur indique le nombre
  de gares dans la zone.
- **Train** : toutes les gares accessibles en train, colorées par durée.

Au démarrage, la carte des prix est affichée et limitée à la zone sélectionnée, avec un
rayon de 15 km autour des gares.

```
scripts/build_data.py   calcul reproductible (Python ≥ 3.9, bibliothèque standard uniquement)
scripts/prix_immo.py    étape « prix immobiliers » (appelée par build_data.py)
scripts/serve.mjs       mini serveur statique de test (Node, sans dépendance)
web/                    sources du site (React 18 + Leaflet / react-leaflet, Vite)
public/                 site construit, prêt à déployer
  index.html, assets/   ← généré par `npm run build`
  data/gares.geojson    ← généré par le script Python (chargé par le site)
  data/gares.csv        ← même contenu, une ligne par gare
  data/isochrones_voiture.geojson ← zones accessibles en voiture, 30 à 105 min par pas de 5
  data/communes_prix.geojson      ← prix par commune (≈ 6 000 communes, < 5 Mo, chargé à la demande)
data/gares_limites.csv  gares entre 85 et 95 min, pour contrôle manuel
data/raw/               données brutes téléchargées (ignoré par git)
```

## Mettre à jour les données

```bash
python scripts/build_data.py                 # télécharge les sources puis recalcule
python scripts/build_data.py --no-download   # recalcule à partir de data/raw/
python scripts/build_data.py --date 2026-12-08   # impose la date de référence
python scripts/build_data.py --refresh-car   # recalcule aussi les temps voiture (sinon cache)
python scripts/build_data.py --no-car        # train uniquement
python scripts/build_data.py --refresh-immo  # retélécharge DVF, loyers et contours (sinon cache)
python scripts/build_data.py --no-immo       # sans la couche prix
```

Les données de prix sont mises en cache dans `data/raw/immo/`, soit environ 150 Mo de
fichiers DVF départementaux. DVF est publié deux fois par an et la carte des loyers une
fois par an. Pour mettre à jour : `--refresh-immo`. Pour une nouvelle carte des loyers,
changer `LOYERS_YEAR` et les URL dans `scripts/prix_immo.py`. Le premier calcul prend
environ 1 min 30, puis environ 30 s avec le cache.

Les temps voiture sont mis en cache dans `data/raw/voiture/` : isochrones et itinéraires
par gare. Le réseau routier évolue peu, donc un relancement standard réutilise ce cache.
Seules les gares nouvelles sont calculées. `--refresh-car` force le recalcul complet,
soit environ 300 requêtes à 4/s, un peu plus d'une minute.

Le script affiche un résumé (nombre de gares, répartition par tranche de durée, gare
parisienne), des contrôles de cohérence (gares attendues présentes ou absentes) et la liste
des gares limites (85–95 min). Il réécrit `public/data/gares.geojson` et `public/data/gares.csv`.
Le site n'a pas besoin d'être reconstruit après une mise à jour des données.

## Construire et servir le site en local

```bash
npm install
npm run dev        # développement : http://localhost:5173 (sert aussi /data/*)
npm run build      # construit le site dans public/ (sans toucher à public/data/)
npm run serve      # sert public/ sur http://localhost:8080, compressé (brotli/gzip)
```

**Déploiement** : copier le contenu de `public/` tel quel sur n'importe quel hébergeur
statique (GitHub Pages, Netlify, OVH, S3…). Les chemins sont relatifs (`base: './'`), donc
le site fonctionne aussi dans un sous-dossier. Aucune clé d'API n'est nécessaire : tuiles
OpenStreetMap, attribution affichée.

## Sources

| Donnée | Jeu | URL de téléchargement |
|---|---|---|
| Horaires (GTFS) TGV INOUI, OUIGO, Intercités, TER | [Réseau SNCF TGV, Intercités et TER – transport.data.gouv.fr](https://transport.data.gouv.fr/datasets/reseau-sncf-tgv-intercites-et-ter) | https://eu.ftp.opendatasoft.com/sncf/plandata/Export_OpenData_SNCF_GTFS_NewTripId.zip |
| Gares (code UIC, coordonnées) | [Liste des gares – SNCF Open Data](https://ressources.data.sncf.com/explore/dataset/liste-des-gares/) | https://ressources.data.sncf.com/api/explore/v2.1/catalog/datasets/liste-des-gares/exports/csv?delimiter=%3B |
| Gares (complément, code INSEE) | [Gares de voyageurs – SNCF Open Data](https://ressources.data.sncf.com/explore/dataset/gares-de-voyageurs/) | https://ressources.data.sncf.com/api/explore/v2.1/catalog/datasets/gares-de-voyageurs/exports/csv?delimiter=%3B |
| Contour de l'Île-de-France | [Contours administratifs 2025 (Etalab, d'après IGN ADMIN EXPRESS)](https://www.data.gouv.fr/fr/datasets/contours-administratifs/) | https://etalab-datasets.geo.data.gouv.fr/contours-administratifs/2025/geojson/regions-50m.geojson.gz |
| Noms de communes et départements | [API Géo (INSEE)](https://geo.api.gouv.fr/decoupage-administratif) | https://geo.api.gouv.fr/communes?fields=code,nom,codeDepartement&format=json |
| Temps de trajet voiture (sans trafic) et isochrones | [Calcul d'itinéraire et d'isochrone – IGN Géoplateforme](https://geoservices.ign.fr/documentation/services/services-geoplateforme/itineraire), ressource `bdtopo-valhalla` | `https://data.geopf.fr/navigation/itineraire` et `/isochrone` (gratuit, sans clé) |
| Prix de vente (achat) | [DVF géolocalisées – Etalab / DGFiP](https://www.data.gouv.fr/fr/datasets/demandes-de-valeurs-foncieres-geolocalisees/) | `https://files.data.gouv.fr/geo-dvf/latest/csv/<année>/departements/<dep>.csv.gz` |
| Contrôle croisé achat | [Statistiques DVF (Etalab)](https://www.data.gouv.fr/fr/datasets/statistiques-dvf/) | https://data-pipeline-open.s3.sbg.io.cloud.ovh.net/dvf/stats_whole_period.csv |
| Loyers | [Carte des loyers 2025 – ANIL / ministère du Logement](https://www.data.gouv.fr/fr/datasets/carte-des-loyers-indicateurs-de-loyers-dannonce-par-commune-en-2025/) | `pred-app-mef-dhup.csv` (appartements), `pred-mai-mef-dhup.csv` (maisons) |
| Contours des communes | [Contours administratifs 2025, communes 100 m (Etalab, d'après IGN ADMIN EXPRESS)](https://www.data.gouv.fr/fr/datasets/contours-administratifs/) | https://etalab-datasets.geo.data.gouv.fr/contours-administratifs/2025/geojson/communes-100m.geojson.gz |
| Vacances scolaires | [Calendrier scolaire – Éducation nationale](https://data.education.gouv.fr/explore/dataset/fr-en-calendrier-scolaire/) | API `fr-en-calendrier-scolaire` (zones A, B, C) |

Les dates d'extraction et la version du GTFS sont écrites dans `metadata` du GeoJSON et
affichées dans le pied de page du site.

> SNCF publie depuis 2025 un **flux GTFS unique** pour TGV INOUI, OUIGO, Intercités et TER.
> Les anciens flux séparés (`export_gtfs_voyages.zip`, `export-ter-gtfs-last.zip`,
> `export-intercites-gtfs-last.zip`) ne sont plus mis à jour depuis janvier 2025. Le script
> sait tout de même fusionner plusieurs flux : il suffit de les ajouter à `GTFS_FEEDS`.
> Les identifiants sont préfixés par flux et les trains en double sont supprimés.

## Méthode de calcul

1. **Date de référence** : parmi les mardis des 21 prochains jours, on écarte ceux qui
   tombent pendant des vacances scolaires (zones A, B ou C). On retient celui qui a le plus
   de circulations au plan de transport, c'est-à-dire le moins touché par des travaux.
   La date est modifiable avec `--date`.
2. **Trains retenus** : toutes les circulations ferroviaires du GTFS ce jour-là (TGV INOUI,
   OUIGO, Intercités, TER, tram-train, ICE, Lyria). Le mode est lu dans le `stop_id`
   (`StopPoint:OCE<mode>-<UIC>`). Les autocars (« Car TER », « Car à réservation ») et les
   navettes routières sont exclus. Le flux SNCF ne contient pas le RER ni le Transilien.
3. **Origines** : les zones d'arrêt GTFS des 7 gares parisiennes (UIC 87271007 Nord,
   87113001 Est, 87686006 Gare de Lyon, 87686667 Bercy, 87547000 Austerlitz,
   87391003 et 87391102 Montparnasse, 87384008 Saint-Lazare).
4. **Trajets** : pour chaque train quittant une gare parisienne (montée autorisée) :
   - **direct** : chaque arrêt suivant avec descente autorisée ;
   - **1 correspondance** : à chaque arrêt intermédiaire hors Paris, tout autre train qui
     part de **la même gare** (même code UIC) au moins **5 min** après l'arrivée. Les
     correspondances dans une gare parisienne sont interdites, donc aucun changement de
     gare dans Paris n'est possible.
   - durée = arrivée en gare de destination − départ de la gare parisienne, correspondance
     comprise.
5. **Par gare de destination** :
   - durée minimale, avec la gare parisienne, le(s) train(s), le type, direct ou non, la
     gare et le temps de correspondance ;
   - **nombre de trains par jour en ≤ 1h30** : nombre de trajets ≤ 90 min « utiles », c'est-à-dire
     **non dominés** (aucun autre trajet ne part plus tard et n'arrive plus tôt). On évite
     ainsi de compter des combinaisons absurdes (aller plus loin puis revenir, ou attendre
     longtemps une correspondance déjà desservie par un train suivant). Les trains couplés
     aux horaires identiques ne comptent qu'une fois. Le même calcul est fait pour les
     directs seuls.
6. **Rapprochement des gares** : la zone d'arrêt GTFS `StopArea:OCE<UIC>` est reliée par
   code UIC à la « Liste des gares », puis à « Gares de voyageurs ». En repli : même nom
   et distance < 300 m. Lors de l'extraction actuelle, 100 % des gares ont été rapprochées
   par UIC.
7. **Filtre hors Île-de-France** : test point-dans-polygone (ray casting, trous gérés)
   avec le contour officiel de la région 11. Le résultat est comparé au département déduit
   du code INSEE, et tout désaccord est signalé dans la sortie du script.
8. **Export** : gares hors IDF avec une durée ≤ 90 min. Chaque gare du GeoJSON contient
   aussi la liste `trajets` des trajets utiles par gare parisienne, avec
   `p` gare de Paris, `d` durée (min), `m` départ (minutes depuis minuit), `dep`/`arr`,
   `t` type, `v` gare de correspondance, `w` attente et `n` numéros de train. Le site
   s'en sert pour recalculer en direct la durée minimale et le nombre de trains selon les
   filtres (gares parisiennes cochées, directs uniquement, durée max).

## Méthode de calcul – voiture

1. **Origine** : point zéro des routes de France, parvis de Notre-Dame (48.85341, 2.34880).
2. **Moteur** : service d'itinéraire de l'IGN (Géoplateforme), ressource `bdtopo-valhalla`
   sur le réseau BD TOPO. Les vitesses sont **théoriques par type de route, sans
   congestion**, ce qui correspond à « sans bouchons ». Le même moteur sert aux isochrones
   et aux itinéraires.
3. **Seuil** : **1h45** (`CAR_MAX_MIN = 105`), contre 1h30 pour le train.
4. **Gares évaluées** : les gares accessibles en train, plus toutes les gares de voyageurs
   SNCF hors IDF situées dans l'isochrone « seuil + 10 min » (115 min). Pour chacune, on
   calcule l'itinéraire le plus rapide jusqu'à la gare (durée et distance). Une gare est
   « accessible en voiture » si cette durée est ≤ 105 min, arrondie à la minute.
5. **Isochrones** de 30 à 105 min, par pas de 5 min : elles servent seulement à l'affichage
   des zones. Elles sont simplifiées (Douglas-Peucker, environ 200 m) pour alléger le site.
   Le script signale les gares dont l'itinéraire et l'isochrone du seuil ne concordent pas,
   ce qui arrive au bord des polygones, généralisés et un peu généreux.
6. **Contrôles** : Chartres, Creil, Compiègne et Beauvais (moins de 100 km), ainsi que
   Reims et Orléans (environ 140 km d'autoroute), doivent être accessibles en 1h45. Rouen,
   Amiens, Le Mans et Lille ne doivent pas l'être. Le script affiche aussi les vitesses
   moyennes extrêmes et les gares limites voiture (100 à 110 min).

Sur le site, le curseur de durée va jusqu'à 1h30 en mode Train et jusqu'à 1h45 en modes
Voiture et Les deux. Au-delà de 1h30, la partie train reste plafonnée à 1h30.

Champs ajoutés dans le GeoJSON et le CSV : `accessible_train`, `accessible_voiture`,
`voiture_min` et `voiture_km`. Pour une gare accessible seulement en voiture, les champs
train sont vides et `trajets` est une liste vide.

## Méthode – prix de l'immobilier

**Périmètre.** On garde les communes situées à moins de 15 km d'une gare retenue (train
ou voiture), plus la commune de chaque gare et les communes citées dans le nom de la gare
(voir plus bas). Une commune est retenue si un sommet de son contour est à moins de 15 km
ou si elle contient la gare. Cela donne environ 6 000 communes dans 34 départements.

**Commune de la gare.** C'est le code INSEE officiel SNCF, ou à défaut la commune dont le
polygone contient la gare. Quand le nom de la gare **cite une autre commune**, la popup
affiche aussi les prix de cette commune. Par exemple, la gare « Compiègne » est située à
Margny-lès-Compiègne, et la gare « Vendôme-Villiers-sur-Loir » à Vendôme. Règle appliquée :
- on retire du nom de la gare le nom de sa propre commune s'il y figure en entier ;
- on cherche le nom complet d'une commune à moins de 12 km, ou à défaut son premier mot
  s'il est unique (« Coye » → Coye-la-Forêt) ;
- 46 gares sont concernées. Le filtre « prix max » reste sur la commune SNCF.

**Achat (DVF).** Les agrégats publiés ne correspondent pas à la méthode voulue :
« Statistiques DVF » d'Etalab calcule la médiane sur 5 ans sans écrêtage, et l'autre jeu
communal s'arrête en 2024 avec des moyennes. On recalcule donc depuis geo-dvf.
- **Millésime** : le script identifie le millésime daté identique à `geo-dvf/latest`, par
  comparaison d'ETag ; c'est actuellement **2025-12**, avec des données jusqu'au
  31/12/2025, généré par Etalab le 18/05/2026. Il vérifie aussi la **complétude de chaque
  année** : 12 mois présents et aucun mois sous 40 % de la médiane mensuelle. Pour 2025 :
  12 mois sur 12, 41 176 ventes en décembre pour une médiane de 36 468. La DGFiP a publié
  en octobre 2026 un DVF incluant le 1er semestre 2026, mais il n'est pas encore traité
  en geo-dvf.
- **Ventes retenues** : `nature_mutation = "Vente"`, avec exactement **un** local
  « Maison » ou « Appartement » par mutation. Les dépendances sont acceptées ; les
  mutations comportant un local commercial ou industriel sont exclues.
- **Calcul** : prix/m² = `valeur_fonciere / surface_reelle_bati`, en écartant les
  surfaces < 9 m² et les valeurs < 1 000 €. Par commune et par type, on retire les ventes
  hors des 5e–95e percentiles, puis on prend la médiane.
- **Types de bien** : appartements, maisons, et **tous biens**. « Tous biens » est la
  médiane du prix au m² de l'ensemble des ventes d'appartements et de maisons ; c'est le
  type par défaut.
- **Période principale** : les 2 dernières années complètes, soit 2024–2025. Avec **moins
  de 10 ventes**, la commune passe sur la **période élargie** (toutes les années complètes
  du millésime : 2021–2025), et la valeur porte la mention « période élargie ». S'il y a
  encore moins de 10 ventes, la commune est hachurée « données insuffisantes ».
- **Codes communes** : les arrondissements sont rattachés à leur commune. Si
  `code_commune` est absent du COG 2025 des contours (fusion de communes), on essaie
  `ancien_code_commune`.
- **Contrôle croisé Etalab, bloquant** : on compare nos médianes 2024–2025 aux médianes
  « Statistiques DVF » d'Etalab, sur les communes ayant au moins 50 ventes chez Etalab.
  **Le build échoue** (code de sortie 1, fichier non écrit) si le rapport médian sort de
  [0,9 ; 1,1] pour l'un des trois types. Valeurs actuelles : appart 0,980, maison 0,978,
  tous biens 0,975.

**Location (carte des loyers 2025).** On utilise l'indicateur `loypredm2` (€/m²,
charges comprises, logement non meublé de référence), l'intervalle de prédiction à 95 %
(`lwr.IPm2`–`upr.IPm2`), le nombre d'annonces dans la commune (`nbobs_com`), le R² ajusté
et le niveau de prédiction (commune, EPCI ou maille). Il n'existe pas d'indicateur
« tous biens » : en location, ce choix est désactivé.

*Ce que disent le guide et la note méthodologique ANIL 2025.*
- **Le seuil de 30 annonces s'applique à la commune** (`nbobs_com`), pas à la maille. Le
  guide le dit explicitement, comme la note (§ E.2).
- **Le modèle est estimé par maille**, un groupe de communes voisines comparables d'au
  moins 500 annonces, avec un R² ajusté moyen de 0,79 en appartement et 0,74 en maison.
- **Une commune n'a son effet propre qu'à partir de 100 annonces** (`TYPPRED = commune`).
  En dessous, elle reçoit la moyenne des effets de sa maille.
- **Conséquence** : « moins de 30 annonces » signifie « valeur du secteur », pas
  « estimation fragile ». Seuls le R² et la largeur de l'intervalle mesurent la qualité du
  modèle.

*Trois niveaux de fiabilité.*
- **Peu fiable** : R² ajusté < 0,5, ou intervalle de prédiction « très large ». Le guide ne
  chiffre pas ce seuil ; on retient une largeur relative au-delà du **95e percentile
  national** du même type de bien, soit 58 % en appartement et 68 % en maison.
- **À prendre avec précaution** : moins de 30 annonces dans la commune. La valeur est
  celle du secteur, c'est-à-dire de la maille.
- **Fiable** : sinon.

| Répartition (6 041 communes) | Fiable | À prendre avec précaution | Peu fiable |
|---|---|---|---|
| Appartements | 1 450 (24,0 %) | 4 506 (74,6 %) | 85 (1,4 %) |
| Maisons | 1 981 (32,8 %) | 4 027 (66,7 %) | 33 (0,5 %) |

Aucune commune du périmètre n'a un R² ajusté inférieur à 0,5.

Mention obligatoire : *« Estimations ANIL, à partir des données du Groupe SeLoger et de
leboncoin »*. Elle est affichée dans les popups et le pied de page.

**Géométries.** Les contours Etalab sont simplifiés à 100 m avec la topologie préservée.
Les coordonnées sont arrondies à 0,001°, et une **simplification Douglas-Peucker
topologique** (environ 100 m) est appliquée arc par arc : chaque frontière partagée est
simplifiée une seule fois, donc il n'y a ni trou ni chevauchement entre communes
voisines. Le résultat fait environ 5,0 Mo pour environ 6 000 communes, et environ
0,8 Mo transférés une fois compressé (voir « Compression »).

**Champs de `communes_prix.geojson`.**
- `code`, `nom`, `achat_tous_m2`, `achat_appart_m2`, `achat_maison_m2`, `nb_ventes` (tous
  biens, 2024–2025), `loyer_appart_m2`, `loyer_maison_m2`, `fiabilite_loyer`,
  `annee_donnees` (« DVF/loyers », ex. `2024-2025/2025`).
- `n` : nombre de ventes [appart, maison, tous] sur la période utilisée pour chaque type.
- `pe` : [0/1 × 3], indique que la période élargie a été utilisée (absent sinon).
- `ipa` / `ipm` : [borne basse, borne haute, annonces dans la commune, R², niveau].
- `dvf: 0` : commune hors couverture DVF.
- `fiabilite_loyer` : `fiable`, `precaution` ou `peu_fiable`, par type.
- Un champ absent vaut « non disponible ».

**Affichage.**
- **Couleurs** : dégradé **vert → jaune → rouge**, du moins cher au plus cher, en
  **10 classes**. Les couleurs sont dans la constante `PRICE_PALETTE` de
  `web/src/prices.js`, à ajuster à volonté : le nombre de couleurs fixe le nombre de
  classes.
- **Palette daltonien** : le bouton bascule sur `PRICE_PALETTE_DALTONIEN` (Viridis 10
  classes, sans opposition vert/rouge). Le choix est mémorisé dans le navigateur
  (`localStorage`, protégé par try/catch : sans stockage, la page fonctionne et revient
  à la palette standard).
- **Classes** : **déciles** calculés sur les communes affichées, une échelle par
  combinaison achat/location × type de bien. Les bornes sont arrondies au plus grand pas
  (500, 250, 100, 50, 25 ou 10 € ; 1, 0,5, 0,25, 0,1 ou 0,05 €) qui garde 9 bornes
  distinctes.
- **Légende** : barre de dégradé par classes, avec les bornes en dessous (en quinconce)
  et les deux pastilles grises à côté.
- **Cas gris** : gris uni `#7b818a` pour « non disponible » (Alsace-Moselle en achat),
  gris hachuré pour « moins de 10 ventes ». Vérifié en CIEDE2000 à 60 % d'opacité sur le
  fond OSM :
  - écart d'au moins 23 avec toutes les couleurs vert→rouge, y compris le jaune et le
    vert clair ;
  - écart d'au moins 12 avec Viridis ;
  - écart de 17 avec le fond de carte. Le gris précédent, plus clair, ne s'en écartait
    que de 12 et s'y confondait.
- **Contour des communes** : trait fin blanc semi-transparent (0,6 px, 50 %).
- **Rendu** : la couche est rendue en canvas sous les marqueurs SVG des gares, qui restent
  cliquables. Quand la carte des prix est affichée, chaque gare garde sa couleur de durée,
  entourée d'un liseré blanc et d'un anneau noir, pour rester visible sur le rouge comme
  sur le vert.
- **Filtre prix max** : il s'applique au prix de la **commune de la gare** pour la
  métrique active. Une gare dont la commune n'a pas de valeur reste affichée, sauf si la
  case « Masquer les gares sans prix » est cochée.
- **Uniquement dans la zone sélectionnée** (cochée par défaut) : la carte des prix se
  limite à ce que montrent les filtres en cours, et les classes de couleur sont
  recalculées sur ces communes.
  - Mode Train : communes dont le centroïde est à moins de 5, 10 ou 15 km (au choix,
    15 km par défaut) d'une gare affichée.
  - Mode Voiture : communes dont le centroïde est dans la zone accessible en voiture à la
    durée max, uniquement (les gares ne sont pas affichées dans ce mode).
  - Mode Les deux : réunion des deux.
  - En modes Train et Les deux, les communes des gares affichées et celles citées dans
    leur nom sont incluses.

**Compression.** Testé avec `npm run serve` (`scripts/serve.mjs`), qui négocie brotli
ou gzip :

| Fichier | Brut | gzip | brotli |
|---|---|---|---|
| `communes_prix.geojson` | 5,04 Mo | 925 Ko | **792 Ko** |
| `gares.geojson` | 513 Ko | 70 Ko | 53 Ko |
| `isochrones_voiture.geojson` | 532 Ko | 156 Ko | 153 Ko |

Chrome a mesuré un transfert réel de 792 677 octets pour `communes_prix.geojson`.
`vite preview` ne compresse pas le GeoJSON, d'où ce petit serveur de test. En production,
cela dépend de l'hébergeur : le type `application/geo+json` n'est pas toujours dans les
listes par défaut.
- **Apache** : `public/.htaccess`, fourni, active `mod_deflate` / `mod_brotli` pour ce type.
- **nginx** : `gzip on; gzip_types application/geo+json application/json text/csv;`
  (le défaut ne compresse que `text/html`).
- **Autres hébergeurs** : après déploiement, vérifier avec
  `curl -sI -H "Accept-Encoding: br, gzip" https://…/data/communes_prix.geojson | grep -i content-encoding`.

## Limites

- **Horaires théoriques** d'un seul jour : pas de retards, et les travaux ponctuels de ce
  jour-là sont reflétés. Un autre mardi peut donner quelques minutes d'écart sur les gares
  limites (voir `data/gares_limites.csv`). Test fait en novembre 2026 : 249 gares le 24/11,
  247 le 01/12 et 250 le 08/12. Seules des gares à 85–91 min changent (Beaugency,
  Lamotte-Beuvron, Corbie, Hénin-Beaumont, Pont-de-Bois).
- **RER / Transilien exclus** comme demandé : des gares hors IDF desservies surtout par le
  Transilien (Gisors, Château-Thierry, Dreux, Montargis…) n'apparaissent que si un TER ou un
  grand ligne y mène en ≤ 90 min. À l'inverse, une gare hors IDF desservie par des TER
  (Orry-la-Ville, Chantilly…) apparaît, même si le RER y va aussi.
- **Une seule correspondance**, dans la même gare (code UIC) : pas de changement à pied
  entre deux gares proches, comme Lille-Flandres et Lille-Europe.
- Pas de prise en compte des **réservations obligatoires** ni des **tarifs** (OUIGO, etc.).
- Seuls les trains du jour de service choisi sont considérés. Un trajet de fin de soirée
  dont la correspondance passe minuit peut être manqué, ce qui ne concerne pas le trajet
  le plus rapide.
- Les durées sont en minutes entières (granularité du GTFS SNCF).
- Le temps de correspondance minimal est de 5 min partout, alors que la SNCF peut en
  imposer davantage dans les grandes gares.
- **Voiture** : les vitesses IGN sont théoriques et plutôt prudentes. Un test sur
  8 villes donne des temps 0 à 15 % plus longs que le serveur OSRM d'OpenStreetMap, par
  exemple Creil 67 min contre 62. Il n'y a ni trafic ni temps de stationnement. L'origine
  unique en centre de Paris compte environ 15 à 25 min pour sortir de la ville.
- **Train et voiture n'ont pas la même origine** : 7 gares parisiennes pour le train, le
  centre de Paris pour la voiture.
- **Prix d'achat** : DVF ne couvre ni le Bas-Rhin, ni le Haut-Rhin, ni la Moselle (Metz),
  ni Mayotte. Ces communes sont en gris. En **appartement**, même sur 2021–2025, 89 % des
  communes couvertes par DVF ont moins de 10 ventes ; en « tous biens », 23 %.
  Une valeur en « période élargie » mélange 5 ans de prix, avec une hausse en 2021–2022
  puis une baisse ensuite. La médiane DVF mélange aussi tous les états de biens (neuf,
  ancien, à rénover) et toutes les tailles.
- **Loyers** : ce sont des loyers d'**annonces**, des loyers de marché pour un nouveau bail,
  pas la moyenne des loyers en cours. Pour 67 à 75 % des communes du périmètre, la valeur
  est celle du **secteur** (moins de 30 annonces locales) : c'est le niveau « à prendre avec
  précaution ». Les millésimes de la carte des loyers ne sont pas comparables entre eux.
- **Commune de la gare** : une gare en limite communale prend la commune SNCF. Par
  exemple, la gare de Compiègne est rattachée à Margny-lès-Compiègne ; la popup affiche
  aussi les prix de Compiègne.
