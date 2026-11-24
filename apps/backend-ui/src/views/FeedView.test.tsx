import { MantineProvider } from "@mantine/core";
import { cleanup, screen, waitFor, within } from "@testing-library/react";
import { createElement } from "react";
import { afterEach, expect, test, vi } from "vitest";
import { fireEvent } from "@testing-library/react";
import { cssVariablesResolver, theme } from "../theme";
import {
  FEED_ID, feedPage, feedRoutes, PEOPLE, renderWithTheme, REQUIREMENT_ID, scheduledLoad, stubFetch, USERS,
} from "../test/fixtures";
import FeedView from "./FeedView";

const NAMES = Object.fromEntries(PEOPLE.map((p) => [p.id, p.display_name]));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const SECTIONS = ["source", "requirements", "file", "profile", "summary", "registration", "glossary-builder", "mapping",
                  "rules", "go-live", "loads", "history"];

function show(page = feedPage(), props: Partial<Parameters<typeof FeedView>[0]> = {}, user = USERS[1],
              extra: Record<string, unknown> = {}) {
  const fetchStub = stubFetch({ ...feedRoutes(page, user), ...extra });
  renderWithTheme(
    <FeedView feedId={FEED_ID} devUser={user.id} me={user} people={NAMES} canAct={false} canRun={false}
              canMap={true} changed={0} onChange={() => undefined} {...props} />);
  return fetchStub;
}

const candidate = (over: Partial<ReturnType<typeof feedPage>> = {}) => feedPage({
  feed: { ...feedPage().feed, stage: "candidate" }, registration: null, signoffs: [], summary: null,
  summary_status: null, next_action: "summarise", columns: [], ...over });

test("task 8: a registered feed page renders every section with a converted heading, rows keep data-column", async () => {
  show(feedPage({ loads: [scheduledLoad()] }));
  await screen.findByTestId("feed-view");
  for (const name of SECTIONS) {
    const heading = screen.getByTestId(`section-${name}`).querySelector("h3");
    expect(heading?.className, name).toContain("mantine-Title");
  }
  const row = document.querySelector('[data-testid="mapping-row"][data-column="qty"]') as HTMLElement;
  expect(row.tagName).toBe("TR");
  expect(within(row).getByTestId("approve-mapping").tagName).toBe("BUTTON");
  const load = document.querySelector('[data-testid="load-row"][data-purpose="scheduled"]') as HTMLElement;
  expect(load.textContent).toContain("on time complete");
  expect(load.textContent).toContain("trust 100.0");
  expect(within(load).getByTestId("stages").textContent).toBe(
    "raw 10→10 · curated 10→10 · validated 10→10 · standard 10→10");
});

test("a new load re-reads the drift section even when the page already shows its maximum number of loads", async () => {
  // the page lists at most 10 loads, so their count stops changing; the newest load's id is what changes
  const loadsStartingAt = (first: string) => [scheduledLoad({ id: first }), ...Array.from({ length: 9 }, (_, n) => scheduledLoad({ id: `old-${n}` }))];
  const driftUrl = `GET /api/amaze/feeds/${FEED_ID}/drift`;
  const empty = { load_id: null, report: { new_columns: [], missing_columns: [], type_changes: [], order_changed: false, rule_rises: [] }, actions: [] };
  const fetchStub = stubFetch({ ...feedRoutes(feedPage({ loads: loadsStartingAt("a") }), USERS[1]), [driftUrl]: empty });
  const feed = (changed: number) => (
    <FeedView feedId={FEED_ID} devUser={USERS[1].id} me={USERS[1]} people={NAMES} canAct={false} canRun={false} canMap={true}
              changed={changed} onChange={() => undefined} />);
  const view = renderWithTheme(feed(0)); // also installs the browser stubs Mantine needs
  await screen.findByTestId("feed-view");
  const drifts = () => fetchStub.mock.calls.filter(([u]) => String(u).endsWith("/drift")).length;
  await waitFor(() => expect(drifts()).toBe(1));
  stubFetch({ ...feedRoutes(feedPage({ loads: loadsStartingAt("b") }), USERS[1]), [driftUrl]: empty });
  const second = vi.mocked(fetch);
  view.rerender(createElement(MantineProvider, { theme, cssVariablesResolver, children: feed(1) }));
  await waitFor(() => expect(second.mock.calls.some(([u]) => String(u).endsWith("/drift"))).toBe(true));
});

