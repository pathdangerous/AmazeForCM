import { cleanup, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { renderWithTheme, stubFetch } from "./test/fixtures";
import { formatWhen } from "./theme";
import FeedRegistryView from "./views/FeedRegistryView";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

test("one date format: 18 Nov 2026, 12:44; a day alone 18 Nov 2026", () => {
  expect(formatWhen(new Date(2026, 10, 18, 12, 44))).toBe("18 Nov 2026, 12:44");
  expect(formatWhen(new Date(2026, 8, 3, 9, 5))).toBe("3 Sep 2026, 09:05"); // Sep, never ICU's "Sept"
  expect(formatWhen(new Date(2026, 10, 18, 12, 44), { time: false })).toBe("18 Nov 2026");
  expect(formatWhen("2026-11-28")).toBe("28 Nov 2026"); // a date with no time, read as that day where you are
});

test("a view shows its dates in that format", async () => {
  const at = new Date(2026, 10, 18, 9, 0).toISOString();
  stubFetch({ "GET /api/amaze/feeds": [{ id: "00000000-0000-4000-8000-000000000001", created_at: at, code: "syn_x",
    name: "X", stage: "candidate", requirement_id: null, owner_id: null, source_ref: null, registered_at: null,
    source_name: null }] });
  renderWithTheme(<FeedRegistryView devUser="dev-backend-team" people={{}} />);
  expect(await screen.findByText("18 Nov 2026, 09:00")).toBeTruthy();
});
