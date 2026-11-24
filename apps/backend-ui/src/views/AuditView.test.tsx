import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { FEED_ID, renderWithTheme, stubFetch } from "../test/fixtures";
import AuditView from "./AuditView";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const MONTH = new Date().toISOString().slice(0, 7);
const AUDIT = `GET /api/amaze/audit?month=${MONTH}`;
const row = (over = {}) => ({ at: `${MONTH}-02T10:00:00Z`, kind: "decision", feed_id: FEED_ID, feed_code: "syn_security_master",
  who: "dev-data-steward", role: "data_steward", decision: "approved", item: "exception_fix", summary: "Ask the provider.",
  by: "agent", agent: "ExceptionFixAgent", code_version: 2, waiver: false, rerun_of: null, note: null, proposal_id: "p1",
  load_id: null, ...over });
const view = (rows: unknown[], over = {}) => ({ month: MONTH, count: rows.length, truncated: false, rows, ...over });
const FEEDS = "GET /api/amaze/feeds";

test("an erase of profiling history is a row: who erased, which versions, and why", async () => {
  stubFetch({ [FEEDS]: [], [AUDIT]: view([row({ kind: "erasure", decision: "erased", item: "profiles", who: "dev-data-owner",
    role: null, summary: "Erased profile versions 2, 3", by: "manual", agent: null, note: "Old files nobody uses.", proposal_id: null })]) });
  renderWithTheme(<AuditView devUser="dev-operator" />);
  const [erased] = await screen.findAllByTestId("audit-row");
  expect(erased.textContent).toContain("erased profiles");
  expect(erased.textContent).toContain("dev-data-owner");
  expect(erased.textContent).toContain("Old files nobody uses.");
});

test("every decision shows who, what, who made the item, the code version, and a waiver or a rerun", async () => {
  stubFetch({ [FEEDS]: [], [AUDIT]: view([
    row(), row({ kind: "decision", decision: "approved", who: "dev-data-owner", role: "data_owner", summary: "Credits are real.",
      by: "manual", agent: null, waiver: true }),
    row({ kind: "rerun", decision: "started", item: "load", who: null, role: null, summary: "Rerun of a held load's file", by: "manual",
      agent: null, note: "Both approved.", rerun_of: "11111111-1111-4111-8111-111111111111", code_version: 1 }),
    row({ decision: "rejected", who: "system", by: "system", agent: null, note: "stale" })]) });
  renderWithTheme(<AuditView devUser="dev-operator" />);
  const rows = await screen.findAllByTestId("audit-row");
  expect(rows).toHaveLength(4);
  expect(rows[0].textContent).toContain("dev-data-steward");
  expect(rows[0].textContent).toContain("agent (ExceptionFixAgent)");
  expect(rows[0].textContent).toContain("version 2");
  expect(rows[1].textContent).toContain("waiver");
  expect(rows[1].textContent).toContain("manual");
  expect(rows[2].textContent).toContain("not recorded");
  expect(rows[2].textContent).toContain("rerun of load 11111111");
  expect(rows[2].textContent).toContain("Both approved.");
  expect(rows[3].textContent).toContain("system");
  expect(screen.getByTestId("audit-count").textContent).toContain("4 rows");
});

test("the month, the person and the feed are filters the API is asked for", async () => {
  const fetchStub = stubFetch({ [FEEDS]: [{ id: FEED_ID, code: "syn_security_master", name: "Security master" }],
    [AUDIT]: view([row()]), [`GET /api/amaze/audit?month=${MONTH}&person=dev-data-owner`]: view([]),
    [`GET /api/amaze/audit?month=${MONTH}&feed=${FEED_ID}`]: view([row()]) });
  renderWithTheme(<AuditView devUser="dev-operator" />);
  await screen.findAllByTestId("audit-row");
  fireEvent.change(screen.getByLabelText("Person"), { target: { value: "dev-data-owner" } });
  expect(await screen.findByText("No decision in this month for these filters.")).toBeTruthy();
  fireEvent.change(screen.getByLabelText("Person"), { target: { value: "" } });
  fireEvent.change(await screen.findByLabelText("Feed"), { target: { value: FEED_ID } });
  await waitFor(() => expect(fetchStub.mock.calls.some(([u]) => String(u).endsWith(`&feed=${FEED_ID}`))).toBe(true));
});

test("Download CSV asks the CSV endpoint with the same filters and saves what it returns", async () => {
  const fetchStub = stubFetch({ [FEEDS]: [], [AUDIT]: view([row()]),
    [`GET /api/amaze/audit.csv?month=${MONTH}`]: "time,kind\r\n" });
  const saved: string[] = [];
  vi.stubGlobal("URL", { createObjectURL: (b: Blob) => { saved.push(String(b.size)); return "blob:x"; }, revokeObjectURL: () => undefined });
  renderWithTheme(<AuditView devUser="dev-operator" />);
  await screen.findAllByTestId("audit-row");
  fireEvent.click(screen.getByRole("button", { name: "Download CSV" }));
  await waitFor(() => expect(fetchStub.mock.calls.some(([u]) => String(u).includes("/audit.csv?month="))).toBe(true));
  await waitFor(() => expect(saved.length).toBe(1));
});

test("a month with more rows than the screen shows says the CSV has them all", async () => {
  stubFetch({ [FEEDS]: [], [AUDIT]: view([row()], { count: 5000, truncated: true }) });
  renderWithTheme(<AuditView devUser="dev-operator" />);
  expect((await screen.findByTestId("audit-count")).textContent).toContain("5000 rows; the first 1 are shown");
});
