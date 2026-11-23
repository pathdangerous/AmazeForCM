import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { fileURLToPath } from "node:url";
import { BACKEND, FEED_CODE, REPO, upload, walk, type StepHooks } from "../operator_flow";

// The 10-minute demo of phase 7.12 (docs/demo-7.12.md), as one story: upload version 1, profile and actions, a rule added
// by hand, one agent rule edited and one rejected (T7), the completion report, code version 1, approve and release; then
// version 2 (a new column and two columns swapped): drift, its actions, the update, code version 2 and the diff, and the
// overview. Mock provider, one operator who holds every role (REQUIRE_FOUR_EYES=false), schema e2e_story.
//   cd e2e; npx playwright test -c playwright.demo.config.ts
// The steps after "Load now" on version 2 that have no screen in this story (approving the new column's glossary element and
// mapping, preparing and approving the new go-live) go through the API, as the older feed-page forms do.

const V2 = `${REPO}fixtures/sources/Synthetic_Security_Master_v2.xlsx`;
const API = "http://127.0.0.1:8000";
const OPERATOR = { "X-Dev-User": "dev-operator" };

async function reject(page: Page, card: ReturnType<Page["locator"]>, why: string) {
  await card.getByRole("button", { name: "Reject", exact: true }).click();
  await card.getByLabel("Why it is rejected").fill(why);
  await card.getByRole("button", { name: "Confirm", exact: true }).click();
}

const hooks: StepHooks = {
  // T1: the facts in plain words, each action says who does it; a person adds one and rejects one
  "step-profile": async (page) => {
    await expect(page.getByTestId("profile-plain")).toBeVisible();
    await expect(page.getByTestId("action-who").first()).toBeVisible();
    await page.getByRole("button", { name: "Add action" }).click();
    const form = page.getByRole("group", { name: "Add action" });
    await form.getByLabel("Action", { exact: true }).selectOption("add_rule");
    await form.getByLabel("Column", { exact: true }).selectOption("SECTOR");
    await form.getByLabel("Why", { exact: true }).fill("A sector must never be empty.");
    await form.getByRole("button", { name: "Add item" }).click();
    await expect(page.getByText("added by Olive Operator").first()).toBeVisible();
    const policy = page.locator('[data-testid^="item-"]:not([data-testid^="item-badge"])').filter({ hasText: "Decide what happens" }).first();
    await reject(page, policy, "The default policy is enough for the demo.");
  },
  // T7: add one rule by hand (back-tested), edit one agent rule, reject one
  "step-rules": async (page) => {
    const cards = page.locator('[data-testid^="item-"]:not([data-testid^="item-badge"]):not([data-testid="item-decider"])');
    await page.getByRole("button", { name: "Add rule" }).click();
    const form = page.getByRole("group", { name: "Add rule" });
    if ((await form.getByLabel("How", { exact: true }).count()) > 0) await form.getByLabel("How", { exact: true }).selectOption("own");
    await form.getByLabel("Rule key", { exact: true }).fill("demo_ticker_is_short");
    await form.getByLabel("Description", { exact: true }).fill("A ticker is shorter than 60 characters.");
    await form.getByLabel("SQL").fill('SELECT _row FROM curated WHERE length("ticker") > 60');
    await form.getByLabel("Elements it checks").fill("ticker");
    await form.getByRole("button", { name: "Add item" }).click();
    await expect(cards.filter({ hasText: "added by Olive Operator" }).filter({ hasText: "demo_ticker_is_short" })).toHaveCount(1,
      { timeout: 60_000 });
    const agents = cards.filter({ hasNotText: "added by" }).filter({ has: page.getByRole("button", { name: "Edit", exact: true }) });
    const edit = agents.first();
    await edit.getByRole("button", { name: "Edit", exact: true }).click();
    await page.getByLabel("Description").first().fill("Reworded by the operator.");
    await page.getByRole("button", { name: "Save edit" }).click();
    await expect(cards.filter({ hasText: "edited by" })).not.toHaveCount(0, { timeout: 60_000 });
    const other = cards.filter({ hasNotText: "added by" }).filter({ hasNotText: "edited by" })
      .filter({ has: page.getByRole("button", { name: "Reject", exact: true }) }).first();
    const id = await other.getAttribute("data-testid"); // the card stops matching once Reject is pressed: pin it by id
    await reject(page, page.getByTestId(id!), "Not needed for the demo.");
  },
  // T2: the completion report before go-live
  "step-go_live": async (page) => {
    const report = page.getByTestId("completion-report");
    await expect(report).toBeVisible();
    await expect(report).toContainText("added by hand");
    await expect(report).toContainText("Items:");
  },
};

