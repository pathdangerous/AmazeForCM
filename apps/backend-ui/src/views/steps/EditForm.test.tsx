import { cleanup, fireEvent, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import {
  aliasItem, glossaryItem, mappingItem, renderWithTheme, ruleItem, tieItem, transformationItem,
} from "../../test/fixtures";
import type { OnboardingItem } from "../../api";
import EditForm from "./EditForm";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function form(item: OnboardingItem, error: string | null = null) {
  const onSave = vi.fn();
  const onCancel = vi.fn();
  renderWithTheme(<EditForm item={item} error={error} onSave={onSave} onCancel={onCancel} />);
  return { onSave, onCancel };
}
const save = () => fireEvent.click(screen.getByRole("button", { name: "Save edit" }));
const type = (label: string, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } });

test("a new glossary element: a changed field is sent in the edited payload, the rest as it was", () => {
  const item = glossaryItem();
  const { onSave } = form(item);
  expect((screen.getByLabelText("Type") as HTMLSelectElement).tagName).toBe("SELECT");
  type("Name", "Coupon percentage");
  type("Type", "integer");
  type("Unit", "");
  save();
  const sent = onSave.mock.calls[0][0] as { options: { element: Record<string, unknown> }[]; source_field: string };
  expect(sent.source_field).toBe("Coupon");
  expect(sent.options[0].element).toMatchObject({ key: "coupon_rate", name: "Coupon percentage", data_type: "integer",
                                                   unit: null, definition: "The annual coupon as a percentage." });
});

test("a glossary element needs a name and a definition before anything is sent", () => {
  const { onSave } = form(glossaryItem());
  type("Name", " ");
  type("Definition", "");
  save();
  expect(screen.getByText("A name is needed")).toBeTruthy();
  expect(screen.getByText("A definition is needed")).toBeTruthy();
  expect(onSave).not.toHaveBeenCalled();
});

test("an alias option edits the alias, which must be snake case", () => {
  const { onSave } = form(aliasItem());
  type("Alias", "Bad Alias");
  save();
  expect(screen.getByText(/lower-case letters, digits and underscores/)).toBeTruthy();
  expect(onSave).not.toHaveBeenCalled();
  type("Alias", "ccy_code");
  save();
  expect((onSave.mock.calls[0][0] as { options: { alias: string }[] }).options[0].alias).toBe("ccy_code");
});

test("a mapping edit picks the element from the candidates", () => {
  const { onSave } = form(tieItem());
  const select = screen.getByLabelText("Element") as HTMLSelectElement;
  expect(Array.from(select.options).map((o) => o.value)).toEqual(["", "bid_price", "ask_price"]);
  save();
  expect(screen.getByText("Choose an element")).toBeTruthy();
  expect(onSave).not.toHaveBeenCalled();
  type("Element", "ask_price");
  save();
  expect(onSave.mock.calls[0][0]).toMatchObject({ candidates: ["ask_price"], recommended: "ask_price", source_field: "Price" });
});

test("a mapping with one candidate starts on it", () => {
  const { onSave } = form(mappingItem());
  expect((screen.getByLabelText("Element") as HTMLSelectElement).value).toBe("instrument_ticker");
  save();
  expect(onSave).toHaveBeenCalledTimes(1);
});

test("transformation steps can be changed, added, removed and moved", () => {
  const { onSave } = form(transformationItem());
  expect((screen.getByLabelText("Step 1 operation") as HTMLSelectElement).tagName).toBe("SELECT");
  type("Step 2 placeholder values", "N/A, ALL");
  fireEvent.click(screen.getByRole("button", { name: "Add step" }));
  type("Step 3 operation", "parse_date");
  type("Step 3 format", "DD/MM/YYYY");
  fireEvent.click(screen.getByRole("button", { name: "Move step 3 up" }));
  expect((screen.getByLabelText("Step 2 operation") as HTMLSelectElement).value).toBe("parse_date");
  fireEvent.click(screen.getByRole("button", { name: "Remove step 1" }));
  save();
  expect((onSave.mock.calls[0][0] as { steps: unknown[] }).steps).toEqual([
    { op: "parse_date", format: "DD/MM/YYYY" }, { op: "placeholder_to_null", values: ["N/A", "ALL"] }]);
});

test("transformation steps are checked before sending: each op once, rename last, a format from the list", () => {
  const { onSave } = form(transformationItem());
  fireEvent.click(screen.getByRole("button", { name: "Add step" }));
  type("Step 3 operation", "trim");
  save();
  expect(screen.getByText("Each step may be used once")).toBeTruthy();
  type("Step 3 operation", "rename");
  type("Step 3 new name", "currency_code");
  fireEvent.click(screen.getByRole("button", { name: "Move step 3 up" }));
  save();
  expect(screen.getByText("Rename must be the last step")).toBeTruthy();
  expect(onSave).not.toHaveBeenCalled();
  const formats = Array.from((() => {
    type("Step 2 operation", "parse_date");
    return (screen.getByLabelText("Step 2 format") as HTMLSelectElement).options;
  })()).map((o) => o.value);
  expect(formats).toContain("YYYY-MM-DD");
  expect(formats).not.toContain("YYYY/MM/DD");
});

test("a rule edit changes the description and the SQL, both required", () => {
  const { onSave } = form(ruleItem());
  type("SQL", "SELECT _row FROM data WHERE price < 0");
  type("Description", "Price is not negative");
  save();
  expect(onSave.mock.calls[0][0]).toMatchObject({ rule_key: "price_positive", description: "Price is not negative",
                                                   sql: "SELECT _row FROM data WHERE price < 0" });
  type("SQL", " ");
  save();
  expect(screen.getByText("SQL is needed")).toBeTruthy();
  expect(onSave).toHaveBeenCalledTimes(1);
});

test("the API's message is shown, and Cancel closes the form", () => {
  const { onCancel } = form(ruleItem(), "Not accepted: the edited rule proposal is not valid");
  expect(screen.getByRole("alert").textContent).toContain("Not accepted: the edited rule proposal is not valid");
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(onCancel).toHaveBeenCalled();
});
