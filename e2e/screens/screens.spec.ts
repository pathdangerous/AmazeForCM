import { copyFileSync, mkdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { expect, test, type Locator, type Page } from "@playwright/test";

// The screens of phase 7.5 (plan, section 8): the story's path, rows E1 to E9 of phase 7, on amaze_test /
// e2e_story, with shots S1 to S25 taken at the points the plan names. It repeats the story's steps (plan risk 6:
// a later change to e2e/tests/poc_story.spec.ts needs the same change here) and checks only what makes a shot
// ready; the story is the test of behaviour.

const BACKEND = "http://127.0.0.1:5173/";
const CONSUMER = "http://127.0.0.1:5174/market/";
const REPO = fileURLToPath(new URL("../../", import.meta.url));
const SET = process.env.SHOTS_SET ?? "before";
const ONLY = process.env.SHOTS_ONLY ? new Set(process.env.SHOTS_ONLY.split(",").map((s) => s.trim())) : null;
const DIR = `${REPO}docs/phase75-screenshots/${SET}/`;
// SHOTS_SCHEME=dark (checkpoint 2, A4): the shots in the dark scheme, each file named <id>-dark.png
const SCHEME = process.env.SHOTS_SCHEME === "dark" ? "dark" as const : "light" as const;
const CONTEXT = { viewport: { width: 1280, height: 900 }, colorScheme: SCHEME };

const RILEY = ["dev-requester", "Riley Requester"] as const;
const BLAKE = ["dev-backend-team", "Blake Backend"] as const;
const GLENN = ["dev-glossary-owner", "Glenn Glossary"] as const;
const SAM = ["dev-data-steward", "Sam Steward"] as const;
const CASEY = ["dev-checker", "Casey Checker"] as const;
const DEVON = ["dev-data-owner", "Devon Owner"] as const;
const OPAL = ["dev-operations", "Opal Operations"] as const;
const FEED = "syn_bond_terms";

const written: string[] = [];

// A shot: the whole page, one element, or the band from the top of `from` to the bottom of `to`.
async function shot(page: Page, name: string, from?: Locator, to?: Locator) {
  if (ONLY && !ONLY.has(name.split("-")[0])) return;
  const path = SCHEME === "dark" ? `${DIR}${name.split("-")[0]}-dark.png` : `${DIR}${name}.png`;
  await page.waitForLoadState("networkidle");
  if (!from) {
    await page.screenshot({ path, fullPage: true, animations: "disabled" });
  } else if (!to) {
    await from.screenshot({ path, animations: "disabled" });
  } else {
    await from.scrollIntoViewIfNeeded();
    const [scrollX, scrollY] = await page.evaluate(() => [window.scrollX, window.scrollY]);
    const a = (await from.boundingBox())!;
    const b = (await to.boundingBox())!;
    const x = Math.max(0, Math.min(a.x, b.x) + scrollX - 8);
    const y = Math.max(0, a.y + scrollY - 8);
    const right = Math.max(a.x + a.width, b.x + b.width) + scrollX + 8;
    const bottom = b.y + b.height + scrollY + 8;
    await page.screenshot({ path, fullPage: true, animations: "disabled",
                            clip: { x, y, width: right - x, height: bottom - y } });
  }
  written.push(name);
}

async function signIn(page: Page, [id, name]: readonly [string, string]) {
  await page.getByTestId("dev-user").selectOption(id);
  await expect(page.getByTestId("signed-in-as")).toContainText(name);
}

async function openFeed(page: Page) {
  await page.goto(`${BACKEND}#/feeds`);
  await page.getByTestId(`feed-link-${FEED}`).click();
  await expect(page.getByTestId("feed-view")).toBeVisible();
}

async function openRequirement(page: Page, href: string) {
  await page.goto(CONSUMER + href);
  await page.reload();
  await expect(page.getByTestId("requirement-status")).toBeVisible();
}

test("the phase 7.5 screens, S1 to S25", async ({ browser }) => {
  mkdirSync(DIR, { recursive: true });
  const consumer = await (await browser.newContext(CONTEXT)).newPage();
  const backend = await (await browser.newContext(CONTEXT)).newPage();
  const section = (name: string) => backend.getByTestId(`section-${name}`);

  // ---- start: the empty registry
  await backend.goto(`${BACKEND}#/feeds`);
  await signIn(backend, BLAKE);
  await expect(backend.getByTestId("feeds-empty")).toBeVisible();
  await shot(backend, "S1-registry-empty");

  // ---- E1 Riley
  await consumer.goto(`${CONSUMER}#/search`);
  await signIn(consumer, RILEY);
  await consumer.getByTestId("search-box").fill("coupon rate");
  await consumer.getByRole("button", { name: "Search", exact: true }).click();
  await expect(consumer.getByTestId("no-hits")).toBeVisible();
  await shot(consumer, "S2-search-no-hits");
  await consumer.getByTestId("submit").click();
  await expect(consumer.getByTestId("find-source-message")).toBeVisible();
  await shot(consumer, "S3-search-raised");
  await consumer.getByRole("link", { name: "See this requirement" }).click();
  await expect(consumer.getByTestId("confirm-scope")).toBeVisible();
  await shot(consumer, "S4-requirement-scope");
  await consumer.getByTestId("confirm-scope").click();
  await expect(consumer.getByTestId(`candidate-${FEED}`)).toBeVisible();
  await shot(consumer, "S5-requirement-sources");
  await consumer.getByTestId(`accept-${FEED}`).check();
  await consumer.getByTestId(`pick-${FEED}`).click();
  await expect(consumer.getByTestId("requirement-status")).toHaveText("onboarding");
  const requirementHref = new URL(consumer.url()).hash;
  await consumer.reload();
  await expect(consumer.getByTestId("progress")).toBeVisible();
  await shot(consumer, "S6-requirement-checking-file");

  await backend.reload();
  await expect(backend.getByTestId(`feed-link-${FEED}`)).toBeVisible();
  await shot(backend, "S7-registry-candidate");

  // ---- E2 Blake
  await openFeed(backend);
  await backend.getByTestId("profile").click();
  await expect(section("profile")).toBeVisible();
  await shot(backend, "S8-feed-source-to-profile", section("source"), section("profile"));
  await backend.getByTestId("summarise").click();
  await expect(backend.getByTestId("signoff-form")).toBeVisible();
  await shot(backend, "S9-feed-summary-signoff", section("summary"), section("signoff"));
  await backend.getByTestId("outcome-meets").check();
  await backend.getByTestId("signoff").click();
  await backend.getByTestId("owner").selectOption(DEVON[0]);
  await backend.getByTestId("register").click();
  await expect(backend.getByTestId("registration")).toContainText("open");
  await shot(backend, "S10-feed-registration", section("registration"));
  await consumer.reload();
  await expect(consumer.getByTestId("progress")).toContainText("is registered");
  await shot(consumer, "S11-requirement-setting-up");

  // ---- E3 the glossary run; E4 Glenn
  await expect(backend.getByTestId("run-banner-waiting")).toBeVisible({ timeout: 60_000 });
  await expect(backend.getByTestId("run-banner-active")).toHaveCount(0, { timeout: 60_000 });
  await signIn(backend, GLENN);
  await backend.goto(`${BACKEND}#/glossary`);
  await expect(backend.getByTestId("glossary-proposal").first()).toBeVisible();
  await shot(backend, "S12-glossary-pending");
  const approvable = backend.getByTestId("glossary-proposal").filter({
    has: backend.locator('[data-testid="approve"]:not([disabled])'),
  });
  await approvable.first().getByTestId("approve").click();
  await expect(backend.getByTestId("glossary-decided")).toContainText(`Approved for ${FEED}.`);
  const decidedFilter = backend.getByRole("button", { name: "Decided", exact: true });
  if (await decidedFilter.count()) { // phase 7.5, P16 (task 12): the Decided list; absent in the before set
    await decidedFilter.click();
    await expect(backend.getByTestId("glossary-decided-row").first()).toBeVisible();
  }
  // checkpoint 2, item 2: with the longest dev-user label (Glenn Glossary (glossary_owner)) the header is one line
  const header = (await backend.locator(".app-header-inner").boundingBox())!;
  expect(header.height, "the backend header stays on one line at 1280 px").toBeLessThanOrEqual(60);
  const label = (await backend.locator('label[for="dev-user"]').boundingBox())!;
  expect(label.height, "the Dev user label stays on one line").toBeLessThanOrEqual(24);
  await shot(backend, "S13-glossary-decided");

  // ---- E5 Sam: Propose mappings (shot), then the three approvals
  await signIn(backend, SAM);
  await openFeed(backend);
  await backend.getByTestId("propose-mappings").click();
  await expect(backend.getByTestId("feed-done")).toBeVisible();
  await shot(backend, "S14-feed-builder-mapping", section("glossary-builder"), section("mapping"));
  for (const column of ["instr_id", "matures_on", "cpn"]) {
    const row = backend.locator(`[data-testid="mapping-row"][data-column="${column}"]`);
    await row.getByTestId("approve-mapping").click();
    await expect(row).toContainText("version 1");
  }

  // ---- E6 rules
  await backend.getByTestId("propose-rules").click();
  const pending = backend.getByTestId("pending-rule");
  await expect(pending.first()).toBeVisible();
  await shot(backend, "S15-feed-rules", section("rules"));
  const ruleKey = (await pending.first().getAttribute("data-rule")) as string;
  const rule = (page: Page) => page.locator(`[data-testid="pending-rule"][data-rule="${ruleKey}"]`);
  await rule(backend).getByTestId("approve-rule").click();
  await signIn(backend, CASEY);
  await openFeed(backend);
  await rule(backend).getByTestId("approve-rule").click();
  await expect(backend.locator(`[data-testid="rule-row"][data-rule="${ruleKey}"]`)).toBeVisible();

  // ---- E7 go-live
  await signIn(backend, BLAKE);
  await openFeed(backend);
  await backend.getByTestId("prepare-go-live").click();
  await expect(backend.getByTestId("go-live-summary")).toBeVisible();
  // 7.17: the backend team runs the code pipeline first
  await backend.getByTestId("go-live-summary").getByRole("button", { name: "Run pipeline" }).click();
  await expect(backend.getByTestId("go-live-summary").getByTestId("pipeline-panel")).toContainText("Latest result: Passed", { timeout: 120_000 });
  await shot(backend, "S16-feed-go-live", section("go-live"));
  await signIn(backend, DEVON);
  await openFeed(backend);
  await backend.getByTestId("approve-go-live").click();
  await expect(backend.getByTestId("go-live-latest")).toContainText("Version 1");
  await openRequirement(consumer, requirementHref);
  await expect(consumer.getByTestId("progress")).toContainText("approved");
  await shot(consumer, "S17-requirement-approved");

  // ---- E8 Opal: Load now, released; Riley Ready
  await signIn(backend, OPAL);
  await openFeed(backend);
  await backend.getByTestId("load-now").click();
  await expect(backend.getByTestId("feed-done")).toHaveText("Loaded: released.");
  await expect(backend.getByTestId("product")).toBeVisible();
  await shot(backend, "S18-feed-loads-product", backend.getByTestId("product"), section("loads"));
  await openRequirement(consumer, requirementHref);
  await expect(consumer.getByTestId("requirement-status")).toHaveText("ready");
  await shot(consumer, "S19-requirement-ready");
  await consumer.goto(`${CONSUMER}#/search`);
  await consumer.getByTestId("search-box").fill("coupon rate");
  await consumer.getByRole("button", { name: "Search", exact: true }).click();
  await expect(consumer.getByTestId(`hit-${FEED}`)).toContainText("Available");
  await shot(consumer, "S20-search-reuse");

  // ---- E9 the breaking delivery: held
  copyFileSync(`${REPO}fixtures/e2e/syn_bond_terms_breaking.csv`, `${REPO}data/e2e/sources/files/bond_terms.csv`);
  await backend.getByTestId("load-now").click();
  await expect(backend.getByTestId("feed-done")).toHaveText("Loaded: held.");
  await shot(backend, "S21-feed-loads-held", section("loads"));
  await backend.goto(`${BACKEND}#/notifications`);
  await expect(backend.getByTestId("notification").first()).toContainText(`Delivery held: ${FEED}`);
  await shot(backend, "S22-notifications-backend");
  await openRequirement(consumer, requirementHref);
  await expect(consumer.getByTestId("held-note")).toBeVisible();
  await shot(consumer, "S23-requirement-ready-held");
  await consumer.goto(`${CONSUMER}#/notifications`);
  await expect(consumer.getByTestId("notification").first()).toContainText(`Delivery held: ${FEED}`);
  await shot(consumer, "S24-notifications-consumer");

  // ---- end: Status
  await backend.goto(`${BACKEND}#/status`);
  await expect(backend.getByTestId("db-status")).toContainText("ok");
  await shot(backend, "S25-status");

  console.log(`screens: ${written.length} files in docs/phase75-screenshots/${SET}/: ${written.join(", ")}`);
  expect(written.length).toBe(ONLY ? ONLY.size : 25);
});
