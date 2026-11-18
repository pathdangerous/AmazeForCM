import { act, cleanup, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import type { Notification } from "../api";
import App from "../App";
import { renderWithTheme, shellRoutes, stubFetch } from "../test/fixtures";
import NotificationsView from "./NotificationsView";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
  localStorage.clear();
  window.location.hash = "";
});

const notice = (id: string, at: string, link: string | null = null): Notification => ({
  id, created_at: at, subject: `Ready: ${id}`, body: "Your data is in Test feed.", link, read_at: null });

test("P27: the Notifications tab counts notices newer than the last look, after a poll", async () => {
  vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
  const routes = { ...shellRoutes(), "GET /api/health": { status: "ok", database: "ok", adapters: {}, unavailable: [] },
                   "GET /api/notifications": [notice("a", "2026-11-18T09:00:00Z")] };
  stubFetch(routes);
  renderWithTheme(<App />);
  expect(await screen.findByRole("link", { name: "Notifications (1)" })).toBeTruthy();
  stubFetch({ ...routes, "GET /api/notifications": [notice("b", "2026-11-18T09:05:00Z"), notice("a", "2026-11-18T09:00:00Z")] });
  await act(async () => {
    vi.advanceTimersByTime(15_000);
  });
  expect(await screen.findByRole("link", { name: "Notifications (2)" })).toBeTruthy();
});

test("P28: consumer-ui links a notice to its requirement page; a feed link stays text", () => {
  renderWithTheme(<NotificationsView rows={[notice("a", "2026-11-18T09:00:00Z", "#/requirement/r1"),
                                            notice("b", "2026-11-18T09:00:00Z", "#/feed/f1")]}
                                     error={null} onRefresh={() => undefined} />);
  const [requirement, feed] = screen.getAllByTestId("notification");
  expect(requirement.querySelector("a")?.getAttribute("href")).toBe("#/requirement/r1");
  expect(feed.querySelector("a")).toBeNull();
  expect(screen.getByRole("heading", { name: "Notifications" }).className).toContain("mantine-Title");
});
