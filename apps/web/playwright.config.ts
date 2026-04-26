import { defineConfig, devices } from "@playwright/test";

const useDockerStack = process.env.PLAYWRIGHT_USE_DOCKER === "1";

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  expect: {
    timeout: 10_000,
  },
  use: {
    baseURL: "http://127.0.0.1:3002",
    trace: "retain-on-failure",
  },
  webServer: useDockerStack
    ? undefined
    : {
        command: "pnpm start:local",
        url: "http://127.0.0.1:3002",
        reuseExistingServer: !process.env.CI,
        timeout: 120_000,
        env: {
          AQ_API_URL: process.env.AQ_API_URL ?? "http://localhost:8001",
        },
      },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
