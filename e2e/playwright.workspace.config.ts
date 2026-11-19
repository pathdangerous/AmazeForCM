import { defineConfig } from "@playwright/test";
import story, { servers } from "./playwright.config";

// The workspace shots of phase 7.8: the story's servers (same database amaze_test, schema e2e_story), only
// screens/workspace.spec.ts. SHOTS_SET names the folder under docs/phase78-screenshots/.
export default defineConfig({
  ...story,
  testDir: "./screens",
  testMatch: "workspace.spec.ts",
  webServer: servers(false), // the operator path: REQUIRE_FOUR_EYES=false
  use: { ...story.use, viewport: { width: 1280, height: 900 }, colorScheme: "light" },
});
