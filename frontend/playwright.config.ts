import { defineConfig, devices } from '@playwright/test'

const BASE_URL = 'http://localhost:5174'

export default defineConfig({
  testDir: './e2e',
  retries: 0,
  forbidOnly: !!process.env.CI,
  use: { baseURL: BASE_URL },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    command: 'npm run dev -- --port 5174 --strictPort',
    url: BASE_URL,
    reuseExistingServer: !process.env.CI,
    env: { VITE_COMPANY_ID: '00000000-0000-4000-8000-000000000001' },
  },
})
