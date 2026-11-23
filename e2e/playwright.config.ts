import { defineConfig } from "@playwright/test";

// The PoC story (phase 7 plan, assumption 39). Playwright starts the API and both UIs itself, on 8000, 5173 and
// 5174, and never reuses a server: a running dev.ps1 (which serves the application database) stops the run before
// any test. The API is e2e/serve_api.py: database amaze_test, schema e2e_story, DAILY_JOB_ENABLED=false.
// 127.0.0.1, not localhost, as the Vite configs: Node would otherwise pick IPv6 while uvicorn listens on IPv4.
// Each config sets REQUIRE_FOUR_EYES itself (phase 7.8 verification): the PoC story and the screens run with true,
// the operator story and the workspace shots with false. Never taken from the shell.
export function servers(fourEyes: boolean) {
  return [
    {
      command: (process.platform === "win32"
        ? ".venv\\Scripts\\python.exe"
        : ".runtime/python/cpython-3.14-linux-x86_64-gnu/bin/python3.14") + " -m e2e.serve_api",
      cwd: "..",
      url: "http://127.0.0.1:8000/api/health",
      reuseExistingServer: false,
      timeout: 120_000,
      stdout: "pipe" as const,
      env: { REQUIRE_FOUR_EYES: fourEyes ? "true" : "false" },
    },
    {
      command: "npm run dev",
      cwd: "../apps/backend-ui",
      url: "http://127.0.0.1:5173",
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: "npm run dev",
      cwd: "../apps/consumer-ui",
      url: "http://127.0.0.1:5174",
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ];
}

export default defineConfig({
  testDir: "./tests",
  testMatch: "poc_story.spec.ts",
  timeout: 300_000, // the budget of one run
  workers: 1,
  fullyParallel: false,
  retries: 0,
  reporter: [["list"]],
  use: { headless: true, screenshot: "only-on-failure", trace: "off" },
  webServer: servers(true),
});
