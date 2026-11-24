import { expect, test, type Page } from "@playwright/test";
import { fileURLToPath } from "node:url";
import { BACKEND, FEED_CODE, REPO, upload, walk, type StepHooks } from "../operator_flow";

// The demo (docs/demo.md), as one story. Phase 7.13: version 1 is onboarded with Run all agents (the profile approval is
// the one click, then the chain review: every step drafted, "based on" links, Approve all confident on the glossary).
// Phase 7.12, as before: upload version 1, profile and actions, a rule added
// by hand, one agent rule edited and one rejected (T7), the completion report, code version 1, approve and release; then
// version 2 (a new column and two columns swapped): drift, its actions, the update, code version 2 and the diff, and the
// overview. Mock provider, one operator who holds every role (REQUIRE_FOUR_EYES=false), schema e2e_story.
//   cd e2e; npx playwright test -c playwright.demo.config.ts
// Every step is a click on a screen: the update after "Load now" on version 2 uses the Glossary tab (the element), the feed
// page's Mapping section (the mapping) and its Go live section (prepare and approve), as the operator.

const V2 = `${REPO}fixtures/sources/Synthetic_Security_Master_v2.xlsx`;

async function reject(page: Page, card: ReturnType<Page["locator"]>, why: string) {
  await card.getByRole("button", { name: "Reject", exact: true }).click();
  await card.getByLabel("Why it is rejected").fill(why);
  await card.getByRole("button", { name: "Confirm", exact: true }).click();
}

