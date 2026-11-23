import { expect, type Browser, type Page } from "@playwright/test";
import { fileURLToPath } from "node:url";

// The operator's path of phase 7.8 (plan goal 1), written once and used by two specs: e2e/tests/operator_story.spec.ts
// (the test) and e2e/screens/workspace.spec.ts (the same path with shots). One operator (Olive Operator, four-eyes
// off) uploads the synthetic security master, walks the nine steps of the workspace (one edited proposal, flagged
// rules rejected), the first load is released, and a consumer finds the product, previews it and downloads it.
// Mock provider (LLM_PROVIDER=mock), amaze_test / e2e_story. NOT RUN when written (budget rule of the session).

export const BACKEND = "http://127.0.0.1:5173/";
export const CONSUMER = "http://127.0.0.1:5174/market/";
export const REPO = fileURLToPath(new URL("../", import.meta.url));
export const OPERATOR = ["dev-operator", "Olive Operator"] as const;
export const RILEY = ["dev-requester", "Riley Requester"] as const;
export const FILE = `${REPO}fixtures/synthetic/Synthetic_Security_Master.xlsx`;
export const FEED_CODE = "syn_security_master";
export const FEED_NAME = "Synthetic security master";

// A hook the screens run passes to take a shot at a named point; the story passes none.
export type Shot = (page: Page, name: string) => Promise<void>;
const none: Shot = async () => {};

export async function signIn(page: Page, [id, name]: readonly [string, string]) {
  await page.getByTestId("dev-user").selectOption(id);
  await expect(page.getByTestId("signed-in-as")).toContainText(name);
}

// Wait until the agent is not working, then decide what is pending on the current step.
async function settle(page: Page) {
  await expect(page.getByTestId("workspace-working")).toBeHidden({ timeout: 180_000 });
  await expect(page.getByTestId("step-loading")).toBeHidden();
}

export async function upload(page: Page, shot: Shot = none) {
  await page.goto(`${BACKEND}#/onboardings`);
  await signIn(page, OPERATOR);
  await shot(page, "W1-onboardings");
  await page.getByTestId("onboard-a-file").click();
  await expect(page.getByTestId("upload-empty")).toBeVisible();
  await page.getByTestId("upload-file").setInputFiles(FILE);
  await page.getByTestId("upload-send").click();
  await expect(page.getByTestId("upload-preview")).toBeVisible({ timeout: 60_000 });
  await page.getByTestId("upload-feed-code").fill(FEED_CODE);
  await page.getByTestId("upload-feed-name").fill(FEED_NAME);
  await shot(page, "W2-upload-preview");
  await page.getByTestId("upload-confirm").click();
  await expect(page).toHaveURL(/#\/onboarding\//, { timeout: 60_000 });
}

// Walk the workspace to "The product is ready for consumers." Returns how many steps had pending items decided.
export async function walk(page: Page, shot: Shot = none) {
  let edited = false;
  for (let turn = 0; turn < 40; turn++) {
    await settle(page);
    if (await page.getByText("The product is ready for consumers.").isVisible()) break;
    const current = page.locator('[data-testid^="step-"][aria-current="step"]');
    const step = (await current.count()) > 0 ? ((await current.first().getAttribute("data-testid")) ?? "step") : "step";
    await shot(page, `W3-${String(turn).padStart(2, "0")}-${step}`);
    // A screenshot can span an automatically completed step. Re-read the real
    // current step before deciding anything, rather than acting on a stale snapshot.
    await settle(page);
    const stepChanged = async () => (await current.count()) > 0
      && (await current.first().getAttribute("data-testid")) !== step;
    if (await stepChanged()) continue;

    if (await page.getByTestId("summary-page").isVisible()) {
      await page.getByLabel("Open to every user").check(); // the owner chooses visibility at go-live
    } else {
      // one proposal is edited on the first step that offers an Edit button
      if (!edited && (await page.getByRole("button", { name: "Edit", exact: true }).count()) > 0) {
        await page.getByRole("button", { name: "Edit", exact: true }).first().click();
        const text = page.getByLabel("Definition").or(page.getByLabel("Description")).first();
        if (await text.isVisible()) {
          await text.fill(`${await text.inputValue()} (checked by the operator)`);
          await page.getByRole("button", { name: "Save edit" }).click();
          edited = true;
          await settle(page);
        }
      }
      // rules that flag rows are rejected, everything else approved; a choice takes its first option
      const items = page.locator('[data-testid^="item-"]:not([data-testid^="item-badge"]):not([data-testid="item-decider"])');
      for (let i = 0; i < 200; i++) {
        // open = still has an Approve button (a "Pre-accepted" card offers Reject and Edit only)
        const open = items.filter({ has: page.getByRole("button", { name: "Approve", exact: true }) });
        const before = await open.count();
        if (before === 0) break;
        const card = open.first();
        const flagged = /Flags \d+ of \d+ rows/.test((await card.textContent()) ?? "");
        const choice = card.getByRole("combobox");
        if (!flagged && (await choice.count()) > 0) await choice.selectOption({ index: 1 });
        if (flagged) {
          await card.getByRole("button", { name: "Reject", exact: true }).click();
          await card.getByLabel("Why it is rejected").fill("Flags too many rows for a demo feed");
          await card.getByRole("button", { name: "Confirm", exact: true }).click();
        } else {
          await card.getByRole("button", { name: "Approve", exact: true }).click();
        }
        // wait for the decision to show (the card loses its Approve button) before the next one: the card locator is
        // lazy, so a last card that is already decided would otherwise be waited for until the test times out
        await expect(open).toHaveCount(before - 1, { timeout: 60_000 });
        await settle(page);
      }
    }
    const next = page.getByTestId("workspace-approve");
    const ready = page.getByText("The product is ready for consumers.");
    // the last step's approval may already have finished the onboarding: then there is nothing more to approve
    await expect(async () => {
      if (await ready.isVisible()) return;
      if (await stepChanged()) return; // an automatic transition needs a fresh decision pass
      await expect(next).toBeEnabled({ timeout: 1_000 });
    }).toPass({ timeout: 30_000 });
    if (await ready.isVisible()) break;
    if (await stepChanged()) continue;
    await next.click();
  }
  await expect(page.getByText("The product is ready for consumers.")).toBeVisible();
  await shot(page, "W4-product-ready");
}

export async function consume(browser: Browser, shot: Shot = none) {
  const consumer = await (await browser.newContext()).newPage();
  await consumer.goto(`${CONSUMER}#/catalogue`);
  await signIn(consumer, RILEY);
  await consumer.getByTestId(`catalogue-product-${FEED_CODE}`).click();
  await expect(consumer.getByTestId("product-preview")).toBeVisible({ timeout: 60_000 });
  await expect(consumer.getByTestId("product-dictionary")).toBeVisible();
  await shot(consumer, "W5-product-page");
  const download = consumer.waitForEvent("download");
  await consumer.getByTestId("product-download-csv").click();
  expect((await download).suggestedFilename()).toMatch(/\.csv$/);
  const xlsx = consumer.waitForEvent("download");
  await consumer.getByTestId("product-download-xlsx").click();
  expect((await xlsx).suggestedFilename()).toMatch(/\.xlsx$/);
}
