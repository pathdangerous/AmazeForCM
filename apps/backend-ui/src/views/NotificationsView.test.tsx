import { act, cleanup, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import type { Notification } from "../api";
import App from "../App";
import { renderWithTheme, shellRoutes, stubFetch } from "../test/fixtures";
import NotificationsView from "./NotificationsView";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
  localStorage.clear();
  window.location.hash = "";
});

const notice = (id: string, at: string, link: string | null = null): Notification => ({
  id, created_at: at, subject: `Delivery held: syn_${id}`, body: "Held.", link, read_at: null });

test("P27: the Notifications tab counts notices newer than the last look, after a poll", async () => {
  // The poll is the callback App hands to setInterval; the test takes it and calls it, instead of faking the timer. Faking
  // setInterval also stopped Testing Library's own polling (its waitFor uses the global setInterval), so the assertion
  // below waited on one DOM mutation inside the default 1 s: a busy machine made it fail (see the config's timeouts).
  const polls: (() => void)[] = [];
  const real = window.setInterval.bind(window);
  vi.spyOn(window, "setInterval").mockImplementation(((fn: TimerHandler, ms?: number, ...rest: unknown[]) => {
    if (ms === 15_000 && typeof fn === "function") polls.push(fn as () => void);
    return real(fn, ms, ...rest);
  }) as typeof window.setInterval);
  const routes = { ...shellRoutes(), "GET /api/notifications": [notice("a", "2026-11-18T09:00:00Z")] };
  stubFetch(routes);
  renderWithTheme(<App />);
  expect(await screen.findByRole("link", { name: "Notifications (1)" })).toBeTruthy();
  expect(polls.length).toBeGreaterThan(0); // the inbox registered its 15 s poll
  stubFetch({ ...routes, "GET /api/notifications": [notice("b", "2026-11-18T09:05:00Z"), notice("a", "2026-11-18T09:00:00Z")] });
  await act(async () => {
    polls[polls.length - 1](); // the 15 s tick, now
  });
  expect(await screen.findByRole("link", { name: "Notifications (2)" })).toBeTruthy();
});

test("P28: backend-ui links a notice to its feed page; a requirement link stays text", () => {
  renderWithTheme(<NotificationsView rows={[notice("a", "2026-11-18T09:00:00Z", "#/feed/f1"),
                                            notice("b", "2026-11-18T09:00:00Z", "#/requirement/r1")]}
                                     error={null} onRefresh={() => undefined} />);
  const [feed, requirement] = screen.getAllByTestId("notification");
  expect(feed.querySelector("a")?.getAttribute("href")).toBe("#/feed/f1");
  expect(requirement.querySelector("a")).toBeNull();
  expect(screen.getByRole("heading", { name: "Notifications" }).className).toContain("mantine-Title");
});
