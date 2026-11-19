import { cleanup, fireEvent, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { itemActions, mappingItem, onboardingItem, renderWithTheme, tieItem } from "../../test/fixtures";
import ItemCard from "./ItemCard";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const CHOICES = [{ value: "bid_price", label: "bid_price" }, { value: "ask_price", label: "ask_price" }];

test("an item that needs a decision shows Approve, Reject and Edit once, and Approve decides", () => {
  const actions = itemActions();
  renderWithTheme(<ItemCard item={mappingItem({ pre_accepted: false, needs_decision: true })} actions={actions}>body</ItemCard>);
  expect(screen.getAllByRole("button", { name: "Approve" }).length).toBe(1);
  expect(screen.getAllByRole("button", { name: "Reject" }).length).toBe(1);
  expect(screen.getAllByRole("button", { name: "Edit" }).length).toBe(1);
  fireEvent.click(screen.getByRole("button", { name: "Approve" }));
  expect(actions.decide).toHaveBeenCalledTimes(1);
  expect(actions.decide).toHaveBeenCalledWith(expect.objectContaining({ proposal_id: mappingItem().proposal_id }), "approved", undefined);
});

test("a tie asks for a choice from a native select; Approve is disabled until one is made", () => {
  const actions = itemActions();
  renderWithTheme(<ItemCard item={tieItem()} actions={actions} choices={CHOICES}>body</ItemCard>);
  const select = screen.getByLabelText("Choice for Price") as HTMLSelectElement;
  expect(select.tagName).toBe("SELECT");
  const approve = screen.getByRole("button", { name: "Approve" }) as HTMLButtonElement;
  expect(approve.disabled).toBe(true);
  fireEvent.change(select, { target: { value: "1" } });
  expect(approve.disabled).toBe(false);
  fireEvent.click(approve);
  expect(actions.decide).toHaveBeenCalledWith(expect.anything(), "approved", "ask_price");
});

test("a pre-accepted item has the badge, Reject and Edit, and no Approve", () => {
  const actions = itemActions();
  renderWithTheme(<ItemCard item={mappingItem()} actions={actions}>body</ItemCard>);
  expect(screen.getByText("Pre-accepted")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
  expect(screen.getByRole("button", { name: "Reject" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Edit" })).toBeTruthy();
});

// A rejection needs a note (the API refuses one without it: NoteNeeded, 422). Reject opens the note; Confirm sends it.
function rejectWith(note: string | null) {
  const actions = itemActions();
  renderWithTheme(<ItemCard item={mappingItem({ pre_accepted: false, needs_decision: true })} actions={actions}>body</ItemCard>);
  fireEvent.click(screen.getByRole("button", { name: "Reject" }));
  const box = screen.getByLabelText("Why it is rejected") as HTMLTextAreaElement;
  const confirm = screen.getByRole("button", { name: "Confirm" }) as HTMLButtonElement;
  if (note !== null) fireEvent.change(box, { target: { value: note } });
  return { actions, box, confirm };
}

test("Reject opens a note box; nothing is decided yet, and Confirm is disabled until the note is not blank", () => {
  const { actions, box, confirm } = rejectWith(null);
  expect(box.tagName).toBe("TEXTAREA");
  expect(actions.decide).not.toHaveBeenCalled();
  expect(confirm.disabled).toBe(true);
  fireEvent.change(box, { target: { value: "   " } });
  expect(confirm.disabled).toBe(true);
  fireEvent.change(box, { target: { value: "Flags too many rows" } });
  expect(confirm.disabled).toBe(false);
});

test("Confirm sends the rejection with the note", () => {
  const { actions, confirm } = rejectWith("Flags too many rows");
  fireEvent.click(confirm);
  expect(actions.decide).toHaveBeenCalledTimes(1);
  expect(actions.decide).toHaveBeenCalledWith(expect.objectContaining({ proposal_id: mappingItem().proposal_id }),
                                              "rejected", undefined, "Flags too many rows");
});

test("a pre-accepted item rejects with the same note box", () => {
  const actions = itemActions();
  renderWithTheme(<ItemCard item={mappingItem()} actions={actions}>body</ItemCard>);
  fireEvent.click(screen.getByRole("button", { name: "Reject" }));
  const confirm = screen.getByRole("button", { name: "Confirm" }) as HTMLButtonElement;
  expect(confirm.disabled).toBe(true);
  fireEvent.change(screen.getByLabelText("Why it is rejected"), { target: { value: "A tenor, not a date" } });
  fireEvent.click(confirm);
  expect(actions.decide).toHaveBeenCalledWith(expect.anything(), "rejected", undefined, "A tenor, not a date");
});

test("Cancel closes the note box and decides nothing", () => {
  const { actions } = rejectWith("x");
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(screen.queryByLabelText("Why it is rejected")).toBeNull();
  expect(screen.getByRole("button", { name: "Reject" })).toBeTruthy();
  expect(actions.decide).not.toHaveBeenCalled();
});

test("a decided item shows its decision and no buttons", () => {
  renderWithTheme(<ItemCard item={mappingItem({ status: "approved", edited_by: "dev-backend-team" })} actions={itemActions()}>body</ItemCard>);
  expect(screen.getByTestId("item-badge-status").textContent).toBe("approved");
  expect(screen.queryAllByRole("button").length).toBe(0);
});

test("a decided item says who decided it, by display name", () => {
  const people = { "dev-operator": "Olivia Operator" };
  renderWithTheme(<ItemCard item={mappingItem({ status: "approved", decided_by: "dev-operator", decided_at: "2026-11-19T10:00:00Z" })}
                            actions={itemActions({ people })}>body</ItemCard>);
  expect(screen.getByTestId("item-decider").textContent).toBe("approved by Olivia Operator");
  cleanup();
  renderWithTheme(<ItemCard item={mappingItem({ status: "rejected", decided_by: "someone" })} actions={itemActions()}>body</ItemCard>);
  expect(screen.getByTestId("item-decider").textContent).toBe("rejected by someone");
  cleanup();
  renderWithTheme(<ItemCard item={mappingItem()} actions={itemActions({ people })}>body</ItemCard>);
  expect(screen.queryByTestId("item-decider")).toBeNull();
});

test("a read-only view shows no buttons even for a pending item", () => {
  renderWithTheme(<ItemCard item={onboardingItem({ pre_accepted: false, needs_decision: true })} actions={itemActions({ readOnly: true })}>body</ItemCard>);
  expect(screen.queryAllByRole("button").length).toBe(0);
});

test("a marked item carries the mark and the needs-decision badge", () => {
  const item = tieItem();
  renderWithTheme(<ItemCard item={item} actions={itemActions({ marked: [item.proposal_id] })} choices={CHOICES}>body</ItemCard>);
  expect(screen.getByTestId(`item-${item.proposal_id}`).getAttribute("data-marked")).toBe("true");
  expect(screen.getByText("needs decision", { selector: "[data-testid^=item-badge]" })).toBeTruthy();
});

test("an item that waits for a second person has no decision buttons", () => {
  renderWithTheme(<ItemCard item={mappingItem({ pre_accepted: false, needs_decision: false, waiting_for: "a second person" })}
                            actions={itemActions()}>body</ItemCard>);
  expect(screen.getByText("Waiting for a second person")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
});

test("while editing, the kind's form shows instead of the buttons", () => {
  const item = mappingItem();
  renderWithTheme(<ItemCard item={item} actions={itemActions({ editing: item.proposal_id })}>body</ItemCard>);
  expect(screen.getByRole("button", { name: "Save edit" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Reject" })).toBeNull();
});
