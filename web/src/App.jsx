import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { MapContainer, TileLayer, CircleMarker, Marker, Popup, Tooltip, GeoJSON, Pane, useMapEvents } from 'react-leaflet'
import L from 'leaflet'
import PriceLayer from './PriceLayer.jsx'
import {
  PRICE_MODES, PRICE_TYPES, PRICE_PALETTE, PRICE_PALETTE_DALTONIEN, ND_COLOR, unitOf, metricKey, fmtPrice, fmtBound, priceStatus, computeClasses,
  classIndex, classLabels, hatchPattern, communePopupHtml, communeTooltip, achatText, loyerText,
  LOYER_LEVELS, typeLabel, communeCentroid, makeContains,
} from './prices.js'

const DATA_URL = `${import.meta.env.BASE_URL}data/gares.geojson`
const ISO_URL = `${import.meta.env.BASE_URL}data/isochrones_voiture.geojson`
const COMMUNES_URL = `${import.meta.env.BASE_URL}data/communes_prix.geojson`
const CSV_URL = `${import.meta.env.BASE_URL}data/gares.csv`

export const BUCKETS = [
  { max: 30, label: '≤ 30 min', color: '#1a9850' },
  { max: 60, label: '30–60 min', color: '#f29e0c' },
  { max: 90, label: '60–90 min', color: '#d7301f' },
  { max: 105, label: '90–105 min', color: '#7a0177' }, // voiture uniquement (seuil 1 h 45)
]
const bucketOf = (d) => BUCKETS.find((b) => d <= b.max) || BUCKETS[BUCKETS.length - 1]

// Mode « les deux » : couleur selon le(s) mode(s) qui permettent d'arriver dans le temps imparti
const CATEGORIES = {
  both: { label: 'Train et voiture', color: '#15803d' },
  train: { label: 'Train seulement', color: '#2563eb' },
  car: { label: 'Voiture seulement', color: '#c026d3' },
}
const CAR_COLOR = '#c026d3'

const MODES = [
  { id: 'train', label: 'Train' },
  { id: 'car', label: 'Voiture' },
  { id: 'both', label: 'Les deux' },
]

export function fmtDuration(min) {
  if (min < 60) return `${min} min`
  const h = Math.floor(min / 60)
  const m = min % 60
  return `${h} h ${String(m).padStart(2, '0')}`
}

function norm(s) {
  return (s || '')
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, ' ')
    .replace(/\bst\b/g, 'saint')
    .replace(/\bste\b/g, 'sainte')
    .trim()
}

const fmtDate = (iso) => (iso ? new Date(`${iso}T12:00:00`).toLocaleDateString('fr-FR', { day: 'numeric', month: 'long', year: 'numeric' }) : '?')

/** Nombre de trajets utiles (non dominés : aucun autre ne part plus tard et n'arrive plus tôt). */
function usefulCount(trajets) {
  const uniq = new Map()
  for (const t of trajets) uniq.set(`${t.m}|${t.m + t.d}`, t)
  const sorted = [...uniq.values()].sort((a, b) => b.m - a.m || a.m + a.d - (b.m + b.d))
  let minArr = Infinity
  let n = 0
  for (const t of sorted) {
    if (t.m + t.d < minArr) {
      n++
      minArr = t.m + t.d
    }
  }
  return n
}

/** Volet train avec les filtres : null si pas de trajet, sinon le meilleur trajet + stats. */
function evaluateTrain(props, { maxDur, paris, directOnly }) {
  const cands = (props.trajets || []).filter((t) => t.d <= maxDur && paris.has(t.p) && (!directOnly || !t.v))
  if (!cands.length) return null
  let best = cands[0]
  for (const t of cands) if (t.d < best.d || (t.d === best.d && best.v && !t.v)) best = t
  return {
    best,
    d: best.d,
    trains: usefulCount(cands),
    directs: usefulCount(cands.filter((t) => !t.v)),
    fromParis: new Set(cands.map((t) => t.p)),
  }
}

// À petite échelle les 7 gares sont à quelques pixels les unes des autres : on les
// dispose en éventail (ordre géographique) autour de leur position réelle.
const FAN_ANGLE = { NORD: 90, EST: 38, LYON: -14, BERCY: -66, AUSTERLITZ: -118, MONTPARNASSE: -170, 'SAINT-LAZARE': 142 }
const FAN_ZOOM = 12
const parisIcon = (code, short, zoom) => {
  const r = zoom >= FAN_ZOOM ? 0 : 26
  const a = (FAN_ANGLE[code] * Math.PI) / 180
  const dx = Math.round(r * Math.cos(a))
  const dy = Math.round(-r * Math.sin(a))
  return L.divIcon({
    className: 'paris-icon',
    html: `<span>${short}</span>`,
    iconSize: [24, 24],
    iconAnchor: [12 - dx, 12 - dy],
    popupAnchor: [dx, dy - 12],
    tooltipAnchor: [dx + 10, dy],
  })
}
const carOriginIcon = L.divIcon({
  className: 'car-origin-icon',
  html: '<span>0</span>',
  iconSize: [22, 22],
  iconAnchor: [11, 11],
  popupAnchor: [0, -10],
  tooltipAnchor: [10, 0],
})

const PARIS_SHORT = {
  NORD: 'N', EST: 'E', LYON: 'L', BERCY: 'B', AUSTERLITZ: 'A', MONTPARNASSE: 'M', 'SAINT-LAZARE': 'SL',
}

function ZoomWatcher({ onZoom }) {
  const map = useMapEvents({ zoomend: () => onZoom(map.getZoom()) })
  return null
}

