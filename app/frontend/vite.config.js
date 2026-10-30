import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { fileURLToPath } from 'node:url'

const webDir = fileURLToPath(new URL('../web', import.meta.url))

export default defineConfig({
  plugins: [react()],
  base: './',
  build: {
    outDir: webDir,
    emptyOutDir: true,
    assetsDir: 'assets',
    // WebView2/Chromium is a modern evergreen runtime, so avoid legacy
    // transpilation helpers and the modulepreload polyfill: both only add
    // bytes and parse time to a locally loaded desktop bundle.
    target: 'es2022',
    modulePreload: { polyfill: false },
    // Release bundles are minified and deliberately have no source maps.
    minify: true,
    cssMinify: true,
    sourcemap: false,
    chunkSizeWarningLimit: 900,
  },
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: false,
  },
})
