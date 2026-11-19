import { cleanup, screen } from "@testing-library/react";
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