test("task 8: the sign-off and register forms keep native radios, a native select and the disabled attribute",
  async () => {
    const candidate = feedPage({ feed: { ...feedPage().feed, stage: "candidate" }, registration: null, signoffs: [],
                                 summary_status: "pending", next_action: "sign_off", columns: [] });
    show(candidate, { canAct: true, canMap: false }, USERS[0]);
    const meets = await screen.findByTestId("outcome-meets");
    expect((meets as HTMLInputElement).type).toBe("radio");
    expect(screen.getByTestId("signoff").hasAttribute("disabled")).toBe(true); // no outcome chosen yet
    cleanup();
    show({ ...candidate, next_action: "register" }, { canAct: true, canMap: false }, USERS[0]);
    expect((await screen.findByTestId("owner")).tagName).toBe("SELECT");
  });

test("checkpoint 2: the stage badge in the title is a span (a div may not sit in an h2)", async () => {
  show();
  const title = (await screen.findByTestId("feed-view")).querySelector("h2") as HTMLElement;
  expect(title.querySelector("div")).toBeNull();
});

test("P6: Profile is offered only while the latest file has no profile", async () => {
  show(candidate(), { canAct: true }, USERS[0]); // the fixture's profile is of the latest file
  const file = await screen.findByTestId("section-file");
  expect(within(file).queryByTestId("profile")).toBeNull();
  expect(file.textContent).toContain("Profile 1 is of this file.");
  cleanup();
  show(candidate({ last_file: { ...feedPage().last_file!, id: "fetch-2" } }), { canAct: true }, USERS[0]);
  expect(await screen.findByTestId("profile")).toBeTruthy();
});

test("P7 and P8: each requirement says when and by whom it was raised; people show by name", async () => {
  show(feedPage({ product: { code: "syn_test_feed", name: "Test feed", visibility: "open", owner_id: "dev-data-owner",
                             created_at: "2026-11-18T09:00:00Z" } }));
  const requirements = await screen.findByTestId("feed-requirements");
  expect(requirements.textContent).toMatch(/raised .+ by Riley Requester/);
  const registration = screen.getByTestId("registration");
  expect(registration.textContent).toContain("Devon Owner");
  expect(registration.textContent).toContain("Blake Backend");
  expect(registration.textContent).not.toContain("dev-data-owner");
  expect(screen.getByTestId("product").textContent).toContain("owned by Devon Owner");
});

test("P9: the result line shows under the heading of the next section, and only once", async () => {
  const before = candidate({ profile: null, profiles: [], next_action: "profile" });
  const after = candidate();
  show(before, { canAct: true }, USERS[0], {
    [`POST /api/amaze/feeds/${FEED_ID}/profile`]: { result: { outcome: "profiled" }, feed: after } });
  fireEvent.click(await screen.findByTestId("profile"));
  const done = await screen.findByTestId("feed-done");
  expect(screen.getByTestId("section-summary").contains(done)).toBe(true);
  expect(screen.getAllByTestId("feed-done")).toHaveLength(1);
  expect(done.textContent).toBe("Profile written: profiled.");
});

test("P11: a coverage card of a requirement that no longer selects the feed keeps its title", async () => {
  const gone = "00000000-0000-4000-8000-0000000000a2";
  const page = feedPage();
  show(feedPage({ profile: { ...page.profile!, coverage: [...page.profile!.coverage, {
                    requirement_id: gone, missing: [], items: page.profile!.coverage[0].items }] },
                  coverage_titles: { [REQUIREMENT_ID]: "Daily volume", [gone]: "Old volume" } }));
  const profile = await screen.findByTestId("section-profile");
  expect(profile.textContent).toContain("Old volume");
  expect(profile.textContent).toContain("no longer selects this feed");
  expect(profile.textContent).not.toContain("A requirement");
});

