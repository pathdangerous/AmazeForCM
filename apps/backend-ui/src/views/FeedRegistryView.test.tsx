import { cleanup, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import type { FeedRow } from "../api";
import { PEOPLE, renderWithTheme, stubFetch } from "../test/fixtures";
import FeedRegistryView from "./FeedRegistryView";

const NAMES = Object.fromEntries(PEOPLE.map((p) => [p.id, p.display_name]));

const FEED: FeedRow = {
  id: "00000000-0000-4000-8000-000000000001", created_at: "2026-11-18T09:00:00Z", code: "syn_test_feed",
  name: "Test feed", stage: "registered", requirement_id: null, owner_id: "dev-data-owner",
  source_ref: "files/test_feed.csv", registered_at: null, source_name: "Synthetic test source",
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

test("the registry renders a stubbed feed list with its feed-link", async () => {
  stubFetch({ "GET /api/amaze/feeds": [FEED] });
  renderWithTheme(<FeedRegistryView devUser="dev-backend-team" people={NAMES} />);
  const link = await screen.findByTestId("feed-link-syn_test_feed");
  expect(link.tagName).toBe("A"); // the story clicks it (plan, 10.3)
  expect(link.getAttribute("href")).toBe(`#/feed/${FEED.id}`);
  expect(screen.getByTestId("feeds-table").tagName).toBe("TABLE");
  expect(screen.getByRole("group", { name: "Stage" })).toBeTruthy();
});

test("P3 and P4: the owner by name, the source by its catalogue name with the reference beside it", async () => {
  stubFetch({ "GET /api/amaze/feeds": [FEED, { ...FEED, id: "00000000-0000-4000-8000-000000000002", code: "syn_other",
                                               owner_id: null, source_name: null }] });
  renderWithTheme(<FeedRegistryView devUser="dev-backend-team" people={NAMES} />);
  const table = await screen.findByTestId("feeds-table");
  const [first, second] = Array.from(table.querySelectorAll("tbody tr"));
  expect(first.textContent).toContain("Devon Owner");
  expect(first.textContent).not.toContain("dev-data-owner");
  expect(first.querySelector('[data-testid="source-name"]')?.textContent).toBe("Synthetic test source");
  expect(first.querySelector('[data-testid="source-ref"]')?.textContent).toBe("files/test_feed.csv");
  expect(second.querySelector('[data-testid="source-name"]')).toBeNull(); // no catalogue name: the reference only
  expect(second.textContent).toContain("files/test_feed.csv");
});

test("P5: the empty registry says when feeds appear, with no project word", async () => {
  stubFetch({ "GET /api/amaze/feeds": [] });
  renderWithTheme(<FeedRegistryView devUser="dev-backend-team" people={NAMES} />);
  const empty = await screen.findByTestId("feeds-empty");
  expect(empty.textContent).toContain("No feeds yet.");
  expect(empty.textContent).toContain("Feeds appear here when a requester's source is selected.");
  expect(empty.textContent).not.toContain("phase");
});
