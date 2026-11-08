import { fileURLToPath, URL } from 'node:url'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The mock API's CORS allow-list is http://localhost:5173, so the dev server must keep that port.
// PRISM_API (not a VITE_ variable, so it never reaches the bundle) only exists so the error states can be
// exercised against a dead or MOCK=0 backend without touching the mock on port 8000.
const apiProxy = { '/api': process.env.PRISM_API ?? 'http://localhost:8000' }

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: { port: 5173, strictPort: true, proxy: apiProxy },
  preview: { proxy: apiProxy },
})
