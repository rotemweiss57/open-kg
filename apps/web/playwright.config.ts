import { defineConfig, devices } from "@playwright/test";

// Runs against an already-running stack (`make dev` or `make demo`). Set BASE_URL to override.
export default defineConfig({
  testDir: "./e2e",
  timeout: 90_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  globalSetup: "./e2e/teardown.ts",   // also clears leftovers from an interrupted earlier run
  globalTeardown: "./e2e/teardown.ts",
  use: { baseURL: process.env.BASE_URL ?? "http://localhost:3100", trace: "retain-on-failure", screenshot: "only-on-failure" },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } }, testIgnore: /mobile\.spec\.ts/ },
    { name: "mobile", use: { ...devices["Pixel 7"] }, testMatch: /mobile\.spec\.ts/ },
  ],
});
