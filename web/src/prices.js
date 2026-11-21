// Prix de l'immobilier : métriques, statut des communes, classes, couleurs, formats, popups.

export const PRICE_MODES = [
  { id: 'achat', label: 'Achat', unit: '€/m²' },
  { id: 'loyer', label: 'Location', unit: '€/m²/mois' },
]
// « Tous biens » n'existe qu'à l'achat (la carte des loyers ne publie que appart / maison)
export const PRICE_TYPES = [
  { id: 'tous', label: 'Tous biens' },
  { id: 'appart', label: 'Appartement' },
  { id: 'maison', label: 'Maison' },
]
const TYPE_INDEX = { appart: 0, maison: 1, tous: 2 }
export const unitOf = (mode) => (mode === 'achat' ? '€/m²' : '€/m²/mois')
export const metricKey = (mode, type) => `${mode}_${type}_m2`
export const typeLabel = (type) => PRICE_TYPES.find((t) => t.id === type)?.label || type

// ---------------------------------------------------------------- palettes (à ajuster ici)
// Palette des prix, du moins cher au plus cher (vert → jaune → rouge). Le nombre de couleurs
// fixe le nombre de classes (bornes par quantiles : 10 couleurs = déciles).
export const PRICE_PALETTE = [
  '#1a9850', '#4cb050', '#8cc63f', '#c5e03a', '#f2ee3c',
  '#fdd23c', '#fdae3a', '#f98a2e', '#ef4b2b', '#c4161c',
]
// Variante « daltonien » : Viridis 10 classes (pas d'opposition vert / rouge)
export const PRICE_PALETTE_DALTONIEN = [
  '#440154', '#482878', '#3e4989', '#31688e', '#26828e',
  '#1f9e89', '#35b779', '#6ece58', '#b5de2b', '#fde725',
]
// Cas spéciaux, volontairement neutres (gris non saturé). Vérifié en CIEDE2000, à 60 %
// d'opacité sur le fond OSM : ΔE ≥ 23 avec la palette vert→rouge (dont le jaune et le vert
// clair), ≥ 12 avec Viridis, et 17 avec le fond de carte (un gris plus clair s'y confondait).
export const ND_COLOR = '#7b818a'
const HATCH_BG = '#d9dce1'
const HATCH_LINE = '#7b818a'
export const MIN_SALES = 10

const nf0 = new Intl.NumberFormat('fr-FR', { maximumFractionDigits: 0 })
const nf1 = new Intl.NumberFormat('fr-FR', { minimumFractionDigits: 1, maximumFractionDigits: 1 })
const nf2 = new Intl.NumberFormat('fr-FR', { maximumFractionDigits: 2 })
export const fmtPrice = (v, mode) => (v == null ? '–' : `${mode === 'achat' ? nf0.format(v) : nf1.format(v)} ${unitOf(mode)}`)
export const fmtBound = (v, mode) => (mode === 'achat' ? nf0.format(v) : nf2.format(v))

/**
 * Statut d'une commune pour une métrique : {kind: 'val', v, ext} | {kind: 'nd'} (non disponible)
 * | {kind: 'insuf'} (moins de 10 ventes même sur la période élargie).
 * `p` = propriétés de commune (ou prix_commune d'une gare). `ext` = période élargie utilisée.
 */
export function priceStatus(p, mode, type) {
  if (!p) return { kind: 'nd' }
  const v = p[metricKey(mode, type)]
  if (mode === 'achat') {
    if (p.dvf === 0) return { kind: 'nd' }
    if (v == null) return { kind: 'insuf' }
    return { kind: 'val', v, ext: !!p.pe?.[TYPE_INDEX[type]] }
  }
  if (v == null) return { kind: 'nd' }
  return { kind: 'val', v }
}

/**
 * n classes par quantiles (10 = déciles) sur les valeurs affichées ; bornes arrondies au plus
 * grand pas « propre » qui garde n − 1 bornes strictement croissantes.
 */
