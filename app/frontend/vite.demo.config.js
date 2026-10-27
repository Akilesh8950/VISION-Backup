import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Demo build: produces ONE JS chunk and ONE CSS file in /tmp so the
// post-process step can inline everything into a single self-contained
// demo.html (no assets folder, works from file:// or any static host).
// The production build (vite.config.js -> ../web) is untouched.
export default defineConfig({
  plugins: [react()],
  base: './',
  build: {
    outDir: '/tmp/vision-demo-dist',
    emptyOutDir: true,
    assetsDir: 'assets',
    minify: true,
    cssMinify: true,
    sourcemap: false,
    cssCodeSplit: false,
    modulePreload: false,
    assetsInlineLimit: 100_000_000,
    chunkSizeWarningLimit: 2000,
    rollupOptions: {
      output: {
        inlineDynamicImports: true,
      },
    },
  },
})