test("P21: FeedView takes the signed-in user from App: no /api/me call, role buttons at once", async () => {
  const fetchStub = show(feedPage({ go_live: { ...feedPage().go_live, latest: {
    id: "go-live-1", created_at: "2026-11-18T09:00:00Z", feed_id: FEED_ID, version: 1, proposal_id: "p",
    approved_by: "dev-data-owner", mapping_ids: [], rule_ids: [], test_load_id: "l", hypercare_until: "2026-11-28" } } }),
    {}, { ...USERS[0], id: "dev-operations", display_name: "Opal Operations", roles: ["operations"] });
  expect(await screen.findByTestId("load-now")).toBeTruthy();
  expect(fetchStub.mock.calls.map(([url]) => String(url))).not.toContain("/api/me");
});

const unmapped = { name: "qty", type: "BIGINT", source_says: "volume", mapped_to: null, mapped_version: null,
                   pending: null, why_none: null, versions: [] };

test("P12: a column with nothing proposed waits for Propose mappings", async () => {
  show(feedPage({ columns: [feedPage().columns[0], unmapped] }));
  const row = (await screen.findAllByTestId("mapping-row")).find((r) => r.dataset.column === "qty")!;
  expect(within(row).getByTestId("why-none").textContent).toBe("waiting for Propose mappings");
});

test("P17: a sent-back run lists its own columns first and folds the skipped ones under not asked", async () => {
  show(feedPage({ glossary_run: { ...feedPage().glossary_run!, reason: "sent_back", columns: ["maturity"],
                                  results: { rating: "already_asked", maturity: "conflict_to_owner",
                                             venue_mic_code: "already_asked", date: "left_to_step_06" } } }));
  const run = await screen.findByTestId("glossary-run");
  const asked = Array.from(run.querySelectorAll("li")).map((li) => li.textContent);
  expect(asked).toEqual(["maturity: conflict to owner", "date: left to step 06"]);
  expect(within(run).getByTestId("run-not-asked").textContent).toBe(
    "Not asked: rating (already asked), venue_mic_code (already asked)");
});

test("P18: Propose mappings is disabled with a word when nothing is left to map", async () => {
  show(feedPage()); // instr_id mapped, qty pending
  const button = await screen.findByTestId("propose-mappings");
  expect(button.hasAttribute("disabled")).toBe(true);
  expect(screen.getByTestId("section-mapping").textContent).toContain("Nothing left to map");
  cleanup();
  show(feedPage({ columns: [feedPage().columns[0], unmapped] }));
  expect((await screen.findByTestId("propose-mappings")).hasAttribute("disabled")).toBe(false);
});

const SUMMARY = {
  id: "summary-2", status: "pending" as const, created_at: "2026-11-18T09:00:00Z", required_approver_roles: ["data_owner"],
  decisions: [], payload: { summary: "Ready to load.", highlights: [], risks: [], unmapped_columns: [], rejected_glossary: [] },
};
const STEWARD = { ...USERS[1], roles: ["data_steward"] };

test("P22 and P23: the last Propose rules answer survives a remount (this tab only); its line is in Rules", async () => {
  sessionStorage.clear();
  const answer = { [`POST /api/amaze/feeds/${FEED_ID}/rules/propose`]: { result: {
    outcome: "rules_proposed", proposal_ids: ["r1"], refused: [{ rule_key: "bad_sql", sql: "DROP", reason: "Not a SELECT." }],
    returned: [{ rule_key: "too_wide", sql: "SELECT _row FROM curated", description: "d", share: 0.8, flagged_rows: 8,
                 total_rows: 10 }] } } };
  show(feedPage(), {}, STEWARD, answer);
  fireEvent.click(await screen.findByTestId("propose-rules"));
  const done = await screen.findByTestId("feed-done");
  expect(screen.getByTestId("section-rules").contains(done)).toBe(true);
  expect((await screen.findByTestId("returned-rules")).textContent).toContain("too_wide");
  cleanup();
  show(feedPage(), {}, STEWARD);
  const rules = await screen.findByTestId("section-rules");
  expect(within(rules).getByTestId("returned-rules").textContent).toContain("too_wide");
  expect(within(rules).getByTestId("refused-rules").textContent).toContain("bad_sql");
  expect(within(rules).getByTestId("rules-kept-note").textContent).toMatch(
    /^From the last Propose rules in this browser tab, .+; not stored\.$/);
});