export function computeClasses(values, mode, n = PRICE_PALETTE.length) {
  const vals = values.filter((v) => v != null).sort((a, b) => a - b)
  if (vals.length < n) return null
  const q = Array.from({ length: n - 1 }, (_, k) => vals[Math.min(vals.length - 1, Math.floor(((k + 1) / n) * vals.length))])
  const steps = mode === 'achat' ? [500, 250, 100, 50, 25, 10] : [1, 0.5, 0.25, 0.1, 0.05]
  for (const s of steps) {
    const b = q.map((x) => Math.round(Math.round(x / s) * s * 100) / 100)
    if (b.every((x, i) => i === 0 || x > b[i - 1])) return { breaks: b, step: s, n, min: vals[0], max: vals[vals.length - 1] }
  }
  return { breaks: q, step: null, n, min: vals[0], max: vals[vals.length - 1] }
}
export function classIndex(v, classes) {
  let i = 0
  while (i < classes.breaks.length && v >= classes.breaks[i]) i++
  return i
}
export function classLabels(classes, mode, palette = PRICE_PALETTE) {
  const b = classes.breaks
  return palette.map((color, i) => ({
    color,
    label: i === 0 ? `< ${fmtBound(b[0], mode)}` : i === b.length ? `≥ ${fmtBound(b[b.length - 1], mode)}` : `${fmtBound(b[i - 1], mode)} – ${fmtBound(b[i], mode)}`,
  }))
}

// Hachures (canvas) pour « données insuffisantes »
let hatch = null
export function hatchPattern() {
  if (hatch) return hatch
  const c = document.createElement('canvas')
  c.width = c.height = 8
  const x = c.getContext('2d')
  x.fillStyle = HATCH_BG
  x.fillRect(0, 0, 8, 8)
  x.strokeStyle = HATCH_LINE
  x.lineWidth = 1.4
  x.beginPath()
  x.moveTo(0, 8); x.lineTo(8, 0)
  x.moveTo(-2, 2); x.lineTo(2, -2)
  x.moveTo(6, 10); x.lineTo(10, 6)
  x.stroke()
  hatch = x.createPattern(c, 'repeat')
  return hatch
}

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c])

// Fiabilité des loyers (3 niveaux, d'après le guide et la note méthodologique ANIL 2025)
export const LOYER_LEVELS = {
  fiable: { label: 'Fiable', cls: 'lvl-ok' },
  precaution: { label: 'À prendre avec précaution', cls: 'lvl-mid' },
  peu_fiable: { label: 'Peu fiable', cls: 'lvl-bad' },
}

/** Libellés de période d'achat : principale / élargie, depuis les métadonnées. */
export const periods = (meta) => ({ main: meta?.dvf_annees || '', ext: meta?.dvf_annees_elargies || '' })

/** Détail d'une valeur d'achat. */
export function achatText(p, type, meta) {
  const s = priceStatus(p, 'achat', type)
  const n = p?.n?.[TYPE_INDEX[type]]
  const per = periods(meta)
  if (s.kind === 'nd') return { main: 'Non disponible', note: "DVF ne couvre pas l'Alsace-Moselle ni Mayotte" }
  if (s.kind === 'insuf') return { main: 'Données insuffisantes', note: `${n ?? 0} vente${n > 1 ? 's' : ''} sur ${per.ext} (< ${MIN_SALES})`, insuf: true }
  return {
    main: fmtPrice(s.v, 'achat'),
    note: s.ext ? `période élargie ${per.ext}, ${n} ventes` : `médiane ${per.main}, ${n} ventes`,
    ext: s.ext,
  }
}

/** Détail d'un loyer, avec niveau de fiabilité et explication. */
export function loyerText(p, type, meta) {
  if (type === 'tous') return { main: '–', note: 'pas d\'indicateur « tous biens » dans la carte des loyers' }
  const s = priceStatus(p, 'loyer', type)
  if (s.kind !== 'val') return { main: 'Non disponible' }
  const ip = type === 'appart' ? p.ipa : p.ipm
  const level = p.fiabilite_loyer?.[type] || 'fiable'
  const reasons = []
  if (ip) {
    if (ip[3] != null && ip[3] < 0.5) reasons.push(`modèle peu explicatif (R² ${nf2.format(ip[3])})`)
    const thr = meta?.fiabilite_loyer?.ip_relatif_max?.[type]
    if (thr && (ip[1] - ip[0]) / s.v > thr) reasons.push('intervalle de prédiction très large')
    if (level === 'precaution') reasons.push(`${ip[2]} annonce${ip[2] > 1 ? 's' : ''} dans la commune : valeur du secteur (groupe de communes voisines comparables)`)
  }
  return {
    main: fmtPrice(s.v, 'loyer'),
    note: ip ? `intervalle ${nf1.format(ip[0])}–${nf1.format(ip[1])} €` : '',
    level,
    why: reasons.join(' ; '),
  }
}

