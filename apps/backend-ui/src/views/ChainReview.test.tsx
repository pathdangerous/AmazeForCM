import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import type { ChainReview as Chain } from "../api";
import { glossaryItem, mappingItem, renderWithTheme, ruleItem, SESSION_ID, stubFetch, tieItem, transformationItem,
  WS_PATH } from "../test/fixtures";
import ChainReview from "./ChainReview";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const glossary = glossaryItem({ confidence: 1 });
const onDraft = mappingItem({ confidence: 0.93 });
const tie = tieItem({ confidence: null });
const doubted = transformationItem({ proposal_id: "00000000-0000-4000-8000-0000000000t2", confidence: 0.5 });
const chain = (over: Partial<Chain> = {}): Chain => ({
  run_mode: "chain", current_step: "glossary", auto_accept_confidence: 0.85, blocked: { Price: "a mapping tie for the data steward" },
  steps: [
    { step: "glossary", state: "ready", items: [{ item: glossary, based_on: [], blocked: false, low_confidence: false, confident: true }] },
    { step: "mapping", state: "needs_decision", items: [
      { item: tie, based_on: [], blocked: true, low_confidence: false, confident: false },
      { item: onDraft, based_on: [{ proposal_id: glossary.proposal_id, step: "glossary", column: "Coupon", status: "pending" }],
        blocked: false, low_confidence: false, confident: false }] },
    { step: "transformations", state: "ready", items: [
      { item: doubted, based_on: [], blocked: false, low_confidence: true, confident: false }] },
    { step: "rules", state: "ready", items: [{ item: ruleItem(), based_on: [], blocked: false, low_confidence: false, confident: true }] },
  ], ...over,
});

const show = (onOpenStep = vi.fn()) => {
  renderWithTheme(<ChainReview id={SESSION_ID} devUser="dev-operator" stamp="1" onOpenStep={onOpenStep} onChanged={vi.fn()} />);
  return onOpenStep;
};

test("it lists every draft by step, blocked and low confidence flagged, with what each was built on", async () => {
  stubFetch({ [`GET ${WS_PATH}/chain`]: chain() });
  const open = show();
  const mapping = await screen.findByTestId("chain-step-mapping");
  const rows = within(mapping).getAllByTestId(/^chain-item-/);
  expect(rows[0].getAttribute("data-testid")).toBe(`chain-item-${tie.proposal_id}`);
  expect(within(rows[0]).getByText("Blocked")).toBeTruthy();
  expect(within(screen.getByTestId("chain-step-transformations")).getByText("Low confidence")).toBeTruthy();
  expect(screen.getByTestId("chain-blocked").textContent).toContain("Price");
  fireEvent.click(within(rows[1]).getByRole("button", { name: "Based on Glossary: Coupon" }));
  expect(open).toHaveBeenCalledWith("glossary");
});

test("7.20: a rule of the chain has a button that shows its rows; other steps' items do not", async () => {
  const id = ruleItem().proposal_id;
  stubFetch({ [`GET ${WS_PATH}/chain`]: chain(),
              [`GET /api/amaze/rules/${id}/rows?failing_only=false&page=1`]: { outcome: "no_file", reason: "No file yet." } });
  show();
  const rules = await screen.findByTestId("chain-step-rules");
  fireEvent.click(within(rules).getByRole("button", { name: "Show rows of price_positive" }));
  expect(await screen.findByText("No file yet.")).toBeTruthy();
  expect(within(screen.getByTestId("chain-step-mapping")).queryByRole("button", { name: /Show rows/ })).toBeNull();
});

test("Approve all confident sends the step and shows the new chain", async () => {
  const after = chain({ steps: chain().steps.map((s) => s.step === "glossary"
    ? { ...s, items: s.items.map((i) => ({ ...i, confident: false, item: { ...i.item, status: "approved" } })) } : s) });
  const fetched = stubFetch({ [`GET ${WS_PATH}/chain`]: chain(),
                              [`POST ${WS_PATH}/chain/approve-confident`]: { step: "glossary", approved: [glossary.proposal_id], chain: after } });
  show();
  const button = (await screen.findByTestId("chain-approve-confident-glossary")) as HTMLButtonElement;
  expect(button.textContent).toBe("Approve all confident (1)");
  expect((screen.getByTestId("chain-approve-confident-mapping") as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(button);
  await waitFor(() => expect((screen.getByTestId("chain-approve-confident-glossary") as HTMLButtonElement).disabled).toBe(true));
  expect(fetched).toHaveBeenCalledWith(`${WS_PATH}/chain/approve-confident`, expect.objectContaining({
    method: "POST", body: JSON.stringify({ step: "glossary" }) }));
});

test("a rule draft says which transformation version it was tested on, and when a newer one exists (7.14, G1)", async () => {
  const rule = ruleItem();
  stubFetch({ [`GET ${WS_PATH}/chain`]: chain({ steps: [{ step: "rules", state: "ready", items: [{
    item: rule, based_on: [], blocked: false, low_confidence: false, confident: true,
    tested_on: [{ column: "instr_id", version: 1, current_version: 2 }, { column: "px", version: null, current_version: null }],
  }] }] }) });
  show();
  const row = await screen.findByTestId(`chain-item-${rule.proposal_id}`);
  expect(row.textContent).toContain("tested on transformation version 1 of instr_id (now version 2)");
  expect(row.textContent).toContain("tested on the draft transformation of px");
});
