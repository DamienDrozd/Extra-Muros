// Mini serveur statique sans dépendance, pour tester public/ comme le ferait un hébergeur.
// Compression négociée (Accept-Encoding) : brotli, sinon gzip, pour les types texte
// (HTML, JS, CSS, JSON, GeoJSON, CSV, SVG). Résultats mis en cache en mémoire.
// Usage : node scripts/serve.mjs [dossier=public] [port=8080]
import http from 'node:http'
import fs from 'node:fs'
import path from 'node:path'
import zlib from 'node:zlib'

const root = path.resolve(process.argv[2] || 'public')
const port = Number(process.argv[3] || 8080)
const TYPES = {
  '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8',
  '.json': 'application/json', '.geojson': 'application/geo+json', '.csv': 'text/csv; charset=utf-8',
  '.png': 'image/png', '.svg': 'image/svg+xml', '.ico': 'image/x-icon',
}
const COMPRESSIBLE = new Set(['.html', '.js', '.css', '.json', '.geojson', '.csv', '.svg'])
const cache = new Map() // `${file}|${mtime}|${encoding}` -> Buffer

function compressed(file, mtime, encoding) {
  const key = `${file}|${mtime}|${encoding}`
  if (!cache.has(key)) {
    const raw = fs.readFileSync(file)
    cache.set(key, encoding === 'br'
      ? zlib.brotliCompressSync(raw, { params: { [zlib.constants.BROTLI_PARAM_QUALITY]: 9, [zlib.constants.BROTLI_PARAM_SIZE_HINT]: raw.length } })
      : zlib.gzipSync(raw, { level: 9 }))
  }
  return cache.get(key)
}

http.createServer((req, res) => {
  let p = decodeURIComponent(new URL(req.url, 'http://x').pathname)
  if (p.endsWith('/')) p += 'index.html'
  const file = path.join(root, p)
  if (!file.startsWith(root) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) {
    res.writeHead(404, { 'Content-Type': 'text/plain' }); res.end('404'); return
  }
  const ext = path.extname(file)
  const headers = { 'Content-Type': TYPES[ext] || 'application/octet-stream' }
  const accept = String(req.headers['accept-encoding'] || '')
  const encoding = !COMPRESSIBLE.has(ext) ? null : /\bbr\b/.test(accept) ? 'br' : /\bgzip\b/.test(accept) ? 'gzip' : null
  if (COMPRESSIBLE.has(ext)) headers.Vary = 'Accept-Encoding'
  if (encoding) {
    const body = compressed(file, fs.statSync(file).mtimeMs, encoding)
    res.writeHead(200, { ...headers, 'Content-Encoding': encoding, 'Content-Length': body.length })
    res.end(req.method === 'HEAD' ? undefined : body)
    return
  }
  res.writeHead(200, { ...headers, 'Content-Length': fs.statSync(file).size })
  if (req.method === 'HEAD') res.end()
  else fs.createReadStream(file).pipe(res)
}).listen(port, () => console.log(`Serveur statique : http://localhost:${port}/  (dossier ${root}, compression br/gzip)`))