const hooks: StepHooks = {
  // 7.13: after the one profile click every agent has drafted ahead; review the chain, approve the confident glossary
  "step-glossary": async (page) => {
    const chain = page.getByTestId("chain-review");
    await expect(chain).toBeVisible();
    for (const step of ["mapping", "transformations", "rules"]) {
      await expect(chain.getByTestId(`chain-step-${step}`)).toContainText(/ready|needs decision/, { timeout: 180_000 });
    }
    await expect(chain.getByTestId("chain-step-mapping").getByRole("button", { name: /^Based on Glossary/ }).first())
      .toBeVisible();
    const confident = chain.getByTestId("chain-approve-confident-glossary");
    if (await confident.isEnabled()) {
      await confident.click();
      await expect(chain.getByRole("status")).toContainText("Approved", { timeout: 60_000 });
    }
  },
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

test("the demo: version 1 to released, then version 2 with drift, code version 2 and its diff", async ({ page }) => {
  await upload(page);
  // 7.13: Run all agents, once; the profile approval is then the one click before the chain review
  await page.getByTestId("workspace-run-all").click();
  await expect(page.getByTestId("chain-review")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("workspace-run-all")).toBeHidden();
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

  // the type change and the rule that flags more rows are in the report too, each with its action
  await expect(drift.getByTestId("drift-report")).toContainText("BEST_DPS DOUBLE to VARCHAR");
  await expect(drift.getByTestId("drift-report")).toContainText("best_sales_not_negative");
  await expect(drift.getByTestId("drift-actions").locator("li").filter({ hasText: "accept type change" })).toBeVisible();
  await expect(drift.getByTestId("drift-actions").locator("li").filter({ hasText: "review rule" })).toBeVisible();

  // the update, all on screen: the glossary owner approves BEST_FCF's element (Glossary tab) ...
  await page.goto(`${BACKEND}#/glossary`);
  const element = page.locator('[data-testid="glossary-proposal"][data-column="BEST_FCF"]');
  await expect(element).toBeVisible({ timeout: 90_000 }); // the glossary builder runs in the background
  await element.getByRole("radio").first().check();
  await element.getByTestId("approve").click();
  await expect(page.getByTestId("glossary-decided")).toBeVisible();

  // ... the steward approves its mapping (feed page, Mapping) ...
  await page.goto(`${BACKEND}#/feed/${feedId}`);
  await page.getByTestId("propose-mappings").click();
  const mapping = page.locator('[data-testid="mapping-row"][data-column="BEST_FCF"]');
  await expect(mapping.getByTestId("pending-mapping")).toBeVisible({ timeout: 60_000 });
  await mapping.getByRole("radio").first().check();
  await mapping.getByTestId("approve-mapping").click();
  await expect(mapping.getByTestId("pending-mapping")).toBeHidden({ timeout: 60_000 });

  // ... and the owner prepares and approves the new go-live (feed page, Go live)
  await page.getByTestId("prepare-go-live").click();
  await expect(page.getByTestId("go-live-summary")).toBeVisible({ timeout: 120_000 });
  await page.getByTestId("approve-go-live").click();
  await expect(page.getByTestId("go-live-latest")).toContainText("2", { timeout: 60_000 });

  // code version 2 and its diff from version 1 (T3, T4)
  await page.reload();
  const code2 = page.getByTestId("section-code");
  await expect(code2.getByTestId("code-meta")).toContainText("Version 2", { timeout: 30_000 });
  await code2.getByLabel("Compare with").selectOption("1");
  const diff = code2.getByTestId("code-diff");
  await expect(diff).toContainText("curated.sql");
  await expect(diff).toContainText("best_fcf");

  // 7.14: version 2 was held (negative BEST_SALES). Exceptions: groups of flagged rows by rule and value pattern; the
  // agent proposes one fix per group; the operator (steward and registered owner) approves the selected ones in bulk;
  // the held file is rerun under the current configuration, as a new, recorded load (the held one stays held)
  await page.goto(`${BACKEND}#/feed/${feedId}`);
  const exceptions = page.getByTestId("section-exceptions");
  await expect(exceptions).toBeVisible({ timeout: 30_000 });
  await expect(exceptions.getByTestId("exceptions-summary")).toContainText("The load is held");
  await exceptions.getByRole("button", { name: "Propose fixes" }).click();
  await expect(exceptions.getByTestId("exceptions-message")).toContainText("Nothing changes until people decide them", { timeout: 60_000 });
  await expect(exceptions.getByTestId("exception-fix").first()).toBeVisible();
  const boxes = exceptions.locator('input[type="checkbox"]');
  for (let i = 0, n = await boxes.count(); i < n; i++) await boxes.nth(i).check();
  await exceptions.getByRole("button", { name: "Approve selected" }).click();
  await expect(exceptions.getByTestId("exceptions-message")).toContainText("Approved for", { timeout: 60_000 });
  await exceptions.getByLabel("Reason for the rerun").fill("The fixes are approved.");
  await exceptions.getByRole("button", { name: "Rerun this load" }).click();
  await expect(exceptions.getByTestId("rerun-result")).toBeVisible({ timeout: 120_000 });
  await expect(exceptions.getByTestId("rerun-result")).toContainText("Rerun released: trust 97.1 (it was 88.6); 6 rows waived");
  await page.reload();
  await expect(page.getByTestId("section-loads").getByTestId("rerun-of").first()).toContainText("Rerun of load", { timeout: 30_000 });

  // T5: the overview, Daily and Monthly
  await page.goto(`${BACKEND}#/overview`);
  await page.getByRole("button", { name: "Monthly" }).click();
  await expect(page.getByTestId("overview-drift")).toContainText(FEED_CODE, { timeout: 30_000 });
  await expect(page.getByTestId("overview-actions")).toContainText("applied by agents");
  await expect(page.getByTestId("overview-exceptions")).toContainText("resolved by agent proposals"); // 7.14
  await expect(page.getByTestId("overview-exceptions")).toContainText("Reruns:");
  await page.getByRole("button", { name: "Daily" }).click();
  await expect(page.getByTestId("overview-drift")).toContainText(FEED_CODE);
});

// keeps the unused import honest for tooling that lists spec files
export const specFile = fileURLToPath(import.meta.url);
