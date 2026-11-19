import { cleanup, fireEvent, screen, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import type { AgentNote, StepInputs } from "../api";
import { onboardingItem, renderWithTheme, stepInputs, TIMELINE, workspace } from "../test/fixtures";
import AgentPanel from "./AgentPanel";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const second = onboardingItem({ proposal_id: "00000000-0000-4000-8000-0000000000e2", column: "Name", confidence: 0.5,
                                summary: "Name -> instrument_name", reason: "Name is a weaker match." });

function panel(opts: { timeline?: typeof TIMELINE; inputs?: StepInputs | null; notes?: AgentNote[] } = {}) {
  const ws = workspace("mapping");
  const notes = opts.notes ?? [...ws.agent_notes, { ...ws.agent_notes[0], proposal_id: second.proposal_id }];
  return renderWithTheme(
    <AgentPanel step="mapping" items={[onboardingItem(), second]} notes={notes} inputs={opts.inputs === undefined ? stepInputs() : opts.inputs}
                timeline={opts.timeline ?? TIMELINE} people={{ "dev-backend-team": "Blake Backend" }} />);
}

test("the model meta (agent, model, prompt version) is shown once, not per item", () => {
  panel();
  const region = screen.getByTestId("agent-panel");
  expect(region.textContent).toContain("What the agent did and why");
  expect(region.textContent).toContain("MappingAgent");
  expect((region.textContent ?? "").split("MappingAgent").length).toBe(2);
  expect(region.textContent).toContain("mock-1");
  expect(region.textContent).toContain("v2");
});

test("per item: the column, the decision proposed, the confidence as a percentage and the reason", () => {
  panel();
  const list = screen.getByRole("list", { name: "Proposals and why" });
  const items = within(list).getAllByRole("listitem");
  expect(items.length).toBe(2);
  expect(items[0].textContent).toContain("Ticker");
  expect(items[0].textContent).toContain("-> instrument_ticker");
  expect(items[0].textContent).toContain("93%");
  expect(items[0].textContent).toContain("The name matches the glossary element exactly.");
  expect(items[1].textContent).toContain("50%");
  expect(items[1].textContent).toContain("Name is a weaker match.");
});

test("an item without a reason falls back to the logged reasoning, and one without confidence has no percentage", () => {
  const ws = workspace("mapping");
  renderWithTheme(<AgentPanel step="mapping" items={[onboardingItem({ reason: null, confidence: null })]} notes={ws.agent_notes}
                              inputs={null} timeline={[]} people={{}} />);
  const item = within(screen.getByRole("list", { name: "Proposals and why" })).getByRole("listitem");
  expect(item.textContent).toContain("Ticker is a close match for the glossary element.");
  expect(item.textContent).not.toContain("%");
});

test("Inputs it saw lists the counts, the columns, the issues, the candidate counts and the facts", () => {
  panel({ inputs: stepInputs({
    row_count: 120, column_count: 3, columns: ["Ticker", "Name", "Currency"],
    issues: [{ kind: "placeholders", columns: ["Currency"], count: 7, detail: "x" }],
    candidate_counts: { Ticker: 3, Name: 5 }, facts: [{ label: "Mapped columns", value: "6" }],
  }) });
  expect(screen.getByText("Inputs it saw")).toBeTruthy();
  const list = screen.getByRole("list", { name: "Inputs it saw" });
  const text = Array.from(list.querySelectorAll("li")).map((li) => li.textContent);
  expect(text).toEqual(expect.arrayContaining([
    "Rows: 120", "Columns: 3", "Column names: Ticker, Name, Currency", "Issues found: 1",
    "Candidates for Ticker: 3", "Candidates for Name: 5", "Mapped columns: 6"]));
});

test("a count of nothing is left out: no 'Issues found: 0', no 'x of 0'", () => {
  const { container } = panel({ inputs: stepInputs({ row_count: 0, issues: [], candidate_counts: { Ticker: 0 } }) });
  expect(container.textContent ?? "").not.toContain("Issues found");
  expect(container.textContent ?? "").not.toContain("Rows: 0");
  expect(container.textContent ?? "").not.toMatch(/\b\d+ of 0\b/);
});

test("no sample values: only what the inputs hold is listed", () => {
  panel();
  const text = screen.getByRole("list", { name: "Inputs it saw" }).textContent ?? "";
  expect(text).not.toContain("input:");
});

test("the Timeline tab lists who did what and when, with the editor", () => {
  panel();
  fireEvent.click(screen.getByRole("tab", { name: "Timeline" }));
  const list = screen.getByTestId("agent-timeline");
  expect(list.textContent).toContain("Agent finished");
  expect(list.textContent).toContain("Edited Ticker mapping");
  expect(list.textContent).toContain("edited by Blake Backend");
});

test.each(["first_load", "product_ready"] as const)("step %s says no agent runs there", (step) => {
  renderWithTheme(<AgentPanel step={step} items={[]} notes={[]} inputs={null} timeline={[]} people={{}} />);
  expect(screen.getByText("No agent runs at this step: rules and the trust score are computed")).toBeTruthy();
  expect(screen.queryByText("The agent has nothing to explain for this step yet.")).toBeNull();
});

test("with nothing to explain it says so", () => {
  renderWithTheme(<AgentPanel step="profile" items={[]} notes={[]} inputs={null} timeline={[]} people={{}} />);
  expect(screen.getByText("The agent has nothing to explain for this step yet.")).toBeTruthy();
});
