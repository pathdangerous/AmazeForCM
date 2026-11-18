import { cleanup, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { renderWithTheme, REQUIREMENT_ID, requirementPage, stubFetch } from "./test/fixtures";
import { formatWhen } from "./theme";
import RequirementView from "./views/RequirementView";

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
  const at = new Date(2026, 10, 18, 12, 44).toISOString();
  stubFetch({ [`GET /api/consumer/requirements/${REQUIREMENT_ID}`]: requirementPage({
    last_fetch: { outcome: "fetched", at, detail: null } }) });
  renderWithTheme(<RequirementView devUser="dev-requester" requirementId={REQUIREMENT_ID} />);
  expect((await screen.findByTestId("last-fetch")).textContent).toBe("File received, 18 Nov 2026, 12:44");
});
