import { cleanup, screen, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import type { RequestSummary } from "../api";
import { renderWithTheme, REQUIREMENT_ID, stubFetch } from "../test/fixtures";
import RequestsView from "./RequestsView";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const ID = "11111111-1111-4111-8111-111111111111";

function request(overrides: Partial<RequestSummary>): RequestSummary {
  return { id: ID, created_at: "2026-11-18T09:00:00Z", query: "coupon rate", outcome: "raise_requirement",
           product_codes: [], message: "m", requirement_id: REQUIREMENT_ID, requirement_status: "onboarding",
           ...overrides };
}

const steps = (el: HTMLElement) => Array.from(el.querySelectorAll("li")).map((li) => li.textContent);

test("an onboarding request shows the five steps with the current one marked", async () => {
  stubFetch({ "GET /api/consumer/requests": [request({})] });
  renderWithTheme(<RequestsView devUser="dev-requester" />);
  const tracker = await screen.findByTestId(`request-steps-${ID}`);
  expect(steps(tracker)).toEqual(["Checking the first file", "Setting up", "Approved for loading", "First delivery", "Ready"]);
  expect(tracker.querySelector('[aria-current="step"]')?.textContent).toBe("Setting up");
  expect(screen.getByTestId("requirement-status")).toBeTruthy(); // the old story's cell stays
});

test("a ready request marks Ready and links to the product", async () => {
  stubFetch({ "GET /api/consumer/requests": [request({ requirement_status: "ready", outcome: "reuse",
                                                       product_codes: ["demo_bond_terms"] })] });
  renderWithTheme(<RequestsView devUser="dev-requester" />);
  const tracker = await screen.findByTestId(`request-steps-${ID}`);
  expect(tracker.querySelector('[aria-current="step"]')?.textContent).toBe("Ready");
  const link = within(screen.getByTestId("requests")).getByRole("link", { name: "Open demo_bond_terms" });
  expect(link.getAttribute("href")).toBe("#/product/demo_bond_terms");
});
