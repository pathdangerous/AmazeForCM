import { cleanup, fireEvent, screen, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { itemActions, mappingItem, renderWithTheme, tieItem, workspace } from "../../test/fixtures";
import MappingPanel from "./MappingPanel";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function show(items = [mappingItem(), tieItem()], actions = itemActions()) {
  const ws = workspace("mapping", "needs_decision", { items });
  renderWithTheme(<MappingPanel ws={ws} items={ws.items} actions={actions} />);
  return actions;
}

test("per column: the element chosen and its score", () => {
  show();
  const card = screen.getByTestId(`item-${mappingItem().proposal_id}`);
  expect(card.textContent).toContain("Ticker");
  expect(card.textContent).toContain("instrument_ticker");
  expect(card.textContent).toContain("Score 0.93");
});

test("7.22: the chosen element is a link to the element on the Glossary page, reachable by keyboard", () => {
  show();
  const card = screen.getByTestId(`item-${mappingItem().proposal_id}`);
  const link = within(card).getByRole("link", { name: "instrument_ticker" });
  expect(link.getAttribute("href")).toBe("#/glossary/instrument_ticker");
  expect(link.tabIndex).toBe(0);
});

test("a tie lists its candidates and decides through the select, with the key as the choice", () => {
  const actions = show();
  const card = screen.getByTestId(`item-${tieItem().proposal_id}`);
  expect(card.textContent).toContain("Tie between bid_price, ask_price");
  fireEvent.change(within(card).getByLabelText("Choice for Price"), { target: { value: "0" } });
  fireEvent.click(within(card).getByRole("button", { name: "Approve" }));
  expect(actions.decide).toHaveBeenCalledWith(expect.objectContaining({ proposal_id: tieItem().proposal_id }), "approved", "bid_price");
});

test("the pre-accepted mapping has no Approve; the tie is marked as needing a decision", () => {
  show();
  expect(within(screen.getByTestId(`item-${mappingItem().proposal_id}`)).queryByRole("button", { name: "Approve" })).toBeNull();
  expect(within(screen.getByTestId(`item-${tieItem().proposal_id}`)).getByText("needs decision")).toBeTruthy();
});

test("the edit form picks an element from the candidates and sends it", () => {
  const item = tieItem();
  const actions = show([item], itemActions({ editing: item.proposal_id }));
  fireEvent.change(screen.getByLabelText("Element"), { target: { value: "ask_price" } });
  fireEvent.click(screen.getByRole("button", { name: "Save edit" }));
  expect(actions.saveEdit).toHaveBeenCalledWith(expect.objectContaining({ proposal_id: item.proposal_id }),
                                                 expect.objectContaining({ candidates: ["ask_price"], recommended: "ask_price" }));
});

test("a decided mapping shows its decision and no buttons", () => {
  show([mappingItem({ status: "rejected", pre_accepted: false })]);
  expect(screen.getByTestId("item-badge-status").textContent).toBe("rejected");
  expect(screen.queryAllByRole("button").length).toBe(0);
});