test("P24 and P8: Prepare says why it is disabled; the go-live names people", async () => {
  show(feedPage({ go_live: { latest: { id: "g", created_at: "2026-11-18T09:00:00Z", feed_id: FEED_ID, version: 1,
                                       proposal_id: "p", approved_by: "dev-data-owner", mapping_ids: [], rule_ids: [],
                                       test_load_id: "l", hypercare_until: "2026-11-28" },
                             hypercare_active: true, pending_summary: SUMMARY } }), {}, USERS[0]);
  expect((await screen.findByTestId("prepare-go-live")).hasAttribute("disabled")).toBe(true);
  expect(screen.getByTestId("prepare-why").textContent).toBe("A summary already waits for the owner.");
  expect(screen.getByTestId("go-live-latest").textContent).toContain("Version 1 approved by Devon Owner");
  expect(screen.getByTestId("go-live-summary").textContent).toContain("The owner is Devon Owner");
});

const OPERATIONS = { id: "dev-operations", display_name: "Opal Operations", email: "o@example.test",
                     roles: ["operations"], app: "backend" } as (typeof USERS)[number];

test("phase 7.9: operations sees Pause and Delete on a live feed; a user with neither role sees none", async () => {
  show(feedPage(), {}, OPERATIONS);
  const box = await screen.findByTestId("feed-lifecycle");
  expect(within(box).getByRole("button", { name: "Pause" })).toBeTruthy();
  expect(within(box).getByRole("button", { name: "Delete" })).toBeTruthy();
  cleanup();
  show(feedPage(), {}, USERS[0]); // backend team: neither the owner nor operations
  await screen.findByTestId("feed-view");
  expect(screen.queryByTestId("feed-lifecycle")).toBeNull();
});

test("phase 7.9: a paused feed says so, offers Resume, and Pause sends the reason and reads the page again", async () => {
  const paused = feedPage({ feed: { ...feedPage().feed, stage: "paused" } });
  show(paused, {}, OPERATIONS);
  expect((await screen.findByTestId("feed-paused-note")).textContent).toContain("not fetched");
  expect(screen.getByRole("button", { name: "Resume" })).toBeTruthy();
  cleanup();
  const onChange = vi.fn();
  const fetchStub = show(feedPage(), { onChange }, OPERATIONS, {
    [`POST /api/amaze/feeds/${FEED_ID}/pause`]: { result: { outcome: "paused" }, feed: paused } });
  fireEvent.click(await screen.findByRole("button", { name: "Pause" }));
  fireEvent.change(screen.getByLabelText("Why it is paused"), { target: { value: "The vendor changes its layout" } });
  fireEvent.click(screen.getByRole("button", { name: "Pause feed" }));
  await vi.waitFor(() => expect(onChange).toHaveBeenCalled());
  expect(fetchStub.mock.calls.some(([u]) => String(u).endsWith("/pause"))).toBe(true);
});

test("phase 7.9: after a delete the page goes back to the registry", async () => {
  show(feedPage(), {}, OPERATIONS, {
    [`DELETE /api/amaze/feeds/${FEED_ID}`]: { outcome: "deleted", code: feedPage().feed.code, rows_deleted: { feeds: 1 } } });
  fireEvent.click(await screen.findByRole("button", { name: "Delete" }));
  fireEvent.change(screen.getByLabelText("Why it is deleted"), { target: { value: "The demo is over" } });
  fireEvent.change(screen.getByLabelText(`Type ${feedPage().feed.code} to confirm`), { target: { value: feedPage().feed.code } });
  fireEvent.click(screen.getByRole("button", { name: "Delete feed" }));
  await vi.waitFor(() => expect(window.location.hash).toBe("#/feeds"));
});

test("P19: the result of Propose mappings shows above the mapping table", async () => {
  show(feedPage({ columns: [feedPage().columns[0], unmapped] }), {}, USERS[1], {
    [`POST /api/amaze/feeds/${FEED_ID}/mappings/propose`]: { result: {
      recommended: [{ source_field: "qty" }], results: [], unchanged: [] } } });
  fireEvent.click(await screen.findByTestId("propose-mappings"));
  const done = await screen.findByTestId("feed-done");
  const table = screen.getByTestId("mapping-table");
  expect(screen.getByTestId("section-mapping").contains(done)).toBe(true);
  expect(done.compareDocumentPosition(table) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
});