function TrainDetails({ ev, props, maxDur, directOnly, parisByCode }) {
  if (!ev) {
    return (
      <p className="muted small">
        {props.trajets?.length
          ? `Aucun trajet en ≤ ${fmtDuration(maxDur)} avec les filtres actuels.`
          : 'Pas de trajet en train en ≤ 1 h 30 (direct ou 1 correspondance).'}
      </p>
    )
  }
  const t = ev.best
  return (
    <dl>
      <dt>Durée min.</dt>
      <dd><span className="dur" style={{ background: bucketOf(t.d).color }}>{fmtDuration(t.d)}</span></dd>
      <dt>Départ de</dt>
      <dd>{parisByCode[t.p]?.nom}</dd>
      <dt>Horaire</dt>
      <dd>{t.dep} → {t.arr}</dd>
      <dt>Train</dt>
      <dd>{t.t}<br /><span className="muted small">{t.n}</span></dd>
      <dt>Trajet</dt>
      <dd>{t.v ? <>Correspondance à <strong>{t.v}</strong> ({t.w} min)</> : 'Direct'}</dd>
      <dt>Trains / jour</dt>
      <dd>
        <strong>{ev.trains}</strong> en ≤ {fmtDuration(maxDur)}
        {!directOnly && ev.trains !== ev.directs && <span className="muted"> (dont {ev.directs} directs)</span>}
      </dd>
    </dl>
  )
}

function PriceLine({ label, t }) {
  const lvl = t.level ? LOYER_LEVELS[t.level] : null
  return (
    <>
      <dt>{label}</dt>
      <dd>
        <strong className={t.insuf ? 'muted' : ''}>{t.main}</strong>
        {t.ext && <span className="badge-ext">période élargie</span>}
        {t.note && <span className="muted small"> · {t.note}</span>}
        {lvl && <><br /><span className={`lvl ${lvl.cls}`}>{lvl.label}</span>{t.why && <span className="muted small"> {t.why}</span>}</>}
      </dd>
    </>
  )
}

function CommunePrices({ pc, meta, title }) {
  return (
    <div className="commune-prices">
      <p className="cp-title">{title} : <strong>{pc.nom}</strong></p>
      <dl>
        <PriceLine label="Achat tous biens" t={achatText(pc, 'tous', meta)} />
        <PriceLine label="Achat appart." t={achatText(pc, 'appart', meta)} />
        <PriceLine label="Achat maison" t={achatText(pc, 'maison', meta)} />
        <PriceLine label="Loyer appart." t={loyerText(pc, 'appart', meta)} />
        <PriceLine label="Loyer maison" t={loyerText(pc, 'maison', meta)} />
      </dl>
    </div>
  )
}

/** Prix de la commune de la gare (code INSEE SNCF) + des communes citées dans son nom. */
function PriceDetails({ pc, others, meta }) {
  if (!pc) return <p className="muted small">Prix de la commune non disponibles.</p>
  return (
    <>
      <CommunePrices pc={pc} meta={meta} title="Commune de la gare" />
      {(others || []).map((o) => (
        <CommunePrices key={o.code} pc={o} meta={meta} title="Commune citée dans le nom de la gare" />
      ))}
    </>
  )
}

function Segmented({ options, value, onChange, label, disabled }) {
  return (
    <div className="seg" role="radiogroup" aria-label={label}>
      {options.map((o) => (
        <button key={o.id} role="radio" aria-checked={value === o.id} className={value === o.id ? 'active' : ''}
          disabled={disabled || o.disabled} title={o.title} onClick={() => onChange(o.id)}>
          {o.label}
        </button>
      ))}
    </div>
  )
}

function CarDetails({ props, maxDur }) {
  if (props.voiture_min == null) return <p className="muted small">Temps de route non calculé (gare éloignée).</p>
  const ok = props.voiture_min <= maxDur
  return (
    <dl>
      <dt>Durée</dt>
      <dd>
        <span className="dur" style={{ background: ok ? bucketOf(props.voiture_min).color : '#6b7280' }}>
          {fmtDuration(props.voiture_min)}
        </span>
        {!ok && <span className="muted small"> (au-delà de {fmtDuration(maxDur)})</span>}
      </dd>
      <dt>Distance</dt>
      <dd>{Math.round(props.voiture_km)} km</dd>
    </dl>
  )
}

// Préférence de palette mémorisée dans le navigateur (le stockage peut être indisponible :
// navigation privée, cookies bloqués… on retombe alors sur la palette standard).
const PALETTE_KEY = 'gares1h30.palettePrix'
function readPalettePref() {
  try {
    return window.localStorage.getItem(PALETTE_KEY) === 'daltonien' ? 'daltonien' : 'standard'
  } catch {
    return 'standard'
  }
}
function writePalettePref(v) {
  try {
    window.localStorage.setItem(PALETTE_KEY, v)
  } catch {
    /* stockage indisponible : préférence non mémorisée */
  }
}

/** Légende des prix : barre de dégradé par classes, bornes en dessous, pastilles grises à côté. */
function PriceLegendBar({ classes, mode, palette, showInsuf }) {
  const n = palette.length
  return (
    <div className="plb">
      <div className="plb-scale">
        <div className="plb-bar" style={{ gridTemplateColumns: `repeat(${n}, 1fr)` }}>
          {palette.map((c, i) => <span key={c + i} style={{ background: c }} />)}
        </div>
        <div className="plb-labels">
          {classes.breaks.map((b, i) => (
            <span key={i} className={i % 2 ? 'odd' : ''} style={{ left: `${((i + 1) / n) * 100}%` }}>{fmtBound(b, mode)}</span>
          ))}
        </div>
      </div>
      <div className="plb-na">
        <span><span className="swatch" style={{ background: ND_COLOR, borderColor: ND_COLOR }} /> non disponible</span>
        {showInsuf && <span><span className="swatch hatched" /> &lt; 10 ventes</span>}
      </div>
    </div>
  )
}

