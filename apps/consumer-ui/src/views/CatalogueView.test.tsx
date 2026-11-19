import { cleanup, screen, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { CATALOGUE, renderWithTheme, stubFetch } from "../test/fixtures";
import CatalogueView from "./CatalogueView";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

test("products are grouped by domain, a card links to the product page", async () => {
  stubFetch({ "GET /api/consumer/catalogue": CATALOGUE });
  renderWithTheme(<CatalogueView devUser="dev-requester" />);
  expect(await screen.findByRole("heading", { name: "Reference data" })).toBeTruthy();
  expect(screen.getByRole("heading", { name: "Market data" })).toBeTruthy();
  const card = screen.getByTestId("catalogue-product-demo_bond_terms");
  expect(card.getAttribute("href")).toBe("#/product/demo_bond_terms");
  expect(card.textContent).toContain("Bond terms");
  expect(card.textContent).toContain("Terms of bonds.");
  expect(card.textContent).toContain("Olly Owner");
  expect(card.textContent).toContain("Updated 18 Nov 2026, 12:44");
});

test("the access badge says Available or Access restricted", async () => {
  stubFetch({ "GET /api/consumer/catalogue": CATALOGUE });
  renderWithTheme(<CatalogueView devUser="dev-requester" />);
  const open = await screen.findByTestId("catalogue-product-demo_bond_terms");
  expect(within(open).getByText("Available")).toBeTruthy();
  expect(within(screen.getByTestId("catalogue-product-demo_prices")).getByText("Access restricted")).toBeTruthy();
});

test("an empty catalogue says so", async () => {
  stubFetch({ "GET /api/consumer/catalogue": [] });
  renderWithTheme(<CatalogueView devUser="dev-requester" />);
  expect(await screen.findByText("No data products yet.")).toBeTruthy();
});

test("a failed load shows the reason", async () => {
  stubFetch({});
  renderWithTheme(<CatalogueView devUser="dev-requester" />);
  expect((await screen.findByRole("alert")).textContent).toContain("not stubbed");
});
