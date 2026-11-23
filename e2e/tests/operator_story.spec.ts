import { expect, test } from "@playwright/test";
import { BACKEND, CONSUMER, consume, upload, walk } from "../operator_flow";

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

test("the two personas switch apps and retain identity on local ports", async ({ page }) => {
  await page.goto(BACKEND);
  const picker = page.getByTestId("dev-user");
  await expect(picker.locator("option")).toHaveCount(2);
  await expect(picker).toHaveValue("dev-operator");
  await picker.selectOption("dev-requester");
  await expect(page).toHaveURL(CONSUMER);
  await expect(picker).toHaveValue("dev-requester");
  await expect(page.getByTestId("signed-in-as")).toContainText("Riley Requester");
  await page.reload();
  await expect(picker).toHaveValue("dev-requester");
  await picker.selectOption("dev-operator");
  await expect(page).toHaveURL(BACKEND);
  await expect(picker).toHaveValue("dev-operator");
  await expect(page.getByTestId("signed-in-as")).toContainText("Olive Operator");
});
