import { cleanup, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { FEED_ID, renderWithTheme, stubFetch } from "../test/fixtures";
import ExceptionsSection from "./ExceptionsSection";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const FEED_PATH = `GET /api/amaze/feeds/${FEED_ID}/exceptions`;
const counts = (over = {}) => ({ load_id: "l1", received_at: "2026-11-24T08:00:00Z", status: "held", purpose: "scheduled",
  rows: 10, flagged_rows: 2, exceptions: 3, ...over });
const group = (over = {}) => ({ group_key: "g1", rule_key: "best_sales_not_negative", description: "Best sales is not negative.",
  elements: ["best_sales"], pattern: "-9.9", exceptions: 2, state: "open",
  samples: [{ row_number: 4, values: { best_sales: "-12.5", isin: "X" } }], ...over });
const view = (over = {}) => ({ load_id: "l1", feed_id: FEED_ID, status: "held", can_fix: true, rows: 10, flagged_rows: 2,
  exceptions: 3, total_groups: 1, limit: 50, offset: 0, groups: [group()], ...over });

test("nothing is shown for a feed whose loads have no exception", async () => {
  const fetchStub = stubFetch({ [FEED_PATH]: [counts({ exceptions: 0, flagged_rows: 0 })] });
  renderWithTheme(<ExceptionsSection feedId={FEED_ID} devUser="dev-operator" />);
  await waitFor(() => expect(fetchStub).toHaveBeenCalled());
  expect(screen.queryByTestId("section-exceptions")).toBeNull();
});

test("a held load's groups show the rule, the pattern, the count and sample rows of the rule's columns", async () => {
  stubFetch({ [FEED_PATH]: [counts()], "GET /api/amaze/loads/l1/exceptions?offset=0": view() });
  renderWithTheme(<ExceptionsSection feedId={FEED_ID} devUser="dev-operator" />);
  const row = await screen.findByTestId("exception-group-g1");
  expect(row.textContent).toContain("best_sales_not_negative");
  expect(row.textContent).toContain("-9.9");
  expect(row.textContent).toContain("row 4: best_sales=-12.5");
  expect(row.textContent).not.toContain("isin");
  expect(screen.getByTestId("exceptions-summary").textContent).toContain("3 exceptions in 2 of 10 rows, 1 groups");
  expect(screen.getByTestId("exceptions-summary").textContent).toContain("each group can take a fix");
});

test("a released load's groups are for information only", async () => {
  stubFetch({ [FEED_PATH]: [counts({ status: "released" })],
              "GET /api/amaze/loads/l1/exceptions?offset=0": view({ status: "released", can_fix: false }) });
  renderWithTheme(<ExceptionsSection feedId={FEED_ID} devUser="dev-operator" />);
  expect((await screen.findByTestId("exceptions-summary")).textContent).toContain("for information only");
});
