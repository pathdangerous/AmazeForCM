import { cleanup, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import type { Feed } from "../api";
import { renderWithTheme, stubFetch } from "../test/fixtures";
import FeedRegistryView from "./FeedRegistryView";

const FEED: Feed = {
  id: "00000000-0000-4000-8000-000000000001", created_at: "2026-11-18T09:00:00Z", code: "syn_test_feed",
  name: "Test feed", stage: "candidate", requirement_id: null, owner_id: null, source_ref: "files/test_feed.csv",
  registered_at: null,
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

test("the registry renders a stubbed feed list with its feed-link", async () => {
  stubFetch({ "GET /api/amaze/feeds": [FEED] });
  renderWithTheme(<FeedRegistryView devUser="dev-backend-team" />);
  const link = await screen.findByTestId("feed-link-syn_test_feed");
  expect(link.tagName).toBe("A"); // the story clicks it (plan, 10.3)
  expect(link.getAttribute("href")).toBe(`#/feed/${FEED.id}`);
  expect(screen.getByTestId("feeds-table").tagName).toBe("TABLE");
  expect(screen.getByRole("group", { name: "Stage" })).toBeTruthy();
});

test("the empty registry keeps feeds-empty", async () => {
  stubFetch({ "GET /api/amaze/feeds": [] });
  renderWithTheme(<FeedRegistryView devUser="dev-backend-team" />);
  expect((await screen.findByTestId("feeds-empty")).textContent).toContain("No feeds yet.");
});
