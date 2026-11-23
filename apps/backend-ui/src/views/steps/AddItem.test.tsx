import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import type { Workspace } from "../../api";
import { itemActions, onboardingItem, renderWithTheme, stepInputs } from "../../test/fixtures";
import AddItem from "./AddItem";
import ItemCard from "./ItemCard";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const TEMPLATE = { template: "not_negative", rule_key: "price_not_negative", description: "Price is not negative.",
                   sql: "SELECT _row FROM curated WHERE price < 0", element_keys: ["price"] };
const ws = (templates = [TEMPLATE]) => ({ inputs: stepInputs({ rule_templates: templates }) }) as unknown as Workspace;
const type = (label: string, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } });
const open = (name: string) => fireEvent.click(screen.getByRole("button", { name }));
const add = () => fireEvent.click(screen.getByRole("button", { name: "Add item" }));

function mount(step: Parameters<typeof AddItem>[0]["step"], answer = true, extra = {}) {
  const addFn = vi.fn().mockResolvedValue(answer);
  const actions = itemActions({ add: addFn, ...extra });
  renderWithTheme(<AddItem step={step} ws={ws()} actions={actions} />);
  return addFn;
}

test("a profile action: a column and a why, sent as an action a person will do themselves", async () => {
  const addFn = mount("profile");
  open("Add action");
  add();
  expect(screen.getByText("Choose a column")).toBeTruthy();
  type("Column", "Name");
  type("Action", "add_rule");
  type("Why", "The name must never be empty.");
  add();
  await waitFor(() => expect(addFn).toHaveBeenCalledWith({
    kind: "profile_action", action: "add_rule", columns: ["Name"], reason: "The name must never be empty." }));
});

test("there is no Add item read only, or without an add action, or at a step that takes none", () => {
  mount("first_load");
  expect(screen.queryByRole("button")).toBeNull();
  cleanup();
  renderWithTheme(<AddItem step="rules" ws={ws()} actions={itemActions({ add: vi.fn(), readOnly: true })} />);
  expect(screen.queryByRole("button")).toBeNull();
  cleanup();
  renderWithTheme(<AddItem step="rules" ws={ws()} actions={itemActions()} />);
  expect(screen.queryByRole("button")).toBeNull();
});

test("a glossary item: a new element is checked, then sent with its column and why", async () => {
  const addFn = mount("glossary");
  open("Add glossary item");
  add();
  expect(screen.getByText("Choose a column")).toBeTruthy();
  expect(screen.getByText("A name is needed")).toBeTruthy();
  expect(addFn).not.toHaveBeenCalled();
  type("Column", "Ticker");
  type("Element key", "my_ticker");
  type("Name", "My ticker");
  type("Domain", "reference_data");
  type("Definition", "The ticker as the desk writes it.");
  type("Why", "The agent's name is not ours.");
  add();
  await waitFor(() => expect(addFn).toHaveBeenCalledTimes(1));
  expect(addFn.mock.calls[0][0]).toMatchObject({
    kind: "glossary_element", source_field: "Ticker", reason: "The agent's name is not ours.",
    option: { kind: "new_element", key: "my_ticker", element: { key: "my_ticker", name: "My ticker", data_type: "string", unit: null } } });
  await waitFor(() => expect(screen.queryByRole("group", { name: "Add glossary item" })).toBeNull()); // added: it closes
});

test("a glossary alias sends the element and the alias", async () => {
  const addFn = mount("glossary");
  open("Add glossary item");
  type("Column", "Name");
  type("What to add", "alias");
  type("Element it is an alias of", "instrument_name");
  type("Alias", "security_title");
  type("Why", "Another name for it.");
  add();
  await waitFor(() => expect(addFn).toHaveBeenCalled());
  expect(addFn.mock.calls[0][0]).toMatchObject({ option: { kind: "alias", key: "instrument_name", alias: "security_title" } });
});

