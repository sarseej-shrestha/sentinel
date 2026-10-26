import { defineConfig } from "@playwright/test";
import { existsSync } from "node:fs";
import { homedir } from "node:os";
const cached =
  homedir() +
  "/Library/Caches/ms-playwright/chromium-1234/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing";
export default defineConfig({
  testDir: "./tests",
  timeout: 45000,
  workers: 1,
  retries: 0,
  reporter: "list",
  use: {
    baseURL: "http://127.0.0.1:8766",
    headless: true,
    viewport: { width: 1440, height: 1000 },
    launchOptions: {
      executablePath:
        process.env.CHROMIUM_PATH ?? (existsSync(cached) ? cached : undefined),
    },
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  webServer: {
    command: ".venv/bin/python -m scripts.browser_server",
    cwd: "..",
    url: "http://127.0.0.1:8766/api/catalog",
    reuseExistingServer: !process.env.CI,
    timeout: 30000,
  },
});
