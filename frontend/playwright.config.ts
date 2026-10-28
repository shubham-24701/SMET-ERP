import { defineConfig } from '@playwright/test'

// Runs the real app: API on :8000 on a fresh copy of the test database, web app on :5173.
// CI uses the Edge already installed on Windows (PW_CHANNEL=msedge), so no browser download.
export default defineConfig({
  testDir: './e2e',
  timeout: 90_000,
  workers: 1,
  reporter: process.env.CI ? [['github'], ['list']] : 'list', // github = failures as run annotations
  use: {
    baseURL: 'http://127.0.0.1:5173',
    channel: process.env.PW_CHANNEL || undefined,
    viewport: { width: 1366, height: 768 }, // common Windows laptop resolution
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  webServer: [
    {
      command: 'uv run python -m tests.e2e_server',
      cwd: '../backend',
      url: 'http://127.0.0.1:8000/api/v1/health/ready',
      timeout: 180_000,
    },
    { command: 'npm run dev', url: 'http://127.0.0.1:5173', timeout: 120_000 },
  ],
})
