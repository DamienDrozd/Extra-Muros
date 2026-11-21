import { useEffect, useRef } from 'react'
import { useMap } from 'react-leaflet'
import L from 'leaflet'

/**
 * Choroplèthe des communes, rendue en canvas (≈ 6 000 polygones) dans le pane « prices »,
 * sous les marqueurs (SVG) des gares. Créée une seule fois ; les changements de métrique ou
 * d'opacité passent par setStyle, et le filtre de zone ajoute / retire seulement les communes
 * qui changent (pas de recréation). Tooltip et popup sont calculés à la demande.
 *
 * filter : Set des codes INSEE à afficher, ou null pour tout afficher.
 */
export default function PriceLayer({ data, visible, style, tooltip, popup, filter }) {
  const map = useMap()
  const layerRef = useRef(null)
  const subRef = useRef(new Map()) // code INSEE -> sous-couche Leaflet
  const fns = useRef({})
  fns.current = { style, tooltip, popup }

  useEffect(() => {
    if (!data) return undefined
    const renderer = L.canvas({ pane: 'prices', padding: 0.4, tolerance: 0 })
    const layer = L.geoJSON(data, {
      pane: 'prices',
      renderer,
      style: (f) => fns.current.style(f),
      smoothFactor: 1.2,
    })
    const subs = new Map()
    layer.eachLayer((l) => subs.set(l.feature.properties.code, l))
    subRef.current = subs
    layer.bindTooltip((l) => fns.current.tooltip(l.feature), { sticky: true, direction: 'top', offset: [0, -8], className: 'commune-tip', opacity: 1 })
    layer.bindPopup((l) => fns.current.popup(l.feature), { maxWidth: 360, autoPanPadding: [20, 20] })
    layer.on('mouseover', (e) => {
      e.layer.setStyle({ weight: 2, color: '#111827', opacity: 1 })
    })
    layer.on('mouseout', (e) => layer.resetStyle(e.layer))
    layerRef.current = layer
    return () => {
      layer.remove()
      layerRef.current = null
      subRef.current = new Map()
    }
  }, [data, map])

  useEffect(() => {
    const l = layerRef.current
    if (!l) return
    if (visible && !map.hasLayer(l)) l.addTo(map)
    if (!visible && map.hasLayer(l)) l.remove()
  }, [visible, data, map])

  // filtre de zone : diff des sous-couches présentes
  useEffect(() => {
    const l = layerRef.current
    if (!l) return
    for (const [code, sub] of subRef.current) {
      const show = !filter || filter.has(code)
      const has = l.hasLayer(sub)
      if (show && !has) {
        l.addLayer(sub)
        sub.setStyle(fns.current.style(sub.feature))
      } else if (!show && has) l.removeLayer(sub)
    }
  }, [filter, data])

  useEffect(() => {
    layerRef.current?.setStyle((f) => fns.current.style(f))
  }, [style])

  return null
}
