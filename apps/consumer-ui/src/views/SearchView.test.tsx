import { cleanup, fireEvent, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { renderWithTheme, stubFetch } from "../test/fixtures";
import SearchView from "./SearchView";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

test("a product hit links to its product page", async () => {
  const hit = { outcome: "found", product: { code: "demo_bond_terms", name: "Bond terms", description: "d",
                                             domain: "reference", element_keys: ["coupon_rate"] } };
  stubFetch({ "GET /api/consumer/search?q=bond": { match: { elements: [], ambiguous: [], domains: [], terms: [] },
                                                   hits: [hit], element_names: {} } });
  // jsdom has no document.fonts, which the autosizing Textarea of the request form listens to
  Object.defineProperty(document, "fonts", { configurable: true,
    value: { addEventListener: () => undefined, removeEventListener: () => undefined } });
  renderWithTheme(<SearchView devUser="dev-requester" />);
  fireEvent.change(screen.getByTestId("search-box"), { target: { value: "bond" } });
  fireEvent.click(screen.getByRole("button", { name: "Search" }));
  const link = await screen.findByRole("link", { name: "Bond terms" });
  expect(link.getAttribute("href")).toBe("#/product/demo_bond_terms");
});