async function json(api: APIRequestContext, path: string, init?: { method?: string; data?: unknown }) {
  const response = init?.method === "POST"
    ? await api.post(`${API}${path}`, { headers: OPERATOR, data: init.data ?? {} })
    : await api.get(`${API}${path}`, { headers: OPERATOR });
  expect(response.ok(), `${path}: ${response.status()} ${await response.text()}`).toBeTruthy();
  return response.json();
}

test("the demo: version 1 to released, then version 2 with drift, code version 2 and its diff", async ({ page, request }) => {
  await upload(page);
  await walk(page, undefined, hooks);

  // the feed page: code version 1 (T3)
  await page.goto(`${BACKEND}#/feeds`);
  await page.getByTestId(`feed-link-${FEED_CODE}`).click();
  const code = page.getByTestId("section-code");
  await expect(code.getByTestId("code-meta")).toContainText("Version 1, approved by dev-operator", { timeout: 30_000 });
  await expect(code.getByRole("button", { name: "curated.sql" })).toBeVisible();
  const feedId = page.url().split("/feed/")[1];

  // version 2: store the file, then Load now
  await page.getByTestId("reupload-file").setInputFiles(V2);
  await expect(page.getByText("Press Load now to load it.")).toBeVisible({ timeout: 30_000 });
  await page.getByTestId("load-now").click();
  const drift = page.getByTestId("drift");
  await expect(drift.getByTestId("drift-report")).toContainText("New columns: BEST_FCF", { timeout: 60_000 });
  await expect(drift.getByTestId("drift-report")).toContainText("information only");
  const newColumn = drift.getByTestId("drift-actions").locator("li").filter({ hasText: "map new column" });
  await expect(newColumn).toContainText("Agent applies, proposed by rules");
  await newColumn.getByRole("button", { name: "Select" }).click();
  await expect(newColumn).toContainText("selected");

  // the update: the glossary builder is asked about BEST_FCF, then mapping, then a new go-live (API)
  let glossary: { id: string }[] = [];
  await expect.poll(async () => {
    glossary = (await json(request, `/api/amaze/proposals?kind=glossary_element&status=pending&feed_id=${feedId}`))
      .filter((p: { column: string }) => p.column === "BEST_FCF");
    return glossary.length;
  }, { timeout: 90_000 }).toBeGreaterThan(0);
  await json(request, `/api/amaze/proposals/${glossary[0].id}/decision`, { method: "POST", data: { decision: "approved", choice: 0 } });
  await json(request, `/api/amaze/feeds/${feedId}/mappings/propose`, { method: "POST", data: { column: "BEST_FCF" } });
  const mapping = (await json(request, `/api/amaze/proposals?kind=mapping&status=pending&feed_id=${feedId}`))
    .find((p: { column: string }) => p.column === "BEST_FCF");
  expect(mapping, "a mapping proposal for BEST_FCF").toBeTruthy();
  await json(request, `/api/amaze/proposals/${mapping.id}/decision`, { method: "POST",
    data: { decision: "approved", choice: mapping.payload.recommended ?? mapping.payload.candidates[0] } });
  const summary = await json(request, `/api/amaze/feeds/${feedId}/go-live/summary`, { method: "POST" });
  expect(summary.proposal, "the go-live summary waits for the owner").toBeTruthy();
  await json(request, `/api/amaze/proposals/${summary.proposal.id}/decision`, { method: "POST", data: { decision: "approved" } });

  // code version 2 and its diff from version 1 (T3, T4)
  await page.reload();
  const code2 = page.getByTestId("section-code");
  await expect(code2.getByTestId("code-meta")).toContainText("Version 2", { timeout: 30_000 });
  await code2.getByLabel("Compare with").selectOption("1");
  const diff = code2.getByTestId("code-diff");
  await expect(diff).toContainText("curated.sql");
  await expect(diff).toContainText("best_fcf");

  // T5: the overview, Daily and Monthly
  await page.goto(`${BACKEND}#/overview`);
  await page.getByRole("button", { name: "Monthly" }).click();
  await expect(page.getByTestId("overview-drift")).toContainText(FEED_CODE, { timeout: 30_000 });
  await expect(page.getByTestId("overview-actions")).toContainText("applied by agents");
  await page.getByRole("button", { name: "Daily" }).click();
  await expect(page.getByTestId("overview-drift")).toContainText(FEED_CODE);
});

// keeps the unused import honest for tooling that lists spec files
export const specFile = fileURLToPath(import.meta.url);
