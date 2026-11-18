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

test("task 13: the run banner is converted; a failed run offers Run again as a button", async () => {
  stubFetch({ "GET /api/amaze/glossary/runs": { active: [], waiting_proposals: 2, finished: [{
    id: "r1", feed_id: "f1", feed_code: "syn_test_feed", feed_name: "Test feed", status: "failed", reason: "registered",
    attempt: 1, created_at: "2026-11-18T09:00:00Z", finished_at: "2026-11-18T09:01:00Z", error: "timeout", counts: {} }] } });
  renderWithTheme(<RunBanner devUser="dev-backend-team" canRun={true} refresh="x" onChange={() => undefined} />);
  const again = await screen.findByTestId("run-again");
  expect(again.className).toContain("mantine-Button");
  expect(screen.getByTestId("run-banner-waiting").textContent).toContain("2 proposals wait for the glossary owner");
});
