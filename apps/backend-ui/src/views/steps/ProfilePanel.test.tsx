import { cleanup, screen, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { itemActions, profileItem, renderWithTheme, stepInputs, workspace } from "../../test/fixtures";
import ProfilePanel from "./ProfilePanel";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const inputs = stepInputs({
  row_count: 120, column_count: 3, key_columns: ["Ticker"], duplicate_key_count: 4,
  issues: [{ kind: "placeholders", columns: ["Currency"], count: 7, detail: "7 values are N/A." },
           { kind: "duplicate_key", columns: ["Ticker"], count: 4, detail: "4 rows repeat a key." }],
});

function show(over = {}, item = profileItem()) {
  const ws = workspace("profile", "ready", { inputs, items: [item], ...over });
  return renderWithTheme(<ProfilePanel ws={ws} items={ws.items} actions={itemActions()} />);
}

test("the figures are stat figures: rows, columns and duplicate keys", () => {
  show();
  const figures = screen.getByRole("list", { name: "Profile figures" });
  expect(within(figures).getByText("Rows").nextSibling?.textContent).toBe("120");
  expect(within(figures).getByText("Columns").nextSibling?.textContent).toBe("3");
  expect(within(figures).getByText("Duplicate keys").nextSibling?.textContent).toBe("4");
});

test("a figure the API does not give is left out", () => {
  show({ inputs: stepInputs({ row_count: 120, column_count: null, duplicate_key_count: null }) });
  expect(screen.queryByText("Columns")).toBeNull();
  expect(screen.queryByText("Duplicate keys")).toBeNull();
});

test("the proposed key shows its columns and reason", () => {
  show();
  const key = screen.getByTestId("profile-key");
  expect(key.textContent).toContain("Ticker");
  expect(key.textContent).toContain("Ticker is unique per row.");
});

test("the issues are a table: kind in words, columns, rows affected, detail", () => {
  show();
  const table = screen.getByRole("table", { name: "Issues found in the file" });
  const rows = within(table).getAllByRole("row");
  expect(rows.length).toBe(3);
  expect(rows[1].textContent).toContain("Placeholder values");
  expect(rows[1].textContent).toContain("Currency");
  expect(rows[1].textContent).toContain("7");
  expect(rows[1].textContent).toContain("7 values are N/A.");
  expect(rows[2].textContent).toContain("Repeated key");
});

test("no issues: no table, a sentence instead", () => {
  show({ inputs: stepInputs() });
  expect(screen.queryByRole("table")).toBeNull();
  expect(screen.getByText("No issues found in the file.")).toBeTruthy();
});

test("the summary sentence and the recommended outcome with its gaps", () => {
  show();
  expect(screen.getByText("120 rows and 3 columns; Currency holds a placeholder.")).toBeTruthy();
  const outcome = screen.getByTestId("profile-outcome");
  expect(outcome.textContent).toContain("Meets with gaps");
  expect(within(outcome).getByText("Currency has placeholders")).toBeTruthy();
});

test("a rejection lists its reasons", () => {
  show({}, profileItem({ payload: { ...profileItem().payload, recommended_outcome: "rejected", gaps: [],
                                    reasons: ["No column identifies a row"] } }));
  expect(screen.getByTestId("profile-outcome").textContent).toContain("Rejected");
  expect(screen.getByText("No column identifies a row")).toBeTruthy();
});

test("no line says 'x of 0'", () => {
  const { container } = show();
  expect(container.textContent ?? "").not.toMatch(/\b\d+ of 0\b/);
});

test("the profile item carries its decision controls once", () => {
  show({}, profileItem({ pre_accepted: false, needs_decision: true }));
  expect(screen.getAllByRole("button", { name: "Approve" }).length).toBe(1);
});
