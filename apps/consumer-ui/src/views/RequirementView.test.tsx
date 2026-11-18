import { cleanup, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import type { RequirementPage } from "../api";
import { renderWithTheme, REQUIREMENT_ID, requirementPage, stubFetch } from "../test/fixtures";
import RequirementView from "./RequirementView";
import SearchView from "./SearchView";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function show(page: RequirementPage) {
  stubFetch({ [`GET /api/consumer/requirements/${REQUIREMENT_ID}`]: page });
  renderWithTheme(<RequirementView devUser="dev-requester" requirementId={REQUIREMENT_ID} />);
}

test("P1: the elements show by their glossary names", async () => {
  show(requirementPage());
  expect((await screen.findByTestId("elements")).textContent).toBe("Coupon rate");
  expect(screen.getByRole("heading", { name: "Requirement" }).className).toContain("mantine-Title");
});

test("P2: an empty source list says the request will close", async () => {
  show(requirementPage({ status: "source_selection", next_action: "pick_source", listed: [], progress: null }));
  expect((await screen.findByTestId("no-listed-source")).textContent).toBe(
    "No listed source is available any more; the next daily run closes this request.");
});

test("P29: the five states as a list with the current one marked; the progress sentence is unchanged", async () => {
  const page = requirementPage();
  show(page);
  const steps = await screen.findByTestId("progress-steps");
  expect(Array.from(steps.querySelectorAll("li")).map((li) => li.textContent)).toEqual(
    ["Checking the first file", "Setting up", "Approved for loading", "First delivery", "Ready"]);
  expect(steps.querySelector('[aria-current="step"]')?.textContent).toBe("Setting up");
  expect(screen.getByTestId("progress").textContent).toBe(page.progress!.text);
  cleanup();
  show(requirementPage({ progress: { state: "delivery_held", text: "The first delivery was held." } }));
  const held = (await screen.findByTestId("progress-steps")).querySelector('[aria-current="step"]') as HTMLElement;
  expect(held.dataset.warn).toBe("true");
  expect(held.textContent).toBe("First delivery");
  cleanup();
  show(requirementPage({ status: "ready", progress: null, message: "Ready. Your data is in Bond terms." }));
  expect((await screen.findByTestId("progress-steps")).querySelector('[aria-current="step"]')?.textContent).toBe("Ready");
});

test("A2: Search is a filled primary button, not full width", async () => {
  stubFetch({});
  renderWithTheme(<SearchView devUser="dev-requester" />);
  const search = screen.getByRole("button", { name: "Search" }); // the name the story clicks
  expect(search.getAttribute("data-variant")).toBe("filled");
  expect(search.className).toContain("mantine-Button");
  expect(search.hasAttribute("data-block")).toBe(false);
});