test("a mapping item: any element, or the column is left out", async () => {
  const addFn = mount("mapping");
  open("Add mapping item");
  type("Column", "Currency");
  type("Glossary element", "currency_code");
  type("Why", "The desk says so.");
  add();
  await waitFor(() => expect(addFn).toHaveBeenCalledTimes(1));
  expect(addFn.mock.calls[0][0]).toEqual({ kind: "mapping", source_field: "Currency", element_key: "currency_code",
                                           reason: "The desk says so." });
  open("Add mapping item");
  type("Column", "Name");
  fireEvent.click(screen.getByLabelText("Leave this column out (it is not loaded)"));
  expect(screen.queryByLabelText("Glossary element")).toBeNull();
  type("Why", "A scratch column.");
  add();
  await waitFor(() => expect(addFn).toHaveBeenCalledTimes(2));
  expect(addFn.mock.calls[1][0]).toEqual({ kind: "mapping", source_field: "Name", excluded: true, reason: "A scratch column." });
});

test("a transformation item uses the same steps as an edit and needs a column and a reason", async () => {
  const addFn = mount("transformations");
  open("Add transformation");
  fireEvent.click(screen.getByRole("button", { name: "Add step" }));
  add();
  expect(screen.getByText("Choose a column")).toBeTruthy();
  expect(addFn).not.toHaveBeenCalled();
  type("Column", "Name");
  type("Why", "Stray spaces.");
  add();
  await waitFor(() => expect(addFn).toHaveBeenCalledTimes(1));
  expect(addFn.mock.calls[0][0]).toEqual({ kind: "transformation", source_field: "Name", reason: "Stray spaces.",
                                           steps: [{ op: "trim" }] });
});

test("a rule: a template is picked, or the SQL is written; the API's refusal is shown and the form stays open", async () => {
  const addFn = mount("rules", false, { addError: "Not accepted: The rule names an element the feed does not map." });
  open("Add rule");
  expect(screen.getByRole("alert").textContent).toContain("does not map");
  add();
  expect(screen.getByText("Choose a template")).toBeTruthy();
  type("Template", "price_not_negative");
  add();
  await waitFor(() => expect(addFn).toHaveBeenCalledWith({ kind: "rule", template: "price_not_negative" }));
  expect(screen.getByRole("group", { name: "Add rule" })).toBeTruthy(); // refused: still open
  type("How", "own");
  type("Rule key", "my_rule");
  type("Description", "Nothing is flagged.");
  type("SQL", "SELECT _row FROM curated WHERE 1 = 0");
  type("Elements it checks", "price, volume");
  add();
  await waitFor(() => expect(addFn).toHaveBeenCalledTimes(2));
  expect(addFn.mock.calls[1][0]).toEqual({ kind: "rule", rule_key: "my_rule", description: "Nothing is flagged.",
                                           sql: "SELECT _row FROM curated WHERE 1 = 0", element_keys: ["price", "volume"] });
});

test("with no template on offer a rule is written directly", () => {
  const addFn = vi.fn();
  renderWithTheme(<AddItem step="rules" ws={ws([])} actions={itemActions({ add: addFn })} />);
  open("Add rule");
  expect(screen.queryByLabelText("Template")).toBeNull();
  expect(screen.getByLabelText("SQL")).toBeTruthy();
});

test("a go-live note is sent as a note", async () => {
  const addFn = mount("go_live");
  open("Add note");
  add();
  expect(screen.getByText("Write the note")).toBeTruthy();
  type("Note", "Desk head has seen the test load.");
  add();
  await waitFor(() => expect(addFn).toHaveBeenCalledWith({ kind: "review_summary", note: "Desk head has seen the test load." }));
});

test("a manual item says who added it, and still has Reject and Edit like an agent's", () => {
  const manual = onboardingItem({ origin: "manual", author: "dev-operator", pre_accepted: true });
  renderWithTheme(<ItemCard item={manual} actions={itemActions({ people: { "dev-operator": "Olive Operator" } })}>body</ItemCard>);
  expect(screen.getByText("added by Olive Operator")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Reject" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Edit" })).toBeTruthy();
});
