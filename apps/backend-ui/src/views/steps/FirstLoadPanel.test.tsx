import { cleanup, fireEvent, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { renderWithTheme, stepInputs, workspace } from "../../test/fixtures";
import FirstLoadPanel from "./FirstLoadPanel";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

test("a finished first load says it was released, with the rows and every fact the API gives", () => {
  const ws = workspace("product_ready", "done", {
    viewed_step: "first_load", items: [],
    inputs: stepInputs({ row_count: 120, facts: [{ label: "Trust score", value: "98.3" }] }),
  });
  const { container } = renderWithTheme(<FirstLoadPanel ws={ws} state="done" detail={null} />);
  expect(screen.getByTestId("first-load-outcome").textContent).toBe("Released: the first load passed and the product was created.");
  expect(container.textContent).toContain("Rows in the file: 120");
  expect(container.textContent).toContain("98.3");
});

test("the figures are rows loaded, rows flagged and the trust score; a held load shows the warning fill and its detail", () => {
  const facts = [{ label: "Load", value: "held" }, { label: "Rows loaded", value: "70" }, { label: "Rows flagged", value: "9" },
                 { label: "Trust score", value: "87.1" }, { label: "Outcome", value: "held consumers warned" }];
  const ws = workspace("first_load", "failed", { items: [], inputs: stepInputs({ facts }) });
  renderWithTheme(<FirstLoadPanel ws={ws} state="failed" detail="The first load ended held: below the threshold." />);
  const figures = screen.getByTestId("first-load-figures").textContent ?? "";
  for (const text of ["70", "Rows loaded", "9", "Rows flagged", "87.1", "Trust score"]) expect(figures).toContain(text);
  const outcome = screen.getByTestId("first-load-outcome");
  expect(outcome.textContent).toContain("Held");
  expect(outcome.getAttribute("style") ?? "").toContain("--amz-warning");
  expect(screen.getByTestId("first-load-detail").textContent).toContain("below the threshold");
});

test("a released load shows its figures, the outcome and the product", () => {
  const facts = [{ label: "Rows loaded", value: "70" }, { label: "Rows flagged", value: "0" }, { label: "Trust score", value: "100.0" },
                 { label: "Outcome", value: "released" }, { label: "Product", value: "security_master" }];
  const ws = workspace("product_ready", "done", { viewed_step: "first_load", items: [], inputs: stepInputs({ facts }) });
  const { container } = renderWithTheme(<FirstLoadPanel ws={ws} state="done" detail={null} />);
  expect(screen.getByTestId("first-load-outcome").textContent).toContain("Released");
  expect(container.textContent).toContain("Product: security_master");
  expect(screen.getByTestId("first-load-figures").textContent).toContain("100.0");
});

test("a load that ended held shows the reason the API gives", () => {
  const ws = workspace("first_load", "failed", { items: [] });
  renderWithTheme(<FirstLoadPanel ws={ws} state="failed" detail="The first load ended held: its trust score is below the release threshold." />);
  expect(screen.getByTestId("first-load-outcome").textContent).toContain("Held or failed");
  expect(screen.getByText("The first load ended held: its trust score is below the release threshold.")).toBeTruthy();
});

test("a load not run yet says so; no row count of 0 is shown", () => {
  const ws = workspace("first_load", "ready", { items: [], inputs: stepInputs({ row_count: 0 }) });
  const { container } = renderWithTheme(<FirstLoadPanel ws={ws} state="ready" detail={null} />);
  expect(screen.getByTestId("first-load-outcome").textContent).toContain("Ready to load");
  expect(container.textContent ?? "").not.toContain("Rows in the file");
});

// Follow-up item 1: a load that did not release is not retried (the same file would give the same answer): the
// operator goes back to the rules or uploads a new file.
function didNotRelease(status: string, handlers: { back?: () => void; upload?: (f: File) => void } = {}) {
  const facts = [{ label: "Load", value: status }];
  const ws = workspace("first_load", "failed", { items: [], inputs: stepInputs({ facts }) });
  return renderWithTheme(<FirstLoadPanel ws={ws} state="failed" detail={`The first load ended ${status}.`}
                                         onBackToRules={handlers.back ?? vi.fn()} onUpload={handlers.upload ?? vi.fn()} busy={false} />);
}

test.each(["held", "quarantined", "failed rolled back"])("a %s first load offers Back to Rules and Upload a new file, not Try again", (status) => {
  didNotRelease(status);
  expect(screen.getByRole("button", { name: "Back to Rules" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Upload a new file" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
});

test("the message is the latest load's own sentence, not the stored job message", () => {
  const ws = workspace("first_load", "failed", { items: [], inputs: stepInputs({
    facts: [{ label: "Load", value: "held" }], load_summary: "The latest load ended held: its trust score 80.0 is below 90.0." }) });
  renderWithTheme(<FirstLoadPanel ws={ws} state="failed" detail="The first load ended held: an OLDER load." />);
  const text = screen.getByTestId("first-load-detail").textContent ?? "";
  expect(text).toContain("trust score 80.0");
  expect(text).not.toContain("OLDER");
});

const BREAKDOWN = {
  load_id: "l1", status: "held", rows: 10, flagged_rows: 2, trust_score: "80.0", threshold: "90.0", below_threshold: true,
  rules: [{ rule_key: "volume_over_100000", flagged_rows: 2, total_rows: 10, sample_rows: [1, 2] },
          { rule_key: "id_present", flagged_rows: 0, total_rows: 10, sample_rows: [] }],
  next_action: "Use Back to Rules to reject or change the rules that flag too many rows, or Upload a new file.",
};

test("the breakdown lists each rule as rows flagged of total, with its first flagged rows", () => {
  const ws = workspace("first_load", "failed", { items: [], inputs: stepInputs({ facts: [{ label: "Load", value: "held" }], load_breakdown: BREAKDOWN }) });
  renderWithTheme(<FirstLoadPanel ws={ws} state="failed" detail={null} />);
  const rules = screen.getByTestId("first-load-rules").textContent ?? "";
  expect(rules).toContain("volume_over_100000");
  expect(rules).toContain("2 of 10 rows flagged");
  expect(rules).toContain("Rows 1, 2");
  expect(rules).toContain("id_present");
  expect(rules).toContain("0 of 10 rows flagged");
});

test("the trust score is shown next to the release threshold, and says when it is below", () => {
  const ws = workspace("first_load", "failed", { items: [], inputs: stepInputs({ facts: [{ label: "Load", value: "held" }], load_breakdown: BREAKDOWN }) });
  renderWithTheme(<FirstLoadPanel ws={ws} state="failed" detail={null} />);
  const score = screen.getByTestId("first-load-score").textContent ?? "";
  expect(score).toContain("80.0");
  expect(score).toContain("90.0");
  expect(score).toContain("below");
});

test("a score at or above the threshold does not say below", () => {
  const ok = { ...BREAKDOWN, status: "released", trust_score: "95.0", below_threshold: false, next_action: "Nothing to do: the product is created." };
  const ws = workspace("product_ready", "done", { viewed_step: "first_load", items: [], inputs: stepInputs({ load_breakdown: ok }) });
  renderWithTheme(<FirstLoadPanel ws={ws} state="done" detail={null} />);
  expect(screen.getByTestId("first-load-score").textContent).not.toContain("below");
});

test("the next action is shown as the API gives it", () => {
  const ws = workspace("first_load", "failed", { items: [], inputs: stepInputs({ facts: [{ label: "Load", value: "held" }], load_breakdown: BREAKDOWN }) });
  renderWithTheme(<FirstLoadPanel ws={ws} state="failed" detail={null} />);
  expect(screen.getByTestId("first-load-next").textContent).toContain("Back to Rules");
});

test("a load with no rules (quarantined) shows no score and no rule list, only the next action", () => {
  const q = { ...BREAKDOWN, status: "quarantined", rows: 0, flagged_rows: 0, trust_score: null, below_threshold: false, rules: [],
              next_action: "Use Upload a new file: this exact file was loaded before." };
  const ws = workspace("first_load", "failed", { items: [], inputs: stepInputs({ facts: [{ label: "Load", value: "quarantined" }], load_breakdown: q }) });
  renderWithTheme(<FirstLoadPanel ws={ws} state="failed" detail={null} />);
  expect(screen.queryByTestId("first-load-score")).toBeNull();
  expect(screen.queryByTestId("first-load-rules")).toBeNull();
  expect(screen.getByTestId("first-load-next").textContent).toContain("Upload a new file");
});

test("Back to Rules calls its handler; a chosen file goes to the upload handler", () => {
  const back = vi.fn(), upload = vi.fn();
  didNotRelease("held", { back, upload });
  fireEvent.click(screen.getByRole("button", { name: "Back to Rules" }));
  expect(back).toHaveBeenCalledTimes(1);
  const file = new File(["a,b\n1,2\n"], "next.csv", { type: "text/csv" });
  fireEvent.change(screen.getByTestId("first-load-file"), { target: { files: [file] } });
  expect(upload).toHaveBeenCalledWith(file);
});

test("both buttons are disabled while a request runs", () => {
  const facts = [{ label: "Load", value: "held" }];
  const ws = workspace("first_load", "failed", { items: [], inputs: stepInputs({ facts }) });
  renderWithTheme(<FirstLoadPanel ws={ws} state="failed" detail="x" onBackToRules={vi.fn()} onUpload={vi.fn()} busy />);
  expect((screen.getByRole("button", { name: "Back to Rules" }) as HTMLButtonElement).disabled).toBe(true);
  expect((screen.getByRole("button", { name: "Upload a new file" }) as HTMLButtonElement).disabled).toBe(true);
});

test("a read-only view of the step offers neither", () => {
  const ws = workspace("first_load", "failed", { items: [], inputs: stepInputs({ facts: [{ label: "Load", value: "held" }] }) });
  renderWithTheme(<FirstLoadPanel ws={ws} state="failed" detail="x" />);
  expect(screen.queryByRole("button", { name: "Back to Rules" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Upload a new file" })).toBeNull();
});