export default function App() {
  const [data, setData] = useState(null)
  const [iso, setIso] = useState(null)
  const [error, setError] = useState(null)
  const [mode, setMode] = useState('both') // par défaut : train + voiture
  const [maxDur, setMaxDur] = useState(90)
  const [paris, setParis] = useState(null) // Set des codes cochés
  const [directOnly, setDirectOnly] = useState(false)
  const [query, setQuery] = useState('')
  const [panelOpen, setPanelOpen] = useState(false)
  const [zoom, setZoom] = useState(7)
  // prix de l'immobilier
  const [priceShow, setPriceShow] = useState(true) // carte des prix affichée par défaut
  const [priceMode, setPriceMode] = useState('achat')
  // « Tous biens » par défaut (appartements + maisons, médiane du prix au m²)
  const [priceType, setPriceType] = useState('tous')
  const [priceOpacity, setPriceOpacity] = useState(0.6)
  const [priceMax, setPriceMax] = useState(null) // null = sans limite
  const [hideNoPrice, setHideNoPrice] = useState(false)
  const [paletteMode, setPaletteMode] = useState(readPalettePref) // 'standard' | 'daltonien'
  const palette = paletteMode === 'daltonien' ? PRICE_PALETTE_DALTONIEN : PRICE_PALETTE
  // mode « Les deux » : n'afficher que les gares de la zone voiture (accessibles en voiture
  // dans la durée max) ; les gares accessibles uniquement en train sont masquées
  const [bothCarZoneOnly, setBothCarZoneOnly] = useState(true)
  const [zoneOnly, setZoneOnly] = useState(true) // carte des prix limitée à la zone sélectionnée (défaut)
  const [zoneKm, setZoneKm] = useState(15)
  const [communes, setCommunes] = useState(null)
  const [communesState, setCommunesState] = useState('idle') // idle | loading | ok | error
  const [listSort, setListSort] = useState('duree')
  const mapRef = useRef(null)
  const markerRefs = useRef(new Map())

  useEffect(() => {
    fetch(DATA_URL)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status} sur ${DATA_URL}`)
        return r.json()
      })
      .then((d) => {
        setData(d)
        setParis(new Set(d.gares_paris.map((p) => p.code)))
      })
      .catch((e) => setError(String(e.message || e)))
    // isochrones voiture : optionnelles (le site fonctionne sans)
    fetch(ISO_URL)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => d && setIso(d))
      .catch(() => {})
  }, [])

  // couche des communes (~5 Mo) : chargée seulement à la première activation
  useEffect(() => {
    if (!priceShow || communesState !== 'idle') return
    setCommunesState('loading')
    fetch(COMMUNES_URL)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        return r.json()
      })
      .then((d) => {
        setCommunes(d)
        setCommunesState('ok')
      })
      .catch(() => setCommunesState('error'))
  }, [priceShow, communesState])

  // changer de métrique réinitialise le filtre de prix (unités différentes)
  useEffect(() => setPriceMax(null), [priceMode, priceType])
  // pas d'indicateur de loyer « tous biens » : en location, on passe sur appartement
  useEffect(() => {
    if (priceMode === 'loyer' && priceType === 'tous') setPriceType('appart')
  }, [priceMode, priceType])

  // sur mobile, la hauteur de la carte change à l'ouverture du panneau
  useEffect(() => {
    const id = setTimeout(() => mapRef.current?.invalidateSize(), 60)
    return () => clearTimeout(id)
  }, [panelOpen])

  const parisByCode = useMemo(() => Object.fromEntries((data?.gares_paris || []).map((p) => [p.code, p])), [data])
  const carAvailable = !!data?.metadata?.voiture
  const showTrain = mode !== 'car'
  const showCar = mode !== 'train'
  // seuils : train 1 h 30, voiture 1 h 45 (lus dans les métadonnées)
  const trainLimit = data?.metadata?.seuil_min ?? 90
  const carLimit = data?.metadata?.voiture?.seuil_min ?? trainLimit
  const sliderMax = mode === 'train' ? trainLimit : Math.max(trainLimit, carLimit)
  const trainMax = Math.min(maxDur, trainLimit)

  // en changeant de mode : si le curseur était au maximum, il suit le nouveau maximum
  const prevSliderMax = useRef(sliderMax)
  useEffect(() => {
    const prev = prevSliderMax.current // capturé ici : la fonction de mise à jour s'exécute plus tard
    prevSliderMax.current = sliderMax
    setMaxDur((d) => (d >= prev || d > sliderMax ? sliderMax : d))
  }, [sliderMax])

  const stationPrice = useCallback((p) => {
    const s = priceStatus(p.prix_commune, priceMode, priceType)
    return s.kind === 'val' ? s.v : null
  }, [priceMode, priceType])

  // bornes du curseur « prix max » : valeurs des communes des gares pour la métrique active
  const priceRange = useMemo(() => {
    if (!data) return null
    const vals = data.features.map((f) => stationPrice(f.properties)).filter((v) => v != null)
    if (!vals.length) return null
    const step = priceMode === 'achat' ? 100 : 0.5
    return { min: Math.floor(Math.min(...vals) / step) * step, max: Math.ceil(Math.max(...vals) / step) * step, step }
  }, [data, stationPrice, priceMode])

  const visible = useMemo(() => {
    if (!data || !paris) return []
    const q = norm(query)
    const out = []
    for (const f of data.features) {
      const p = f.properties
      if (q && !norm(`${p.nom} ${p.commune}`).includes(q)) continue
      const price = stationPrice(p)
      if (priceMax != null && price != null && price > priceMax) continue
      if (hideNoPrice && price == null) continue
      const train = showTrain ? evaluateTrain(p, { maxDur, paris, directOnly }) : null
      const car = showCar && p.voiture_min != null && p.voiture_min <= maxDur ? { d: p.voiture_min } : null
      if (!train && !car) continue
      if (mode === 'both' && bothCarZoneOnly && !car) continue // hors zone voiture
      let color
      let sortKey
      let category = null
      if (mode === 'train') {
        color = bucketOf(train.d).color
        sortKey = train.d
      } else if (mode === 'car') {
        color = bucketOf(car.d).color
        sortKey = car.d
      } else {
        category = train && car ? 'both' : train ? 'train' : 'car'
        color = CATEGORIES[category].color
        sortKey = Math.min(train?.d ?? Infinity, car?.d ?? Infinity)
      }
      out.push({ f, p, train, car, color, sortKey, category, price })
    }
    // les plus rapides dessinés en dernier (au-dessus)
    return out.sort((a, b) => b.sortKey - a.sortKey)
  }, [data, paris, maxDur, directOnly, query, mode, showTrain, showCar, stationPrice, priceMax, hideNoPrice, bothCarZoneOnly])

  const sortedList = useMemo(() => {
    const l = [...visible]
    if (listSort === 'prix') l.sort((a, b) => (a.price ?? Infinity) - (b.price ?? Infinity) || a.sortKey - b.sortKey)
    else l.sort((a, b) => a.sortKey - b.sortKey || a.p.nom.localeCompare(b.p.nom, 'fr'))
    return l
  }, [visible, listSort])

  // ---- zone sélectionnée (carte des prix limitée à ce que montrent les filtres) :
  //   train : communes dont le centroïde est à ≤ zoneKm d'une gare affichée accessible en train ;
  //   voiture : communes dont le centroïde est dans la zone accessible en voiture (durée max) ;
  //   les deux : union. + communes des gares affichées et communes citées dans leur nom.
  const communeCentroids = useMemo(
    () => (communes ? communes.features.map((f) => [f.properties.code, communeCentroid(f.geometry)]) : null),
    [communes],
  )
  const carZoneContains = useMemo(() => {
    if (!iso || !showCar) return null
    const f = iso.features.find((x) => x.properties.minutes === maxDur)
    return f ? makeContains(f.geometry) : null
  }, [iso, showCar, maxDur])
  const zoneSet = useMemo(() => {
    if (!zoneOnly || !communeCentroids) return null
    const set = new Set()
    const trainSt = visible.filter((v) => v.train).map((v) => v.f.geometry.coordinates)
    const r2 = zoneKm * zoneKm
    for (const [code, [x, y]] of communeCentroids) {
      if (carZoneContains && carZoneContains(x, y)) {
        set.add(code)
        continue
      }
      const kx = 111.32 * Math.cos((y * Math.PI) / 180)
      for (const [lon, lat] of trainSt) {
        const dx = (x - lon) * kx
        const dy = (y - lat) * 110.57
        if (dx * dx + dy * dy <= r2) {
          set.add(code)
          break
        }
      }
    }
    // communes des gares affichées (en mode Voiture seul, les gares ne sont pas affichées :
    // la zone est uniquement celle accessible en voiture)
    if (mode !== 'car') {
      for (const v of visible) {
        if (v.p.prix_commune) set.add(v.p.prix_commune.code)
        for (const o of v.p.prix_communes_nom || []) set.add(o.code)
      }
    }
    return set
  }, [zoneOnly, zoneKm, communeCentroids, carZoneContains, visible, mode])

  // ---- choroplèthe : classes par quantiles sur les communes affichées, une échelle par métrique
  const priceClasses = useMemo(() => {
    if (!communes) return null
    const feats = zoneSet ? communes.features.filter((f) => zoneSet.has(f.properties.code)) : communes.features
    return computeClasses(feats.map((f) => priceStatus(f.properties, priceMode, priceType)).map((s) => (s.kind === 'val' ? s.v : null)), priceMode, palette.length)
  }, [communes, priceMode, priceType, zoneSet, palette.length])

  // contour : trait fin blanc semi-transparent, pour garder les couleurs vives lisibles
  const communeStyle = useCallback((f) => {
    const s = priceStatus(f.properties, priceMode, priceType)
    const base = { weight: 0.6, color: '#ffffff', opacity: 0.5, fillOpacity: priceOpacity }
    if (s.kind === 'val' && priceClasses) return { ...base, fillColor: palette[classIndex(s.v, priceClasses)] }
    if (s.kind === 'insuf') return { ...base, fillColor: hatchPattern(), fillOpacity: Math.min(1, priceOpacity + 0.15) }
    return { ...base, fillColor: ND_COLOR }
  }, [priceMode, priceType, priceOpacity, priceClasses, palette])
  const communeTip = useCallback((f) => communeTooltip(f.properties, priceMode, priceType), [priceMode, priceType])
  const communePopup = useCallback((f) => communePopupHtml(f.properties, communes?.metadata), [communes])
  const priceLegend = useMemo(() => (priceClasses ? classLabels(priceClasses, priceMode, palette) : []), [priceClasses, priceMode, palette])
  const togglePalette = () => {
    const next = paletteMode === 'daltonien' ? 'standard' : 'daltonien'
    setPaletteMode(next)
    writePalettePref(next)
  }

  const suggestions = useMemo(() => {
    if (!query.trim()) return []
    return [...visible].sort((a, b) => a.p.nom.localeCompare(b.p.nom, 'fr')).slice(0, 8)
  }, [visible, query])

  const legendItems = useMemo(() => {
    if (mode === 'both') {
      const c = { both: 0, train: 0, car: 0 }
      for (const v of visible) c[v.category]++
      return Object.entries(CATEGORIES).filter(([k]) => !(bothCarZoneOnly && k === 'train')).map(([k, cat]) => ({ ...cat, count: c[k] }))
    }
    // mode Voiture seul : pas de gares affichées, la légende décrit les anneaux de la zone voiture
    if (mode === 'car') {
      return BUCKETS.filter((b) => b.max - 15 < sliderMax).map((b) => ({ ...b, label: `Zone voiture ${b.label}`, count: null }))
    }
    const c = BUCKETS.map(() => 0)
    for (const v of visible) c[BUCKETS.indexOf(bucketOf(v.sortKey))]++
    return BUCKETS.map((b, i) => ({ ...b, count: c[i] })).filter((b) => b.max - 15 < sliderMax)
  }, [visible, mode, sliderMax, bothCarZoneOnly])

  // Isochrones à afficher : en mode voiture, les seuils 30 / 60 / max colorés par tranche ;
  // en mode « les deux », la seule zone accessible en ≤ durée max.
  const isoLayers = useMemo(() => {
    if (!iso || !showCar) return []
    const byMin = new Map(iso.features.map((f) => [f.properties.minutes, f]))
    const steps = mode === 'car' ? [...new Set([30, 60, 90].filter((v) => v < maxDur).concat(maxDur))] : [maxDur]
    return steps
      .sort((a, b) => b - a)
      .filter((m) => byMin.has(m))
      .map((m) => ({
        m,
        feature: byMin.get(m),
        style: mode === 'car'
          ? { color: bucketOf(m).color, weight: 1.2, fillColor: bucketOf(m).color, fillOpacity: 0.07, opacity: 0.8 }
          : { color: CAR_COLOR, weight: 1.5, dashArray: '5 4', fillColor: CAR_COLOR, fillOpacity: 0.08, opacity: 0.9 },
      }))
  }, [iso, showCar, mode, maxDur])

  const focusStation = (uic) => {
    const v = visible.find((x) => x.p.uic === uic)
    const map = mapRef.current
    if (!v || !map) return
    const [lon, lat] = v.f.geometry.coordinates
    map.flyTo([lat, lon], Math.max(map.getZoom(), 10), { duration: 0.8 })
    map.once('moveend', () => markerRefs.current.get(uic)?.openPopup())
    setPanelOpen(false)
  }

  const toggleParis = (code) => {
    setParis((prev) => {
      const s = new Set(prev)
      if (s.has(code)) s.delete(code)
      else s.add(code)
      return s
    })
  }

  const meta = data?.metadata
  const gtfs = meta?.sources?.find((s) => s.validite)
  const carMeta = meta?.voiture

  const durLabel = (v) => {
    if (mode === 'train') return fmtDuration(v.train.d)
    if (mode === 'car') return fmtDuration(v.car.d)
    return [v.train && `train ${fmtDuration(v.train.d)}`, v.car && `voiture ${fmtDuration(v.car.d)}`].filter(Boolean).join(' · ')
  }

  return (
    <div className={`app ${panelOpen ? 'panel-open' : ''}`}>
      <aside className="panel" aria-label="Filtres">
        <header className="panel-head">
          <div>
            <h1>Gares à 1h30 de Paris</h1>
            <p className="subtitle">En train (≤ {fmtDuration(trainLimit)}) ou en voiture (≤ {fmtDuration(carLimit)}) depuis Paris, hors Île-de-France</p>
          </div>
          <button className="panel-toggle" onClick={() => setPanelOpen((o) => !o)} aria-expanded={panelOpen}>
            {panelOpen ? 'Carte' : 'Filtres'}
          </button>
        </header>

        <div className="mode-switch" role="radiogroup" aria-label="Mode de transport">
          {MODES.map((m) => (
            <button
              key={m.id}
              role="radio"
              aria-checked={mode === m.id}
              className={mode === m.id ? 'active' : ''}
              disabled={m.id !== 'train' && !carAvailable && !!data}
              onClick={() => setMode(m.id)}
            >
              {m.label}
            </button>
          ))}
        </div>

        <div className="counter" aria-live="polite">
          {mode === 'car' ? (
            <>
              <strong>{visible.length}</strong> gare{visible.length > 1 ? 's' : ''} dans la zone voiture
              <span className="muted"> (non affichées en mode Voiture)</span>
            </>
          ) : (
            <>
              <strong>{visible.length}</strong> gare{visible.length > 1 ? 's' : ''} affichée{visible.length > 1 ? 's' : ''}
              {data && <span className="muted"> sur {data.features.length}</span>}
            </>
          )}
        </div>

        <div className="panel-body">
          <section className="control">
            <label htmlFor="search" className="control-label">Rechercher une gare</label>
            <div className="search">
              <input
                id="search"
                type="search"
                placeholder="ex. Chartres, Amiens…"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                autoComplete="off"
              />
              {suggestions.length > 0 && (
                <ul className="suggestions" role="listbox">
                  {suggestions.map((v) => (
                    <li key={v.p.uic}>
                      <button onClick={() => focusStation(v.p.uic)}>
                        <span className="dot" style={{ background: v.color }} />
                        <span className="s-name">{v.p.nom}</span>
                        <span className="s-dur">{durLabel(v)}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </section>

          <section className="control">
            <label htmlFor="maxdur" className="control-label">
              Durée max : <strong>{fmtDuration(maxDur)}</strong>
              {mode === 'both' && maxDur > trainLimit && <span className="muted"> (train plafonné à {fmtDuration(trainLimit)})</span>}
            </label>
            <input
              id="maxdur"
              type="range"
              min="30"
              max={sliderMax}
              step="5"
              value={Math.min(maxDur, sliderMax)}
              onChange={(e) => setMaxDur(Number(e.target.value))}
            />
            <div className="range-scale"><span>30 min</span><span>{fmtDuration(sliderMax)}</span></div>
            {mode === 'both' && (
              <label className="check sub-control">
                <input type="checkbox" checked={bothCarZoneOnly} onChange={(e) => setBothCarZoneOnly(e.target.checked)} />
                <span>
                  Uniquement les gares de la zone voiture
                  <span className="muted small"> (accessibles en voiture en ≤ {fmtDuration(maxDur)})</span>
                </span>
              </label>
            )}
          </section>

          <fieldset className={`control train-filters ${showTrain ? '' : 'is-disabled'}`} disabled={!showTrain}>
            <legend className="control-label">Filtres train{!showTrain && <span className="muted"> (mode voiture)</span>}</legend>
            <label className="check">
              <input type="checkbox" checked={directOnly} onChange={(e) => setDirectOnly(e.target.checked)} />
              Trains directs uniquement
            </label>
            <div className="control-label row sub">
              <span>Gare parisienne de départ</span>
              {data && (
                <span className="links">
                  <button className="link" onClick={() => setParis(new Set(data.gares_paris.map((p) => p.code)))}>toutes</button>
                  {' · '}
                  <button className="link" onClick={() => setParis(new Set())}>aucune</button>
                </span>
              )}
            </div>
            <ul className="paris-list">
              {(data?.gares_paris || []).map((g) => (
                <li key={g.code}>
                  <label className="check">
                    <input type="checkbox" checked={!!paris?.has(g.code)} onChange={() => toggleParis(g.code)} />
                    <span className="paris-badge">{PARIS_SHORT[g.code]}</span>
                    {g.nom}
                  </label>
                </li>
              ))}
            </ul>
          </fieldset>

          <section className="control price-block">
            <div className="control-label">Prix de l'immobilier</div>
            <Segmented label="Achat ou location" options={PRICE_MODES} value={priceMode} onChange={setPriceMode} />
            <Segmented
              label="Type de bien"
              options={PRICE_TYPES.map((t) => (t.id === 'tous' && priceMode === 'loyer'
                ? { ...t, disabled: true, title: 'La carte des loyers ne publie pas d\'indicateur « tous biens »' } : t))}
              value={priceType}
              onChange={setPriceType}
            />

            {priceRange && (
              <div className="sub-control">
                <label htmlFor="pricemax" className="control-label sub">
                  Prix max (commune de la gare) :{' '}
                  <strong>{priceMax == null ? 'sans limite' : fmtPrice(priceMax, priceMode)}</strong>
                </label>
                <input
                  id="pricemax"
                  type="range"
                  min={priceRange.min}
                  max={priceRange.max}
                  step={priceRange.step}
                  value={priceMax ?? priceRange.max}
                  onChange={(e) => {
                    const v = Number(e.target.value)
                    setPriceMax(v >= priceRange.max ? null : v)
                  }}
                />
                <div className="range-scale">
                  <span>{fmtPrice(priceRange.min, priceMode)}</span><span>{fmtPrice(priceRange.max, priceMode)}</span>
                </div>
              </div>
            )}
            <label className="check sub-control">
              <input type="checkbox" checked={hideNoPrice} onChange={(e) => setHideNoPrice(e.target.checked)} />
              <span>
                Masquer les gares sans prix
                <span className="muted small"> ({priceMode === 'achat' ? 'ventes insuffisantes ou hors DVF' : 'non disponible'})</span>
              </span>
            </label>

            <label className="check sub-control">
              <input type="checkbox" checked={priceShow} onChange={(e) => setPriceShow(e.target.checked)} />
              Afficher la carte des prix par commune
              {communesState === 'loading' && <span className="muted small"> · chargement…</span>}
              {communesState === 'error' && <span className="warn small"> · indisponible</span>}
            </label>
            {priceShow && (
              <>
                <label className="check sub-control">
                  <input type="checkbox" checked={zoneOnly} onChange={(e) => setZoneOnly(e.target.checked)} />
                  Uniquement dans la zone sélectionnée
                </label>
                {zoneOnly && (
                  <div className="zone-opts">
                    {showTrain && (
                      <div className="list-sort">
                        Rayon autour des gares
                        <Segmented label="Rayon autour des gares" value={zoneKm} onChange={setZoneKm}
                          options={[5, 10, 15].map((k) => ({ id: k, label: `${k} km` }))} />
                      </div>
                    )}
                    <p className="hint">
                      {mode === 'train' && `Communes à moins de ${zoneKm} km d'une gare affichée.`}
                      {mode === 'car' && `Communes dans la zone accessible en voiture en ≤ ${fmtDuration(maxDur)}.`}
                      {mode === 'both' && `Zone accessible en voiture en ≤ ${fmtDuration(maxDur)} + communes à moins de ${zoneKm} km d'une gare affichée accessible en train.`}
                      {zoneSet && ` ${zoneSet.size} communes.`}
                    </p>
                  </div>
                )}
                <label htmlFor="opacity" className="control-label sub">Opacité : {Math.round(priceOpacity * 100)} %</label>
                <input id="opacity" type="range" min="0.15" max="0.95" step="0.05" value={priceOpacity}
                  onChange={(e) => setPriceOpacity(Number(e.target.value))} />
                <button type="button" className={`palette-toggle ${paletteMode === 'daltonien' ? 'on' : ''}`}
                  aria-pressed={paletteMode === 'daltonien'} onClick={togglePalette}>
                  <span className="pt-swatch" style={{ background: `linear-gradient(90deg, ${(paletteMode === 'daltonien' ? PRICE_PALETTE : PRICE_PALETTE_DALTONIEN).join(',')})` }} />
                  {paletteMode === 'daltonien' ? 'Palette standard (vert → rouge)' : 'Palette daltonien (Viridis)'}
                </button>
                {priceClasses && (
                  <div className="price-legend">
                    <p className="muted small" style={{ margin: '8px 0 6px' }}>
                      {priceMode === 'achat' ? 'Prix médian de vente' : 'Loyer charges comprises'} ({unitOf(priceMode)}), {typeLabel(priceType).toLowerCase()} — déciles des communes affichées
                    </p>
                    <PriceLegendBar classes={priceClasses} mode={priceMode} palette={palette} showInsuf={priceMode === 'achat'} />
                    {priceMode === 'achat' && (
                      <p className="hint">
                        Hachuré : moins de 10 ventes sur {communes?.metadata?.dvf_annees_elargies}. Moins de 10 ventes sur {communes?.metadata?.dvf_annees} : valeur
                        calculée sur {communes?.metadata?.dvf_annees_elargies} (« période élargie »).
                      </p>
                    )}
                  </div>
                )}
              </>
            )}
          </section>

          {mode !== 'car' && <details className="control station-list">
            <summary className="control-label">Liste des gares affichées ({visible.length})</summary>
            <div className="list-sort">
              Trier par
              <Segmented label="Tri" options={[{ id: 'duree', label: 'Durée' }, { id: 'prix', label: 'Prix' }]} value={listSort} onChange={setListSort} />
            </div>
            <ol>
              {sortedList.map((v) => (
                <li key={v.p.uic}>
                  <button onClick={() => focusStation(v.p.uic)}>
                    <span className="dot" style={{ background: v.color }} />
                    <span className="l-name">{v.p.nom}</span>
                    <span className="l-meta">{durLabel(v)}<br /><span className="muted">{v.price != null ? fmtPrice(v.price, priceMode) : 'prix –'}</span></span>
                  </button>
                </li>
              ))}
            </ol>
          </details>}

          <section className="control legend-block">
            <div className="control-label">
              Légende {mode === 'train' ? '(trajet en train le plus rapide)' : mode === 'car' ? '(temps en voiture)' : `(train ≤ ${fmtDuration(trainMax)}, voiture ≤ ${fmtDuration(maxDur)})`}
            </div>
            <ul className="legend">
              {legendItems.map((b) => (
                <li key={b.label}>
                  <span className="dot" style={{ background: b.color }} /> {b.label}
                  {b.count != null && <span className="muted"> · {b.count}</span>}
                </li>
              ))}
              {showCar && iso && (
                <li>
                  <span className={`swatch ${mode === 'both' ? 'dashed' : ''}`} style={{ borderColor: mode === 'both' ? CAR_COLOR : '#888', background: mode === 'both' ? 'rgba(192,38,211,.12)' : 'rgba(0,0,0,.06)' }} />
                  Zone accessible en voiture{mode === 'car' ? ' (30 / 60 / max)' : ''}
                </li>
              )}
              {showTrain && (
                <li><span className="paris-badge small">P</span> Gare parisienne de départ</li>
              )}
              {showCar && (
                <li><span className="car-badge">0</span> Départ voiture : point zéro (Notre-Dame)</li>
              )}
            </ul>
          </section>

          <footer className="sources">
            {meta && (
              <>
                <p>
                  <strong>Train</strong> : horaires théoriques du <strong>mardi {fmtDate(meta.date_reference)}</strong>
                  {gtfs?.version && <> (GTFS SNCF version {gtfs.version})</>}. Trajets directs ou avec
                  1 correspondance (≥ {meta.correspondance_min} min, sans changement de gare dans Paris).
                  TGV INOUI, OUIGO, Intercités, TER ; hors RER/Transilien et autocars.
                </p>
                {carMeta && (
                  <p>
                    <strong>Voiture</strong> : gares à {fmtDuration(carLimit)} maximum, temps de trajet sans trafic depuis le point zéro des routes de France
                    (parvis de Notre-Dame), vitesses théoriques du réseau BD TOPO (calculateur IGN, version {carMeta.version}).
                  </p>
                )}
                {meta.immo && (
                  <p>
                    <strong>Prix</strong> : achat = médiane DVF {meta.immo.dvf_annees} (millésime {meta.immo.dvf_millesime})
                    des ventes d'un seul logement, 5e–95e percentiles, ≥ {meta.immo.ventes_min} ventes, sinon période
                    élargie {meta.immo.dvf_annees_elargies} ; location = carte des loyers {meta.immo.loyers_annee},
                    charges comprises, fiabilité en 3 niveaux d'après le guide ANIL. {meta.immo.loyers_credit}.
                    Communes à moins de {meta.immo.rayon_km} km d'une gare.
                  </p>
                )}
                <p className="src-title">Sources (date d'extraction)</p>
                <ul>
                  {meta.sources.map((s) => (
                    <li key={s.telechargement}>
                      <a href={s.url} target="_blank" rel="noreferrer">{s.titre}</a>
                      {s.extrait_le && <> – {fmtDate(s.extrait_le)}</>}
                    </li>
                  ))}
                </ul>
                <p>
                  <a href={CSV_URL} download>Télécharger les données (CSV)</a> · données générées le {meta.genere_le}
                </p>
              </>
            )}
          </footer>
        </div>
      </aside>

      <main className="map-wrap">
        {error && <div className="banner error">Impossible de charger les données : {error}</div>}
        {!data && !error && <div className="banner">Chargement des données…</div>}
        <MapContainer
          ref={mapRef}
          center={[48.95, 2.4]}
          zoom={7}
          minZoom={5}
          className="map"
          preferCanvas={false}
        >
          <TileLayer
            attribution='&copy; <a href="https://www.openstreetmap.org/copyright">contributeurs OpenStreetMap</a>'
            url="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
            maxZoom={19}
          />
          <ZoomWatcher onZoom={setZoom} />
          <Pane name="prices" style={{ zIndex: 300 }}>
            <PriceLayer data={communes} visible={priceShow} style={communeStyle} tooltip={communeTip} popup={communePopup} filter={zoneSet} />
          </Pane>
          <Pane name="isochrones" style={{ zIndex: 350, pointerEvents: 'none' }}>
            {isoLayers.map((l) => (
              <GeoJSON key={`${mode}-${l.m}`} data={l.feature} style={l.style} interactive={false} />
            ))}
          </Pane>
          {/* mode Voiture seul : les gares ne sont pas affichées (zone voiture + prix uniquement) */}
          {mode !== 'car' && visible.map((v) => {
            const { f, p } = v
            const [lon, lat] = f.geometry.coordinates
            const label = durLabel(v)
            return (
              <Fragment key={p.uic}>
              {/* sur la carte des prix (vert / rouge) : anneau noir + liseré blanc autour de la
                  pastille, pour que la gare reste lisible quelle que soit la couleur dessous */}
              {priceShow && (
                <CircleMarker center={[lat, lon]} radius={9.5} interactive={false}
                  pathOptions={{ color: '#111827', weight: 2, opacity: 0.95, fill: false }} />
              )}
              <CircleMarker
                center={[lat, lon]}
                radius={7}
                pathOptions={priceShow
                  ? { color: '#ffffff', weight: 2, fillColor: v.color, fillOpacity: 1 }
                  : { color: '#ffffff', weight: 1.5, fillColor: v.color, fillOpacity: 0.95 }}
                ref={(m) => {
                  if (m) markerRefs.current.set(p.uic, m)
                  else markerRefs.current.delete(p.uic)
                }}
              >
                <Tooltip direction="top" offset={[0, -6]}>
                  {p.nom} – {label}
                </Tooltip>
                <Popup>
                  <div className="popup">
                    <h3>{p.nom}</h3>
                    <p className="muted">{p.commune}{p.departement ? ` (${p.departement})` : ''}</p>
                    {mode === 'both' && v.category && (
                      <p className="cat" style={{ color: CATEGORIES[v.category].color }}>
                        {CATEGORIES[v.category].label} (train ≤ {fmtDuration(trainMax)}, voiture ≤ {fmtDuration(maxDur)})
                      </p>
                    )}
                    {(mode === 'car' ? ['car', 'train'] : ['train', 'car']).map((section) =>
                      section === 'train' ? (
                        <section key="train" className="popup-section">
                          <h4>En train</h4>
                          <TrainDetails
                            ev={v.train || (mode === 'car' ? evaluateTrain(p, { maxDur: 90, paris, directOnly: false }) : null)}
                            props={p}
                            maxDur={mode === 'car' ? trainLimit : trainMax}
                            directOnly={directOnly}
                            parisByCode={parisByCode}
                          />
                        </section>
                      ) : carAvailable ? (
                        <section key="car" className="popup-section">
                          <h4>En voiture <span className="muted small">(sans trafic)</span></h4>
                          <CarDetails props={p} maxDur={maxDur} />
                        </section>
                      ) : null,
                    )}
                    {meta?.immo && (
                      <section className="popup-section">
                        <h4>Prix de l'immobilier</h4>
                        <PriceDetails pc={p.prix_commune} others={p.prix_communes_nom} meta={meta.immo} />
                      </section>
                    )}
                  </div>
                </Popup>
              </CircleMarker>
              </Fragment>
            )
          })}
          {showTrain && (data?.gares_paris || []).map((g) => {
            const reach = visible.filter((v) => v.train?.fromParis.has(g.code)).length
            return (
              <Marker
                key={g.code}
                position={[g.lat, g.lon]}
                icon={parisIcon(g.code, PARIS_SHORT[g.code], zoom)}
                zIndexOffset={1000}
                opacity={paris?.has(g.code) ? 1 : 0.45}
              >
                <Tooltip direction="right">{g.nom}</Tooltip>
                <Popup>
                  <div className="popup">
                    <h3>{g.nom}</h3>
                    <p className="muted">Gare parisienne de départ</p>
                    <p>
                      {paris?.has(g.code)
                        ? <><strong>{reach}</strong> gare{reach > 1 ? 's' : ''} affichée{reach > 1 ? 's' : ''} atteignable{reach > 1 ? 's' : ''} en train depuis cette gare avec les filtres actuels.</>
                        : 'Gare décochée dans les filtres.'}
                    </p>
                  </div>
                </Popup>
              </Marker>
            )
          })}
          {showCar && carMeta && (
            <Marker
              position={[carMeta.origine.lat, carMeta.origine.lon]}
              icon={carOriginIcon}
              zIndexOffset={900}
            >
              <Tooltip direction="right">Départ voiture : point zéro</Tooltip>
              <Popup>
                <div className="popup">
                  <h3>Point de départ voiture</h3>
                  <p className="muted">{carMeta.origine.nom}</p>
                  <p>Temps calculés sans trafic (vitesses théoriques IGN BD TOPO).</p>
                </div>
              </Popup>
            </Marker>
          )}
        </MapContainer>
        <div className="map-legend" aria-hidden="true">
          {legendItems.map((b) => (
            <span key={b.label}><span className="dot" style={{ background: b.color }} />{b.label}</span>
          ))}
        </div>
        {priceShow && priceLegend.length > 0 && (
          <div className="map-price-legend" aria-label="Légende des prix">
            <div className="mpl-title">
              {priceMode === 'achat' ? 'Achat' : 'Location'} · {typeLabel(priceType).toLowerCase()} ({unitOf(priceMode)})
            </div>
            <PriceLegendBar classes={priceClasses} mode={priceMode} palette={palette} showInsuf={priceMode === 'achat'} />
          </div>
        )}
      </main>
    </div>
  )
}
