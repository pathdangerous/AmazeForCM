import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { renderWithTheme, stubFetch } from "../test/fixtures";
import ReportsView from "./ReportsView";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const ID = "00000000-0000-4000-8000-0000000000e1";
const row = (over = {}) => ({ id: ID, created_at: "2026-11-03T06:00:00Z", kind: "daily", subject_id: "dev-data-owner",
  subject_role: "data_owner", period_start: "2026-11-02", period_end: "2026-11-03", version: 1, narrative_status: "written",
  generated_by: "job", ...over });
const numbers = (over = {}) => ({ subject_id: "dev-data-owner", subject_role: "data_owner",
  period: { kind: "daily", start: "2026-11-02", end: "2026-11-03" },
  totals: { feeds: 1, loads: 2, released: 1, held: 1, failed: 0, quarantined: 0, rows: 20, flagged_rows: 4, waived_rows: 2,
    average_trust_score: "80.0" },
  feeds: [{ feed_id: "f1", feed_code: "syn_security_master", loads: 2, released: 1, held: 1, failed: 0, quarantined: 0, rows: 20,
    flagged_rows: 4, waived_rows: 2, average_trust_score: "80.0", open_now: 2, waiting_for_owner: 1, resolved_by_agent: 3,
    resolved_by_hand: 1, waivers: 1, referred_groups: 1, reruns_released: 1, reruns_still_held: 0, drift_events: 1, patterns: 1 }],
  exceptions: { open_now: 2, waiting_for_owner: 1, resolved_by_agent: 3, resolved_by_hand: 1 }, waivers: 1,
  reruns: { released: 1, still_held: 0 }, drift_events: 1,
  patterns: [{ feed_id: "f1", feed_code: "syn_security_master", rule_key: "best_sales_not_negative", dimension: "weekday",
    value: "Monday", failing_loads: 3, loads_in_window: 4, evidence: [
      { load_id: "l1", received_on: "2026-10-12", provider_date: null, flagged_rows: 2 },
      { load_id: "l2", received_on: "2026-10-19", provider_date: null, flagged_rows: 2 },
      { load_id: "l3", received_on: "2026-10-26", provider_date: null, flagged_rows: 1 }] }],
  referred_groups: [{ feed_id: "f1", feed_code: "syn_security_master", rule_key: "best_sales_not_negative", pattern: "-9.9",
    load_id: "l1", reason: "Ask the provider.", decided_by: "dev-data-steward", referred_at: "2026-11-02T10:00:00Z" }],
  decisions: null, ...over });
const report = (over = {}) => ({ ...row(), numbers: numbers(), numbers_sha256: "ab".repeat(32),
  narrative: "Between 2026-11-02 and 2026-11-03: 2 loads, 1 released, 1 held.", narrative_note: null, narrative_log_id: "log-1", ...over });

const LIST = "GET /api/amaze/reports";

test("the list shows my reports, newest first, with their version and who made them", async () => {
  stubFetch({ [LIST]: [row({ version: 2, generated_by: "dev-operator" }), row({ id: "r2", kind: "monthly", period_start: "2026-10-01",
    period_end: "2026-11-01", narrative_status: "none" })] });
  renderWithTheme(<ReportsView devUser="dev-data-owner" roles={["data_owner"]} reportId={null} />);
  const rows = await screen.findAllByTestId("report-row");
  expect(rows).toHaveLength(2);
  expect(rows[0].textContent).toContain("2026-11-02");
  expect(rows[0].textContent).toContain("version 2");
  expect(rows[0].textContent).toContain("dev-operator");
  expect(rows[1].textContent).toContain("Monthly");
  expect(rows[1].textContent).toContain("no narrative");
});

test("a report shows its narrative apart from its numbers, and every number as the API gave it", async () => {
  stubFetch({ [`GET /api/amaze/reports/${ID}`]: report() });
  renderWithTheme(<ReportsView devUser="dev-data-owner" roles={["data_owner"]} reportId={ID} />);
  const narrative = await screen.findByTestId("report-narrative");
  expect(narrative.textContent).toContain("2 loads, 1 released, 1 held");
  expect(narrative.textContent).toContain("separate from the numbers");
  const totals = screen.getByTestId("report-totals").textContent ?? "";
  expect(totals).toContain("Loads2");
  expect(totals).toContain("Held1");
  expect(totals).toContain("Average trust score80.0");
  expect(totals).toContain("Waived rows2");
  expect(screen.getByTestId("report-feeds").textContent).toContain("syn_security_master");
  expect(screen.getByTestId("report-exceptions").textContent).toContain("3 resolved by agent proposals, 1 by hand");
});

test("a recurring pattern shows its dates as evidence, and a referred group appears once", async () => {
  stubFetch({ [`GET /api/amaze/reports/${ID}`]: report() });
  renderWithTheme(<ReportsView devUser="dev-data-owner" roles={["data_owner"]} reportId={ID} />);
  const pattern = await screen.findByTestId("report-patterns");
  expect(pattern.textContent).toContain("best_sales_not_negative");
  expect(pattern.textContent).toContain("every Monday: 3 of 4 loads");
  expect(pattern.textContent).toContain("2026-10-12, 2026-10-19, 2026-10-26");
  expect(screen.getAllByTestId("report-referred")).toHaveLength(1);
});

test("a refused narrative says why, and shows no text", async () => {
  stubFetch({ [`GET /api/amaze/reports/${ID}`]: report({ narrative: null, narrative_status: "refused",
    narrative_note: "The narrative stated 42, which is no number of this report; it was not kept." }) });
  renderWithTheme(<ReportsView devUser="dev-data-owner" roles={["data_owner"]} reportId={ID} />);
  expect((await screen.findByTestId("report-narrative")).textContent).toContain("which is no number of this report");
  expect(screen.getByTestId("report-totals").textContent).toContain("Loads2"); // the numbers stand
});

test("New version asks the API for the same report again and shows its version", async () => {
  const fetchStub = stubFetch({ [`GET /api/amaze/reports/${ID}`]: report(),
    "POST /api/amaze/reports": { result: { outcome: "report_written", report_id: "r9", version: 2 } } });
  renderWithTheme(<ReportsView devUser="dev-data-owner" roles={["data_owner"]} reportId={ID} />);
  fireEvent.click(await screen.findByRole("button", { name: "New version" }));
  await waitFor(() => expect(fetchStub.mock.calls.some(([u, i]) => String(u).endsWith("/api/amaze/reports") && i?.method === "POST"
    && String(i?.body).includes('"period_start":"2026-11-02"') && String(i?.body).includes('"role":"data_owner"'))).toBe(true));
  expect((await screen.findByTestId("report-made")).textContent).toContain("version 2");
});
