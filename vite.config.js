import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const PUBLIC = path.join(here, 'public')

// Le site est construit dans public/ (à côté de public/data/ produit par le script Python).
// En dev, /data/* est servi directement depuis public/data/.
function dataDir() {
  return {
    name: 'serve-public-data',
    configureServer(server) {
      server.middlewares.use('/data', (req, res, next) => {
        const file = path.join(PUBLIC, 'data', decodeURIComponent(req.url.split('?')[0]))
        if (!file.startsWith(path.join(PUBLIC, 'data')) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) return next()
        res.setHeader('Content-Type', file.endsWith('.geojson') ? 'application/geo+json' : file.endsWith('.csv') ? 'text/csv; charset=utf-8' : 'application/octet-stream')
        fs.createReadStream(file).pipe(res)
      })
    },
    buildStart() {
      // nettoie l'ancien build (sans toucher à public/data/)
      fs.rmSync(path.join(PUBLIC, 'assets'), { recursive: true, force: true })
      fs.rmSync(path.join(PUBLIC, 'index.html'), { force: true })
    },
  }
}

export default defineConfig({
  root: 'web',
  base: './',
  publicDir: false,
  plugins: [react(), dataDir()],
  build: {
    outDir: PUBLIC,
    emptyOutDir: false,
  },
})
