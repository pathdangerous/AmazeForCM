import { cleanup, fireEvent, screen, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { itemActions, renderWithTheme, stepInputs, transformationItem, workspace } from "../../test/fixtures";
import TransformationsPanel from "./TransformationsPanel";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function show(over = {}, actions = itemActions(), onKeyPolicy = vi.fn(), keyPolicyError: string | null = null) {
  const ws = workspace("transformations", "ready", { items: [transformationItem()], ...over });
  renderWithTheme(<TransformationsPanel ws={ws} items={ws.items} actions={actions} onKeyPolicy={onKeyPolicy}
                                         keyPolicyError={keyPolicyError} />);
  return { actions, onKeyPolicy };
}

test("per column the ordered steps with their arguments", () => {
  show();
  const steps = within(screen.getByTestId(`item-${transformationItem().proposal_id}`)).getAllByRole("listitem");
  expect(steps.map((s) => s.textContent)).toEqual(["1. trim", "2. placeholder to null: N/A"]);
});

test("a pre-accepted transformation has Reject and Edit, no Approve", () => {
  show();
  const card = screen.getByTestId(`item-${transformationItem().proposal_id}`);
  expect(within(card).queryByRole("button", { name: "Approve" })).toBeNull();
  expect(within(card).getByRole("button", { name: "Edit" })).toBeTruthy();
});

test("the edit form sends the changed steps", () => {
  const item = transformationItem();
  const { actions } = show({ items: [item] }, itemActions({ editing: item.proposal_id }));
  fireEvent.click(screen.getByRole("button", { name: "Remove step 1" }));
  fireEvent.click(screen.getByRole("button", { name: "Save edit" }));
  expect(actions.saveEdit).toHaveBeenCalledWith(expect.objectContaining({ proposal_id: item.proposal_id }),
    expect.objectContaining({ steps: [{ op: "placeholder_to_null", values: ["N/A"] }] }));
});

test("the duplicate-key policy shows and is saved through onKeyPolicy", () => {
  const { onKeyPolicy } = show({ key_policy: { policy: "hold", key_columns: ["Ticker"], reason: "Hold duplicates" },
                                  inputs: stepInputs({ key_columns: ["Ticker"] }) });
  expect((screen.getByLabelText("Duplicate-key policy") as HTMLSelectElement).value).toBe("hold");
  expect((screen.getByLabelText("Key columns") as HTMLInputElement).value).toBe("Ticker");
  fireEvent.change(screen.getByLabelText("Duplicate-key policy"), { target: { value: "keep_last" } });
  fireEvent.change(screen.getByLabelText("Reason"), { target: { value: "The last row wins" } });
  fireEvent.click(screen.getByRole("button", { name: "Save key policy" }));
  expect(onKeyPolicy).toHaveBeenCalledWith({ policy: "keep_last", key_columns: ["Ticker"], reason: "The last row wins" });
});

test("a key policy without a reason or key columns is not sent", () => {
  const { onKeyPolicy } = show({ key_policy: null });
  fireEvent.click(screen.getByRole("button", { name: "Save key policy" }));
  expect(screen.getByText("A reason is needed")).toBeTruthy();
  expect(screen.getByText("Name at least one key column")).toBeTruthy();
  expect(onKeyPolicy).not.toHaveBeenCalled();
});

test("the API's message about the key policy is shown", () => {
  show({}, itemActions(), vi.fn(), "Not accepted: unknown key column");
  expect(screen.getByRole("alert").textContent).toContain("Not accepted: unknown key column");
});

test("read only: the policy is shown as text, with no form", () => {
  show({ key_policy: { policy: "flag", key_columns: ["Ticker"], reason: "Flag them" } }, itemActions({ readOnly: true }));
  expect(screen.queryByRole("button", { name: "Save key policy" })).toBeNull();
  expect(screen.getByText(/flag on Ticker/)).toBeTruthy();
});
