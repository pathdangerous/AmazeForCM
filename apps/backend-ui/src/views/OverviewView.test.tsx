import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { renderWithTheme, stubFetch } from "../test/fixtures";
import OverviewView from "./OverviewView";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const overview = (days: number, extra = {}) => ({
  days, feeds_live: 1, feeds_paused: 0, onboardings_active: 0, loads_count: 0, average_trust_score: null, release_threshold: 85,
  quality: null, pending_decisions: [], recent_loads: [],
  actions: { by_agent: 2, by_hand: 1, pending: 1 },
  drift_events: [{ load_id: "l1", feed_id: "f1", feed_code: "syn_security_master", at: "2026-11-23T10:00:00Z", outcome: "additive_parked",
                   new_columns: 1, missing_columns: 0, type_changes: 0, order_changed: true }], ...extra });

test("Daily and Monthly switch the period to 1 and 30 days and the heading says which", async () => {
  const fetchStub = stubFetch({
    "GET /api/amaze/overview?days=7": overview(7), "GET /api/amaze/overview?days=1": overview(1),
    "GET /api/amaze/overview?days=30": overview(30) });
  renderWithTheme(<OverviewView devUser="dev-operator" />);
  await screen.findByText("Drift and actions");
  fireEvent.click(screen.getByRole("button", { name: "Daily" }));
  await screen.findByText("Drift and actions today");
  expect(screen.getByRole("button", { name: "Daily" }).getAttribute("aria-pressed")).toBe("true");
  fireEvent.click(screen.getByRole("button", { name: "Monthly" }));
  await screen.findByText("Drift and actions this month");
  const urls = fetchStub.mock.calls.map(([u]) => String(u));
  expect(urls).toEqual(expect.arrayContaining(["/api/amaze/overview?days=1", "/api/amaze/overview?days=30"]));
  expect((screen.getByLabelText("Period") as HTMLSelectElement).value).toBe("30");
});

test("drift events and the actions applied by agents and by hand are shown, as counts from the API", async () => {
  stubFetch({ "GET /api/amaze/overview?days=7": overview(7) });
  renderWithTheme(<OverviewView devUser="dev-operator" />);
  const box = await screen.findByTestId("overview-actions");
  expect(box.textContent).toContain("2 applied by agents, 1 done by hand; 1 waiting");
  expect(screen.getByTestId("overview-drift").textContent).toContain("syn_security_master");
  expect(screen.getByTestId("overview-drift").textContent).toContain("1 new, 0 missing, 0 type changes, order changed");
});

test("exception fixes are counted by agent and by hand, with the open groups and the reruns (7.14)", async () => {
  stubFetch({ "GET /api/amaze/overview?days=7": overview(7, { exceptions: { by_agent: 3, by_hand: 1, open: 2,
    waiting_for_owner: 1, reruns_released: 1, reruns_still_held: 2 } }) });
  renderWithTheme(<OverviewView devUser="dev-operator" />);
  const box = await screen.findByTestId("overview-exceptions");
  expect(box.textContent).toContain("3 resolved by agent proposals, 1 by hand");
  expect(box.textContent).toContain("2 groups open, 1 waiting for the data owner");
  expect(box.textContent).toContain("Reruns: 1 released, 2 still held");
});

test("with no drift in the period it says so, and an older API answer without these fields shows no section", async () => {
  stubFetch({ "GET /api/amaze/overview?days=7": overview(7, { drift_events: [] }) });
  renderWithTheme(<OverviewView devUser="dev-operator" />);
  await waitFor(() => expect(screen.getByText("No delivery drifted in this period.")).toBeTruthy());
  cleanup();
  const old = overview(7) as Record<string, unknown>;
  delete old.actions;
  delete old.drift_events;
  stubFetch({ "GET /api/amaze/overview?days=7": old });
  renderWithTheme(<OverviewView devUser="dev-operator" />);
  await screen.findByText("Operations overview");
  await waitFor(() => expect(screen.getByText("Feeds live")).toBeTruthy());
  expect(screen.queryByTestId("overview-actions")).toBeNull();
});
