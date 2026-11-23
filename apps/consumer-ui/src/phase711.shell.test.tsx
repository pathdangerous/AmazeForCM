import { cleanup, screen, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import App from "./App";
import { renderWithTheme, shellRoutes, stubFetch } from "./test/fixtures";

afterEach(() => {
  cleanup();
  localStorage.clear();
  window.history.replaceState(null, "", "/");
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

test("marketplace shell uses the approved brand and preserves native persona/test interfaces and existing destinations", async () => {
  window.history.replaceState(null, "", "/#/catalogue");
  stubFetch({
    ...shellRoutes(),
    "GET /api/consumer/catalogue": [],
    "GET /api/notifications": [],
  });
  renderWithTheme(<App />);
  expect(await screen.findByTestId("signed-in-as")).toBeTruthy();
  const header = screen.getByRole("banner");
  expect(within(header).getByText("amaze")).toBeTruthy();
  expect(within(header).getByText("Marketplace", { exact: true })).toBeTruthy();
  const nav = screen.getByRole("navigation", { name: "Marketplace" });
  expect(within(nav).getAllByRole("link").map((link) => link.getAttribute("href")))
    .toEqual(["#/search", "#/catalogue", "#/requests", "#/notifications"]);
  expect(within(nav).getByRole("link", { name: "Catalogue" }).getAttribute("aria-current")).toBe("page");
  expect(screen.getByTestId("dev-user").tagName).toBe("SELECT");
  await within(screen.getByTestId("dev-user")).findByText("Riley Requester (requester)");
});
