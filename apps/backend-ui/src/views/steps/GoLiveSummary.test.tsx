import { cleanup, fireEvent, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { itemActions, renderWithTheme, reviewItem, stepInputs, workspace } from "../../test/fixtures";
import GoLiveSummary from "./GoLiveSummary";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function show(over = {}, visibility: "open" | "restricted" = "open", readOnly = false) {
  const onVisibility = vi.fn();
  const ws = workspace("go_live", "ready", {
    items: [reviewItem()],
    inputs: stepInputs({ facts: [{ label: "Mapped columns", value: "6" }, { label: "Approved rules", value: "2" },
                                 { label: "Test load", value: "loaded, 70 rows" }] }),
    ...over,
  });
  const view = renderWithTheme(<GoLiveSummary ws={ws} items={ws.items} actions={itemActions({ readOnly })}
                                              visibility={visibility} onVisibility={onVisibility} />);
  return { onVisibility, ...view };
}

test("the summary page shows the counts, the review text, its highlights and risks, and the test load", () => {
  show();
  const page = screen.getByTestId("summary-page");
  expect(page.textContent).toContain("Mapped columns: 6");
  expect(page.textContent).toContain("Approved rules: 2");
  expect(page.textContent).toContain("All 6 columns are mapped; the test load passed.");
  expect(page.textContent).toContain("Six columns mapped");
  expect(page.textContent).toContain("Currency has placeholders");
  expect(page.textContent).toContain("Test load: loaded, 70 rows");
});

test("the steps before go-live are listed with their state", () => {
  show();
  expect(screen.getByTestId("summary-page").textContent).toContain("Glossary: done");
});

test("the visibility radios are native, default open, and report a change", () => {
  const { onVisibility } = show();
  const open = screen.getByLabelText("Open to every user") as HTMLInputElement;
  expect(open.type).toBe("radio");
  expect(open.checked).toBe(true);
  fireEvent.click(screen.getByLabelText("Restricted to entitled users"));
  expect(onVisibility).toHaveBeenCalledWith("restricted");
});

test("the radios show the chosen visibility", () => {
  show({}, "restricted");
  expect((screen.getByLabelText("Restricted to entitled users") as HTMLInputElement).checked).toBe(true);
});

test("a read-only view disables the radios", () => {
  show({}, "open", true);
  expect((screen.getByLabelText("Restricted to entitled users") as HTMLInputElement).disabled).toBe(true);
});

test("the review item does not repeat its text and carries no 'x of 0'", () => {
  const { container } = show();
  expect((container.textContent ?? "").split("All 6 columns are mapped; the test load passed.").length).toBe(2);
  expect(container.textContent ?? "").not.toMatch(/\b\d+ of 0\b/);
});

test("the summary item that needs a decision has Approve once", () => {
  show({ items: [reviewItem({ pre_accepted: false, needs_decision: true })] });
  expect(screen.getAllByRole("button", { name: "Approve" }).length).toBe(1);
});
