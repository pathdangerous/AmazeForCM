import { cleanup, fireEvent, screen, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { aliasItem, conflictItem, glossaryItem, itemActions, renderWithTheme, workspace } from "../../test/fixtures";
import type { ItemActions } from "./shared";
import GlossaryPanel from "./GlossaryPanel";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function show(items = [glossaryItem(), aliasItem(), conflictItem()], actions: ItemActions = itemActions()) {
  const ws = workspace("glossary", "needs_decision", { items });
  const view = renderWithTheme(<GlossaryPanel ws={ws} items={ws.items} actions={actions} />);
  return { actions, ...view };
}

test("a proposed element shows key, name, domain, type, unit and definition", () => {
  show();
  const card = screen.getByTestId(`item-${glossaryItem().proposal_id}`);
  const text = card.textContent ?? "";
  for (const part of ["Coupon", "coupon_rate", "Coupon rate", "reference", "decimal", "percent", "The annual coupon as a percentage."]) {
    expect(text).toContain(part);
  }
});

test("an alias shows the element it names", () => {
  show();
  const text = screen.getByTestId(`item-${aliasItem().proposal_id}`).textContent ?? "";
  expect(text).toContain("Alias ccy of currency_code");
});

test("a conflict lists what it clashes with and decides through a native select of its options", () => {
  const { actions } = show();
  const card = screen.getByTestId(`item-${conflictItem().proposal_id}`);
  expect(card.textContent).toContain("coupon_rate, yield_rate");
  const select = within(card).getByLabelText("Choice for Rate") as HTMLSelectElement;
  expect(select.tagName).toBe("SELECT");
  expect(Array.from(select.options).map((o) => o.text)).toEqual(
    ["Choose an option", "Alias rate of coupon_rate", "Alias rate of yield_rate"]);
  fireEvent.change(select, { target: { value: "1" } });
  fireEvent.click(within(card).getByRole("button", { name: "Approve" }));
  expect(actions.decide).toHaveBeenCalledWith(expect.objectContaining({ proposal_id: conflictItem().proposal_id }), "approved", 1);
});

test("items that need a decision are marked; the pre-accepted ones say so and have no Approve", () => {
  show();
  const pre = screen.getByTestId(`item-${glossaryItem().proposal_id}`);
  expect(within(pre).getByText("Pre-accepted")).toBeTruthy();
  expect(within(pre).queryByRole("button", { name: "Approve" })).toBeNull();
  expect(within(screen.getByTestId(`item-${conflictItem().proposal_id}`)).getByText("needs decision")).toBeTruthy();
});

test("Edit starts an edit of that item", () => {
  const { actions } = show();
  fireEvent.click(within(screen.getByTestId(`item-${glossaryItem().proposal_id}`)).getByRole("button", { name: "Edit" }));
  expect(actions.startEdit).toHaveBeenCalledWith(expect.objectContaining({ proposal_id: glossaryItem().proposal_id }));
});

test("the edit form of a new element sends the edited payload", () => {
  const item = glossaryItem();
  const actions = itemActions({ editing: item.proposal_id });
  show([item], actions);
  fireEvent.change(screen.getByLabelText("Definition"), { target: { value: "The coupon as a percentage of face value." } });
  fireEvent.click(screen.getByRole("button", { name: "Save edit" }));
  expect(actions.saveEdit).toHaveBeenCalledWith(
    expect.objectContaining({ proposal_id: item.proposal_id }),
    expect.objectContaining({ options: [expect.objectContaining({ element: expect.objectContaining({
      definition: "The coupon as a percentage of face value." }) })] }));
});

test("a read-only (done) step shows the proposals with no buttons", () => {
  show([glossaryItem({ status: "approved", pre_accepted: false }), conflictItem({ status: "approved", needs_decision: false })],
       itemActions({ readOnly: true }));
  expect(screen.queryAllByRole("button").length).toBe(0);
  expect(screen.getAllByTestId("item-badge-status").length).toBe(2);
});

test("an empty step says there is nothing to decide, and no line says 'x of 0'", () => {
  const { container } = show([]);
  expect(screen.getByText("No new elements were proposed for this file.")).toBeTruthy();
  expect(container.textContent ?? "").not.toMatch(/\b\d+ of 0\b/);
});
