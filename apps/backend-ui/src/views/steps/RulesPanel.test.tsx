import { cleanup, fireEvent, screen, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { itemActions, renderWithTheme, ruleItem, workspace } from "../../test/fixtures";
import RulesPanel from "./RulesPanel";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function show(items = [ruleItem()], actions = itemActions()) {
  const ws = workspace("rules", "ready", { items });
  const view = renderWithTheme(<RulesPanel ws={ws} items={ws.items} actions={actions} />);
  return { actions, ...view };
}

test("a rule shows its key, description, SQL in a monospace block and its flags on this file", () => {
  show();
  const card = screen.getByTestId(`item-${ruleItem().proposal_id}`);
  expect(within(card).getByRole("heading", { name: "price_positive" })).toBeTruthy();
  expect(card.textContent).toContain("Price is above zero");
  const sql = within(card).getByTestId("rule-sql");
  expect(sql.tagName).toBe("PRE");
  expect(sql.textContent).toBe("SELECT _row FROM data WHERE price <= 0");
  expect(card.textContent).toContain("Flags 2 of 120 rows on this file");
});

test("a rule that was not back-tested on this file says so and never shows 'x of 0'", () => {
  const item = ruleItem({ payload: { ...ruleItem().payload, backtest: { status: "not_back_tested", total_rows: 0,
    flagged_rows: 0, share: null, deliveries: [] } } });
  const { container } = show([item]);
  expect(screen.getByText("Not back-tested on this file")).toBeTruthy();
  expect(container.textContent ?? "").not.toMatch(/\b\d+ of 0\b/);
});

test("a rule with no flags says it flags nothing", () => {
  const item = ruleItem({ payload: { ...ruleItem().payload, backtest: { status: "ran", total_rows: 120, flagged_rows: 0,
    share: 0, deliveries: [{ label: "current file", rows: 120, flagged: 0, excluded: null }] } } });
  show([item]);
  expect(screen.getByText("Flags nothing on this file (120 rows)")).toBeTruthy();
});

test("a rule needs a decision: Approve once; the edit form edits description and SQL", () => {
  const item = ruleItem({ pre_accepted: false, needs_decision: true });
  const { actions } = show([item]);
  fireEvent.click(screen.getByRole("button", { name: "Approve" }));
  expect(actions.decide).toHaveBeenCalledTimes(1);
  cleanup();
  const editing = itemActions({ editing: item.proposal_id });
  show([item], editing);
  fireEvent.change(screen.getByLabelText("Description"), { target: { value: "Price is positive" } });
  fireEvent.click(screen.getByRole("button", { name: "Save edit" }));
  expect(editing.saveEdit).toHaveBeenCalledWith(expect.objectContaining({ proposal_id: item.proposal_id }),
    expect.objectContaining({ description: "Price is positive", sql: "SELECT _row FROM data WHERE price <= 0" }));
});

test("a rule waiting for a second person shows it", () => {
  show([ruleItem({ pre_accepted: false, needs_decision: false, waiting_for: "a second person" })]);
  expect(screen.getByText("Waiting for a second person")).toBeTruthy();
});
