import { copyFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { expect, test, type Page } from "@playwright/test";

// The PoC story, end to end, on amaze_test / e2e_story (phase 7 plan, section 8, rows E1 to E9): a request no
// product covers, source picking, onboarding, one glossary and one mapping approval, rules, go-live, a released
// load, "Ready", then a delivery with a column missing, which is held and told to people.

const BACKEND = "http://127.0.0.1:5173/";
const CONSUMER = "http://127.0.0.1:5174/market/";
const REPO = fileURLToPath(new URL("../../", import.meta.url));

const RILEY = ["dev-requester", "Riley Requester"] as const;
const BLAKE = ["dev-backend-team", "Blake Backend"] as const;
const GLENN = ["dev-glossary-owner", "Glenn Glossary"] as const;
const SAM = ["dev-data-steward", "Sam Steward"] as const;
const CASEY = ["dev-checker", "Casey Checker"] as const;
const DEVON = ["dev-data-owner", "Devon Owner"] as const;
const OPAL = ["dev-operations", "Opal Operations"] as const;

const RAISED = "Requirement raised; the backend team will look for a source.";
const ONBOARDING = "Your data is getting onboarded; we will notify you once it is ready.";
const SOURCE = "Bond terms";
const FEED = "syn_bond_terms";

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

test("the PoC story: request, onboard, release, Ready, then a delivery that is held", async ({ browser }) => {
  const consumer = await (await browser.newContext()).newPage();
  const backend = await (await browser.newContext()).newPage();

  // ---- E1 Riley: search "coupon rate", Request this data, find a source, confirm the scope, pick Bond terms
  await consumer.goto(`${CONSUMER}#/search`);
  await signIn(consumer, RILEY);
  await consumer.getByTestId("search-box").fill("coupon rate");
  await consumer.getByRole("button", { name: "Search", exact: true }).click();
  await expect(consumer.getByTestId("no-hits")).toBeVisible(); // no product holds coupon_rate: not a reuse
  await consumer.getByTestId("submit").click();
  await expect(consumer.getByTestId("result-message")).toHaveText(RAISED);
  await expect(consumer.getByTestId("find-source-message")).toBeVisible();
  await consumer.getByRole("link", { name: "See this requirement" }).click();
  await consumer.getByTestId("confirm-scope").click(); // lists the sources right away
  await expect(consumer.getByTestId(`candidate-${FEED}`)).toBeVisible();
  await consumer.getByTestId(`accept-${FEED}`).check();
  await consumer.getByTestId(`pick-${FEED}`).click();
  await expect(consumer.getByTestId("requirement-status")).toHaveText("onboarding");
  const requirementHref = new URL(consumer.url()).hash; // #/requirement/<id>
  await consumer.reload();
  await expect(consumer.getByTestId("requirement-message")).toHaveText(ONBOARDING);
  await expect(consumer.getByTestId("progress")).toHaveText(
    `The backend team is checking the first file from ${SOURCE}.`);

  // ---- E2 Blake: profile, summarise, sign off "meets", register (owner Devon, open)
  await backend.goto(`${BACKEND}#/feeds`);
  await signIn(backend, BLAKE);
  await openFeed(backend);
  await backend.getByTestId("profile").click();
  await backend.getByTestId("summarise").click();
  await backend.getByTestId("outcome-meets").check();
  await backend.getByTestId("signoff").click();
  await backend.getByTestId("owner").selectOption(DEVON[0]);
  await backend.getByTestId("register").click();
  await expect(backend.getByTestId("registration")).toContainText("open");
  await consumer.reload();
  await expect(consumer.getByTestId("progress")).toHaveText(
    `${SOURCE} is registered; the backend team is setting up how its data is read and checked.`);

  // ---- E3 the glossary run finishes in the background: the banner says proposals wait (at most 60 s)
  await expect(backend.getByTestId("run-banner-waiting")).toBeVisible({ timeout: 60_000 });
  await expect(backend.getByTestId("run-banner-active")).toHaveCount(0, { timeout: 60_000 });

  // ---- E4 Glenn approves one glossary proposal
  await signIn(backend, GLENN);
  await backend.goto(`${BACKEND}#/glossary`);
  const approvable = backend.getByTestId("glossary-proposal").filter({
    has: backend.locator('[data-testid="approve"]:not([disabled])'),
  });
  await approvable.first().getByTestId("approve").click();
  await expect(backend.getByTestId("glossary-decided")).toContainText(`Approved for ${FEED}.`);

  // ---- E5 Sam proposes the mappings and approves instr_id, matures_on and cpn
  await signIn(backend, SAM);
  await openFeed(backend);
  await backend.getByTestId("propose-mappings").click();
  for (const column of ["instr_id", "matures_on", "cpn"]) {
    const row = backend.locator(`[data-testid="mapping-row"][data-column="${column}"]`);
    await row.getByTestId("approve-mapping").click();
    await expect(row).toContainText("version 1");
  }

  // ---- E6 Sam proposes rules and approves one; Casey approves it: it becomes a rule
  await backend.getByTestId("propose-rules").click();
  const pending = backend.getByTestId("pending-rule");
  await expect(pending.first()).toBeVisible();
  const ruleKey = (await pending.first().getAttribute("data-rule")) as string;
  const rule = (page: Page) => page.locator(`[data-testid="pending-rule"][data-rule="${ruleKey}"]`);
  await rule(backend).getByTestId("approve-rule").click();
  await signIn(backend, CASEY);
  await openFeed(backend);
  await rule(backend).getByTestId("approve-rule").click();
  await expect(backend.locator(`[data-testid="rule-row"][data-rule="${ruleKey}"]`)).toBeVisible();

  // ---- E7 Blake prepares the go-live; Devon approves it
  await signIn(backend, BLAKE);
  await openFeed(backend);
  await backend.getByTestId("prepare-go-live").click();
  await expect(backend.getByTestId("go-live-summary")).toBeVisible();
  await signIn(backend, DEVON);
  await openFeed(backend);
  await backend.getByTestId("approve-go-live").click();
  await expect(backend.getByTestId("go-live-latest")).toContainText("Version 1");
  await openRequirement(consumer, requirementHref);
  await expect(consumer.getByTestId("progress")).toHaveText(
    `The data owner approved ${SOURCE} for loading; the first delivery is being checked.`);

  // ---- E8 Opal: Load now. Arrival on time, four stages, trust 100.0, released; the product; Riley is Ready
  await signIn(backend, OPAL);
  await openFeed(backend);
  await backend.getByTestId("load-now").click();
  await expect(backend.getByTestId("feed-done")).toHaveText("Loaded: released.");
  const first = backend.locator('[data-testid="load-row"][data-purpose="scheduled"]').first();
  await expect(first).toContainText("on time complete");
  await expect(first).toContainText("trust 100.0");
  await expect(first).toContainText("released");
  await expect(first.getByTestId("stages")).toContainText("raw 10→10 · curated 10→10 · validated 10→10 · standard 10→10");
  await expect(backend.getByTestId("product")).toContainText(SOURCE);

  await openRequirement(consumer, requirementHref);
  await expect(consumer.getByTestId("requirement-status")).toHaveText("ready");
  await expect(consumer.getByTestId("requirement-message")).toHaveText(`Ready. Your data is in ${SOURCE}.`);
  await consumer.goto(`${CONSUMER}#/notifications`);
  await expect(consumer.getByTestId("notification").first()).toContainText("Ready: coupon rate");
  await consumer.goto(`${CONSUMER}#/search`);
  await consumer.getByTestId("search-box").fill("coupon rate");
  await consumer.getByRole("button", { name: "Search", exact: true }).click();
  await expect(consumer.getByTestId(`hit-${FEED}`)).toContainText("Available"); // a reuse of Bond terms

  // ---- E9 the next delivery lacks a column: held, nothing loaded, people told, Riley still Ready
  copyFileSync(`${REPO}fixtures/e2e/syn_bond_terms_breaking.csv`, `${REPO}data/e2e/sources/files/bond_terms.csv`);
  await backend.getByTestId("load-now").click();
  await expect(backend.getByTestId("feed-done")).toHaveText("Loaded: held.");
  const held = backend.locator('[data-testid="load-row"][data-purpose="scheduled"]').first();
  await expect(held).toContainText("breaking held");
  await expect(held).toContainText("cpn");
  await expect(held.getByTestId("stages")).toHaveText("");
  await backend.goto(`${BACKEND}#/notifications`);
  await expect(backend.getByTestId("notification").first()).toContainText(`Delivery held: ${FEED}`);
  await signIn(backend, BLAKE);
  await expect(backend.getByTestId("notification").first()).toContainText(`Delivery held: ${FEED}`);

  await openRequirement(consumer, requirementHref);
  await expect(consumer.getByTestId("requirement-message")).toHaveText(`Ready. Your data is in ${SOURCE}.`);
  await expect(consumer.getByTestId("held-note")).toContainText("was held");
  await consumer.goto(`${CONSUMER}#/notifications`);
  await expect(consumer.getByTestId("notification").first()).toContainText(`Delivery held: ${FEED}`);
});
