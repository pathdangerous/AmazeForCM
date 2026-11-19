import { mkdirSync } from "node:fs";
import { test, type Page } from "@playwright/test";
import { consume, REPO, upload, walk } from "../operator_flow";

// The workspace shots of phase 7.8: the operator story's path (e2e/operator_flow.ts, shared with the story, so a
// change to the path is made once) with a full-page shot at each named point, into
// docs/phase78-screenshots/<SHOTS_SET>/. Not part of the story run.
//   cd e2e; $env:SHOTS_SET="final"; npx playwright test -c playwright.workspace.config.ts
const SET = process.env.SHOTS_SET ?? "final";
const DIR = `${REPO}docs/phase78-screenshots/${SET}/`;

async function shot(page: Page, name: string) {
  mkdirSync(DIR, { recursive: true });
  await page.waitForLoadState("networkidle");
  await page.screenshot({ path: `${DIR}${name}.png`, fullPage: true, animations: "disabled" });
}

test("the workspace shots: upload, each step, product ready, product page", async ({ browser }) => {
  const backend = await (await browser.newContext({ viewport: { width: 1280, height: 900 } })).newPage();
  await upload(backend, shot);
  await walk(backend, shot);
  await consume(browser, shot);
});
