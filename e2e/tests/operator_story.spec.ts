import { test } from "@playwright/test";
import { consume, upload, walk } from "../operator_flow";

// The operator story of phase 7.8 (plan goal 1): one operator uploads the synthetic security master, walks the nine
// steps of the workspace (one edited proposal, flagged rules rejected), the first load is released, and a consumer
// previews and downloads the product. Mock provider, four-eyes off (the default of REQUIRE_FOUR_EYES in the run).
//   cd e2e; npx playwright test tests/operator_story.spec.ts

test("the operator story: upload, nine steps, release, preview and download", async ({ browser }) => {
  const backend = await (await browser.newContext()).newPage();
  await upload(backend);
  await walk(backend);
  await consume(browser);
});
