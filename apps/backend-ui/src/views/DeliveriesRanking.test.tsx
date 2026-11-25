import { cleanup, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { renderWithTheme, stubFetch, USERS } from "../test/fixtures";
import DeliveriesRanking from "./DeliveriesRanking";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const URL = "GET /api/amaze/deliveries/overview";

test("recurring feeds are listed in the order the API gave, worst first, each linking to its feed", async () => {
  stubFetch({ [URL]: { outcome: "deliveries_overview", window: "30", one_time: 2, not_set: 1, feeds: [
    { feed_id: "f2", code: "syn_poor", name: "Poor", schedule: "Recurring, every day at 07:00 UTC, 60 min grace", expected: 4,
      on_time: 2, late: 0, missing: 2, on_time_pct: 50, streak: 1 },
    { feed_id: "f1", code: "syn_good", name: "Good", schedule: "Recurring, weekly", expected: 4, on_time: 4, late: 0, missing: 0,
      on_time_pct: 100, streak: 4 },
    { feed_id: "f3", code: "syn_new", name: "New", schedule: "Recurring, weekly", expected: 0, on_time: 0, late: 0, missing: 0,
      on_time_pct: null, streak: 0 }] } });
  renderWithTheme(<DeliveriesRanking devUser={USERS[0].id} />);
  const rows = await screen.findAllByTestId("overview-deliveries-row");
  expect(rows.map((r) => r.textContent)).toEqual([
    expect.stringContaining("syn_poor"), expect.stringContaining("syn_good"), expect.stringContaining("syn_new")]);
  expect(rows[0].textContent).toContain("50% (2 of 4)");
  expect(rows[2].textContent).toContain("none due yet");
  expect(rows[0].querySelector("a")?.getAttribute("href")).toBe("#/feed/f2");
  expect(screen.getByTestId("overview-deliveries-rest").textContent).toBe(
    "2 one-time loads (never late or missing). 1 live feed has no schedule yet: Schedule not set.");
});

test("with no recurring feed it says so, and an unavailable answer does not break the page", async () => {
  stubFetch({ [URL]: { outcome: "deliveries_overview", window: "30", one_time: 0, not_set: 0, feeds: [] } });
  renderWithTheme(<DeliveriesRanking devUser={USERS[0].id} />);
  expect((await screen.findByTestId("overview-deliveries")).textContent).toContain("No recurring feed has a delivery due yet.");
  cleanup();
  stubFetch({});
  renderWithTheme(<DeliveriesRanking devUser={USERS[0].id} />);
  expect((await screen.findByRole("status")).textContent).toContain("Delivery figures are not available");
});