function row(label, t) {
  const lvl = t.level ? LOYER_LEVELS[t.level] : null
  return `<tr><th>${esc(label)}</th><td><strong>${esc(t.main)}</strong>${t.ext ? ' <span class="badge-ext">période élargie</span>' : ''}`
    + `${t.note ? `<br><span class="muted small">${esc(t.note)}</span>` : ''}`
    + `${lvl ? `<br><span class="lvl ${lvl.cls}">${esc(lvl.label)}</span>${t.why ? ` <span class="muted small">${esc(t.why)}</span>` : ''}` : ''}</td></tr>`
}

export function communePopupHtml(p, meta) {
  const per = periods(meta)
  return `<div class="popup commune-popup">
    <h3>${esc(p.nom)}</h3><p class="muted">Commune ${esc(p.code)}</p>
    <h4>Achat <span class="muted small">(DVF ${esc(per.main)}, médiane au m²)</span></h4>
    <table>${row('Tous biens', achatText(p, 'tous', meta))}${row('Appartement', achatText(p, 'appart', meta))}${row('Maison', achatText(p, 'maison', meta))}</table>
    <h4>Location <span class="muted small">(carte des loyers ${esc(meta?.loyers_annee || '')}, charges comprises)</span></h4>
    <table>${row('Appartement', loyerText(p, 'appart', meta))}${row('Maison', loyerText(p, 'maison', meta))}</table>
    <p class="src small">Sources : DVF (DGFiP / Etalab), millésime ${esc(meta?.dvf_millesime || '')} ; ${esc(meta?.loyers_credit || 'Estimations ANIL')}.</p>
  </div>`
}

export function communeTooltip(p, mode, type) {
  const s = priceStatus(p, mode, type)
  const val = s.kind === 'val' ? fmtPrice(s.v, mode) + (s.ext ? ' (période élargie)' : '') : s.kind === 'insuf' ? 'données insuffisantes' : 'non disponible'
  return `<strong>${esc(p.nom)}</strong> · ${esc(val)}`
}

// ---------------------------------------------------------------- zone sélectionnée
/** Point représentatif d'une commune : centroïde (pondéré par l'aire) de son plus grand anneau. */
export function communeCentroid(geom) {
  const polys = geom.type === 'MultiPolygon' ? geom.coordinates : [geom.coordinates]
  let best = null
  for (const poly of polys) {
    const r = poly[0]
    let a = 0, cx = 0, cy = 0
    for (let i = 0, j = r.length - 1; i < r.length; j = i++) {
      const f = r[j][0] * r[i][1] - r[i][0] * r[j][1]
      a += f
      cx += (r[j][0] + r[i][0]) * f
      cy += (r[j][1] + r[i][1]) * f
    }
    if (!best || Math.abs(a) > Math.abs(best.a)) best = { a, x: a ? cx / (3 * a) : r[0][0], y: a ? cy / (3 * a) : r[0][1] }
  }
  return [best.x, best.y]
}

function inRing(x, y, ring) {
  let inside = false
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i]
    const [xj, yj] = ring[j]
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside
  }
  return inside
}
/** Test point-dans-polygone (MultiPolygon / Polygon, trous gérés) avec pré-filtre par emprise. */
export function makeContains(geom) {
  const polys = geom.type === 'MultiPolygon' ? geom.coordinates : [geom.coordinates]
  const boxes = polys.map((p) => {
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity
    for (const [x, y] of p[0]) { x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y) }
    return [x0, y0, x1, y1]
  })
  return (x, y) => polys.some((p, i) => {
    const b = boxes[i]
    return x >= b[0] && x <= b[2] && y >= b[1] && y <= b[3] && inRing(x, y, p[0]) && !p.slice(1).some((h) => inRing(x, y, h))
  })
}
