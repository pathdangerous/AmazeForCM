import { defineConfig } from "@playwright/test";
import story, { servers } from "./playwright.config";

// The operator story of phase 7.8: the story's servers with REQUIRE_FOUR_EYES=false (one operator holds every role).
export default defineConfig({
  ...story,
  testMatch: "operator_story.spec.ts",
  webServer: servers(false),
});
