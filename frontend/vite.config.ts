/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    // Same-origin proxy: the browser only ever talks to :5173, so cookies stay first-party.
    proxy: { '/api': 'http://127.0.0.1:8000' },
  },
  test: { environment: 'jsdom', include: ['src/**/*.test.tsx'] }, // e2e/ is for Playwright
})
