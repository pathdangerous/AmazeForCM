import { defineConfig } from "@playwright/test";
import story, { servers } from "./playwright.config";

// The 7.12 demo story: the story's servers with REQUIRE_FOUR_EYES=false (one operator holds every role).
export default defineConfig({
  ...story,
  testMatch: "demo_story.spec.ts",
  timeout: 600_000,
  use: { ...story.use, actionTimeout: 30_000 },  // fail fast on a wrong selector
  webServer: servers(false),
});
