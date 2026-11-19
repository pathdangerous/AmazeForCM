import { defineConfig } from "@playwright/test";
import story from "./playwright.config";

// The screens run of phase 7.5 (plan, assumption 10 and section 8): the story's servers (the same webServer list,
// so the same database amaze_test, schema e2e_story, rebuilt when the API starts), its own spec under ./screens.
// Not part of the story run. SHOTS_SET (before | after | checkpoint1) names the folder under
// docs/phase75-screenshots/; SHOTS_ONLY (for example "S1,S2,S7") writes only those shots.
//   cd e2e; $env:SHOTS_SET="after"; npx playwright test -c playwright.screens.config.ts
export default defineConfig({
  ...story,
  testDir: "./screens",
  testMatch: "screens.spec.ts", // the story's path: REQUIRE_FOUR_EYES=true, inherited from the story config
  testIgnore: "workspace.spec.ts", // phase 7.8's shots have their own config (playwright.workspace.config.ts)
  use: { ...story.use, viewport: { width: 1280, height: 900 }, colorScheme: "light" },
});
