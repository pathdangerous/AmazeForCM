import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { clearElementCache } from "../../../ui-shared/ElementLink";
import ElementLink from "../../../ui-shared/ElementLink";
import { renderWithTheme, stubFetch } from "../test/fixtures";

const PATH = "GET /api/amaze/glossary/elements/coupon_rate";
const detail = (over: Record<string, unknown> = {}) => ({
  element: { id: "e1", created_at: "2026-11-01T00:00:00Z", key: "coupon_rate", version: 2, name: "Coupon rate", domain: "reference",
             data_type: "decimal", definition: "The annual coupon as a percentage of the face value.", aliases: ["coupon", "cpn"],
             unit: "percent", ...over },
  history: [],
});

beforeEach(() => clearElementCache());
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function show(label?: string) {
  return renderWithTheme(<ElementLink elementKey="coupon_rate" devUser="dev-operator" label={label} />);
}

test("the element name is a real link to the element on the Glossary page", () => {
  stubFetch({ [PATH]: detail() });
  show();
  const link = screen.getByRole("link", { name: "coupon_rate" });
  expect(link.getAttribute("href")).toBe("#/glossary/coupon_rate");
});

test("hovering the link shows name, definition, data type and aliases, and leaving hides them", async () => {
  stubFetch({ [PATH]: detail() });
  show();
  const link = screen.getByRole("link", { name: "coupon_rate" });
  fireEvent.mouseEnter(link);
  const card = await screen.findByRole("tooltip");
  for (const part of ["Coupon rate", "The annual coupon as a percentage of the face value.", "decimal", "coupon", "cpn"]) {
    expect(card.textContent).toContain(part);
  }
  expect(link.getAttribute("aria-describedby")).toBe(card.id);
  fireEvent.mouseLeave(link);
  expect(screen.queryByRole("tooltip")).toBeNull();
});

test("keyboard focus shows the same card, and Escape closes it without leaving the link", async () => {
  stubFetch({ [PATH]: detail() });
  show();
  const link = screen.getByRole("link", { name: "coupon_rate" });
  link.focus();
  fireEvent.focus(link);
  expect((await screen.findByRole("tooltip")).textContent).toContain("Coupon rate");
  fireEvent.keyDown(link, { key: "Escape" });
  expect(screen.queryByRole("tooltip")).toBeNull();
  expect(document.activeElement).toBe(link);
  fireEvent.focus(link);
  await screen.findByRole("tooltip");
  fireEvent.blur(link);
  expect(screen.queryByRole("tooltip")).toBeNull();
});

test("the definition is read once per element, however often the card opens", async () => {
  const fetchStub = stubFetch({ [PATH]: detail() });
  show();
  const link = screen.getByRole("link", { name: "coupon_rate" });
  for (let i = 0; i < 3; i++) {
    fireEvent.mouseEnter(link);
    await screen.findByRole("tooltip");
    fireEvent.mouseLeave(link);
  }
  expect(fetchStub.mock.calls.filter(([u]) => String(u).includes("/glossary/elements/coupon_rate")).length).toBe(1);
});

test("an element with no aliases says so, and a label can replace the key as the link text", async () => {
  stubFetch({ [PATH]: detail({ aliases: [] }) });
  show("Coupon rate (decimal)");
  fireEvent.mouseEnter(screen.getByRole("link", { name: "Coupon rate (decimal)" }));
  expect((await screen.findByRole("tooltip")).textContent).toContain("No other names");
});

test("when the definition cannot be read the card says so, and the link still works", async () => {
  stubFetch({});
  show();
  const link = screen.getByRole("link", { name: "coupon_rate" });
  fireEvent.mouseEnter(link);
  await waitFor(() => expect(screen.getByRole("tooltip").textContent).toContain("could not be read"));
  expect(link.getAttribute("href")).toBe("#/glossary/coupon_rate");
});
