import { cleanup, fireEvent, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import AgentPanel from "../../ui-shared/AgentPanel";
import { ConfidenceBar, CssDonut, KpiCard, SectionCard, StatusChip, StickyFooter } from "../../ui-shared/phase711";
import { renderWithTheme } from "./test/fixtures";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

test("KPI and section cards render caller-supplied facts and accessible headings without invented trends", () => {
  renderWithTheme(<>
    <KpiCard label="Feeds live" value={0} detail="No registered feeds" />
    <SectionCard title="Waiting for your decision"><p>No pending proposals</p></SectionCard>
  </>);
  expect(screen.getByText("Feeds live")).toBeTruthy();
  expect(screen.getByText("0")).toBeTruthy();
  expect(screen.getByText("No registered feeds")).toBeTruthy();
  expect(screen.getByRole("heading", { name: "Waiting for your decision" })).toBeTruthy();
  expect(screen.getByText("No pending proposals")).toBeTruthy();
  expect(screen.queryByText(/last week|↑|↓/)).toBeNull();
});

test("status chips carry readable text so status never depends on color alone", () => {
  renderWithTheme(<>
    <StatusChip tone="good">Released</StatusChip>
    <StatusChip tone="attention">Held</StatusChip>
    <StatusChip tone="neutral">Quarantined</StatusChip>
  </>);
  for (const label of ["Released", "Held", "Quarantined"]) expect(screen.getByText(label)).toBeTruthy();
});

test("confidence is a named accessible meter, with unknown distinct from zero", () => {
  renderWithTheme(<>
    <ConfidenceBar value={0.62} label="Mapping confidence" />
    <ConfidenceBar value={0} label="Zero confidence" />
    <ConfidenceBar value={null} label="Unknown confidence" />
  </>);
  const meter = screen.getByRole("meter", { name: "Mapping confidence" });
  expect(Number(meter.getAttribute("aria-valuenow"))).toBe(62);
  expect(Number(meter.getAttribute("aria-valuemin"))).toBe(0);
  expect(Number(meter.getAttribute("aria-valuemax"))).toBe(100);
  expect(screen.getByRole("meter", { name: "Zero confidence" }).getAttribute("aria-valuenow")).toBe("0");
  expect(screen.queryByRole("meter", { name: "Unknown confidence" })).toBeNull();
  expect(screen.getByLabelText("Unknown confidence").textContent).toMatch(/—|not recorded|unknown/i);
});

test("CSS donut has a factual accessible name and no fabricated percentage for missing data", () => {
  const view = renderWithTheme(<CssDonut value={73.4} label="Rows passing all rules" />);
  expect(screen.getByRole("img", { name: /Rows passing all rules.*73.4/ })).toBeTruthy();
  expect(view.container.querySelector("canvas")).toBeNull();
  cleanup();
  renderWithTheme(<CssDonut value={null} label="Trust score" />);
  expect(screen.queryByText("0%")).toBeNull();
  expect(screen.getByLabelText(/Trust score.*(not recorded|unavailable|unknown)/i)).toBeTruthy();
});

test("sticky footer preserves caller-owned controls, accessible names and test IDs", () => {
  const approve = vi.fn();
  renderWithTheme(<StickyFooter>
    <span role="status">No outstanding decisions</span>
    <button data-testid="workspace-approve" onClick={approve}>Approve &amp; next</button>
    <button data-testid="workspace-back" disabled>Back</button>
  </StickyFooter>);
  fireEvent.click(screen.getByRole("button", { name: "Approve & next" }));
  expect(approve).toHaveBeenCalledTimes(1);
  expect(screen.getByTestId("workspace-back")).toHaveProperty("disabled", true);
  expect(screen.getByRole("status").textContent).toBe("No outstanding decisions");
});

test("shared agent panel preserves its existing interface and never substitutes mockup agent facts", () => {
  renderWithTheme(
    <AgentPanel step="mapping" items={[]} notes={[]} inputs={null} timeline={[]} people={{}} />,
  );
  const panel = screen.getByRole("complementary", { name: "What the agent did and why" });
  expect(screen.getByTestId("agent-panel")).toBe(panel);
  expect(panel.textContent).toContain("The agent has nothing to explain for this step yet.");
  expect(panel.textContent).not.toMatch(/Azure OpenAI|4,812|4\.2 s|26 scored/);
  fireEvent.click(screen.getByRole("tab", { name: "Timeline" }));
  expect(screen.getByTestId("agent-timeline").textContent).toContain("No events yet.");
});
