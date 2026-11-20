import { cleanup, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { renderWithTheme, stubFetch } from "../test/fixtures";
import RunBanner from "./RunBanner";
import StatusView from "./StatusView";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

test("task 13: Status is converted and keeps its words", async () => {
  stubFetch({ "GET /api/health": { status: "ok", database: "ok", adapters: { llm: "mock" }, unavailable: [] } });
  renderWithTheme(<StatusView />);
  expect(screen.getByRole("heading", { name: "Status" }).className).toContain("mantine-Title");
  const db = await screen.findByText("ok", { selector: '[data-testid="db-status"] *' });
  expect(db.className).toContain("mantine-Badge");
});

const HEALTH = { status: "ok", database: "ok", adapters: { llm: "mock" }, unavailable: [] };

test("phase 7.9: Status lists the deleted feeds: who, when, why and how many rows went", async () => {
  stubFetch({ "GET /api/health": HEALTH, "GET /api/amaze/feed-deletions": [{
    id: "d1", at: "2026-11-20T09:30:00Z", feed_id: "f1", code: "syn_security_master", name: "Security master",
    by: "dev-operator", reason: "The demo is over", rows_deleted: { feeds: 1, loads: 2, standard_records: 140 } }] });
  renderWithTheme(<StatusView devUser="dev-operator" people={{ "dev-operator": "Olive Operator" }} />);
  const list = await screen.findByTestId("feed-deletions");
  expect(list.textContent).toContain("syn_security_master");
  expect(list.textContent).toContain("Olive Operator");
  expect(list.textContent).toContain("The demo is over");
  expect(list.textContent).toContain("143 rows"); // feeds 1 + loads 2 + standard_records 140 = 143
});

test("phase 7.9: with no deletion Status says so", async () => {
  stubFetch({ "GET /api/health": HEALTH, "GET /api/amaze/feed-deletions": [] });
  renderWithTheme(<StatusView devUser="dev-operator" people={{}} />);
  expect((await screen.findByTestId("no-feed-deletions")).textContent).toContain("No feed has been deleted");
});

test("task 13: the run banner is converted; a failed run offers Run again as a button", async () => {
  stubFetch({ "GET /api/amaze/glossary/runs": { active: [], waiting_proposals: 2, finished: [{
    id: "r1", feed_id: "f1", feed_code: "syn_test_feed", feed_name: "Test feed", status: "failed", reason: "registered",
    attempt: 1, created_at: "2026-11-18T09:00:00Z", finished_at: "2026-11-18T09:01:00Z", error: "timeout", counts: {} }] } });
  renderWithTheme(<RunBanner devUser="dev-backend-team" canRun={true} refresh="x" onChange={() => undefined} />);
  const again = await screen.findByTestId("run-again");
  expect(again.className).toContain("mantine-Button");
  expect(screen.getByTestId("run-banner-waiting").textContent).toContain("2 proposals wait for the glossary owner");
});
