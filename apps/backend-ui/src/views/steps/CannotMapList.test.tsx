import { cleanup, fireEvent, screen, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { ONBOARDING_STEPS } from "../../api";
import { cannotMapColumn, itemActions, mappingItem, renderWithTheme, workspace } from "../../test/fixtures";
import type { ItemActions } from "./shared";
import MappingPanel from "./MappingPanel";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function show(cannotMap = [cannotMapColumn(), cannotMapColumn({ column: "Sector", reason: "type_does_not_fit" })],
              actions: ItemActions = itemActions({ cannotMap: vi.fn() })) {
  const ws = workspace("mapping", "ready", { items: [mappingItem()], cannot_map: cannotMap, can_approve: false,
                                              blocked_by: "2 columns cannot be mapped" });
  renderWithTheme(<MappingPanel ws={ws} items={ws.items} actions={actions} />);
  return actions;
}

test("the order of the steps is mapping, then the glossary requests", () => {
  expect(ONBOARDING_STEPS.slice(0, 4)).toEqual(["upload", "profile", "mapping", "glossary"]);
});

test("a column no element fits is listed as cannot map, with its reason in words, and what to do", () => {
  show();
  const list = screen.getByTestId("cannot-map");
  expect(within(list).getByText(/Cannot map: no glossary element fits/)).toBeTruthy();
  const row = within(list).getByTestId("cannot-map-Coupon");
  expect(row.textContent).toContain("No element is close enough");
  expect(within(list).getByTestId("cannot-map-Sector").textContent).toContain("The type does not fit any element");
  expect(within(row).getByText("Undecided")).toBeTruthy(); // a word, not only a colour
  expect(within(row).getByRole("button", { name: "Ignore Coupon" })).toBeTruthy();
});

test("Ignore sends that one column; Ignore all undecided sends every undecided column", () => {
  const actions = show();
  fireEvent.click(screen.getByRole("button", { name: "Ignore Coupon" }));
  expect(actions.cannotMap).toHaveBeenCalledWith("ignore", ["Coupon"]);
  fireEvent.click(screen.getByRole("button", { name: "Ignore all undecided" }));
  expect(actions.cannotMap).toHaveBeenLastCalledWith("ignore", ["Coupon", "Sector"]);
});

test("Request glossary element sends that column; Request all undecided sends every undecided column", () => {
  const actions = show();
  fireEvent.click(screen.getByRole("button", { name: "Request glossary element for Sector" }));
  expect(actions.cannotMap).toHaveBeenCalledWith("request", ["Sector"]);
  fireEvent.click(screen.getByRole("button", { name: "Request all undecided" }));
  expect(actions.cannotMap).toHaveBeenLastCalledWith("request", ["Coupon", "Sector"]);
});

test("a requested column says where its request stands, in words, and has no buttons", () => {
  show([cannotMapColumn({ state: "requested", glossary_status: "pending" }),
        cannotMapColumn({ column: "Sector", state: "requested", glossary_status: "approved" }),
        cannotMapColumn({ column: "Region", state: "requested", glossary_status: "drafting" })]);
  expect(screen.getByTestId("cannot-map-Coupon").textContent).toContain("Glossary element requested: waiting for the glossary owner");
  expect(screen.getByTestId("cannot-map-Sector").textContent).toContain("approved, its mapping waits for the steward");
  expect(screen.getByTestId("cannot-map-Region").textContent).toContain("being drafted");
  expect(within(screen.getByTestId("cannot-map-Coupon")).queryByRole("button")).toBeNull();
});

test("a request the owner rejected can only be ignored", () => {
  show([cannotMapColumn({ glossary_status: "rejected" })]);
  const row = screen.getByTestId("cannot-map-Coupon");
  expect(row.textContent).toContain("request was rejected");
  expect(within(row).queryByRole("button", { name: /Request glossary element/ })).toBeNull();
  expect(within(row).getByRole("button", { name: "Ignore Coupon" })).toBeTruthy();
});

test("an ignored column says it is left out and has no buttons", () => {
  show([cannotMapColumn({ state: "ignored" })]);
  const row = screen.getByTestId("cannot-map-Coupon");
  expect(row.textContent).toContain("Ignored: left out, not loaded");
  expect(within(row).queryByRole("button")).toBeNull();
  expect(screen.queryByRole("button", { name: "Ignore all undecided" })).toBeNull();
});

test("a read only view shows the list without buttons", () => {
  show(undefined, itemActions({ readOnly: true, cannotMap: vi.fn() }));
  expect(screen.getByTestId("cannot-map-Coupon")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Ignore Coupon" })).toBeNull();
});

test("no cannot-map columns, no list", () => {
  show([]);
  expect(screen.queryByTestId("cannot-map")).toBeNull();
});
