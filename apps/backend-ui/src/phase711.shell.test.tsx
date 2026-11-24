import { cleanup, fireEvent, screen, within } from "@testing-library/react";
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

function shell() {
  window.history.replaceState(null, "", "/#/onboardings");
  stubFetch({
    ...shellRoutes(),
    "GET /api/amaze/people": [],
    "GET /api/amaze/onboardings": [],
    "GET /api/notifications": [],
  });
  renderWithTheme(<App />);
}

test("operator shell uses the approved brand and all six group headings, even empty groups", async () => {
  shell();
  // This test covers the shell, not the default landing view's asynchronously loaded body.
  // shell() explicitly selects #/onboardings; wait for persona setup rather than a body empty state.
  await within(screen.getByTestId("dev-user")).findByText("Blake Backend (backend_team)");
  const header = screen.getByRole("banner");
  expect(within(header).getByText("amaze")).toBeTruthy();
  expect(within(header).getByText("for Capital Markets")).toBeTruthy();
  const nav = screen.getByRole("navigation", { name: "Main" });
  for (const group of ["Overview", "Onboard", "Govern", "Operate", "Masters", "Insights"]) {
    expect(within(nav).getByText(group, { exact: true })).toBeTruthy();
  }
  expect(screen.getByTestId("dev-user").tagName).toBe("SELECT");
  expect(screen.getByTestId("signed-in-as").getAttribute("aria-label")).toBe("Signed in user");
});

test("grouped links target only implemented views; unsupported mockup destinations are omitted", async () => {
  shell();
  const nav = await screen.findByRole("navigation", { name: "Main" });
  const links = within(nav).getAllByRole("link");
  const allowed = new Set(["#/overview", "#/onboardings", "#/feeds", "#/glossary", "#/status", "#/notifications", "#/onboard", "#/reports"]);
  for (const link of links) expect(allowed.has(link.getAttribute("href") ?? ""), link.textContent ?? "").toBe(true);
  for (const unsupported of ["Sources", "Rules library", "Approvals", "Loads", "Exceptions", "Schedules", "Security master", "Reference data", "DQ dashboard", "Lineage", "Usage"]) {
    expect(within(nav).queryByRole("link", { name: unsupported })).toBeNull();
  }
  expect(within(nav).getByRole("link", { name: "Operations overview" }).getAttribute("href")).toBe("#/overview");
  expect(within(screen.getByRole("banner")).getByRole("link", { name: /View notifications/ }).getAttribute("href")).toBe("#/notifications");
  expect(links.find((link) => link.getAttribute("href") === "#/onboardings")?.getAttribute("aria-current")).toBe("page");
});

test("menu toggle announces expansion and keeps navigation available when reopened", async () => {
  shell();
  const toggle = screen.getByRole("button", { name: "Toggle menu" });
  const before = toggle.getAttribute("aria-expanded");
  expect(["true", "false"]).toContain(before);
  expect(toggle.getAttribute("aria-controls")).toBeTruthy();
  fireEvent.click(toggle);
  expect(toggle.getAttribute("aria-expanded")).toBe(before === "true" ? "false" : "true");
  fireEvent.click(toggle);
  expect(toggle.getAttribute("aria-expanded")).toBe(before);
  expect(await screen.findByTestId("onboard-a-file")).toHaveProperty("href", expect.stringContaining("#/onboard"));
});
