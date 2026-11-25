import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { renderWithTheme, RULE_ID, ruleRowsPath, ruleRowsShown, stubFetch } from "../test/fixtures";
import RuleRowsButton from "./RuleRowsPanel";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

async function open(routes: Record<string, unknown>) {
  const fetched = stubFetch(routes);
  renderWithTheme(<RuleRowsButton ruleId={RULE_ID} ruleKey="price_positive" devUser="dev-data-steward" />);
  const opener = screen.getByRole("button", { name: "Show rows of price_positive" });
  opener.focus(); // a real click or key press focuses the button first; jsdom's click() does not
  fireEvent.click(opener);
  return { fetched, opener, dialog: await screen.findByRole("dialog") };
}

const cell = (dialog: HTMLElement, row: number, column: string) => {
  const index = within(dialog).getAllByRole("columnheader").findIndex((h) => h.textContent?.startsWith(column));
  return within(within(dialog).getAllByRole("row")[row]).getAllByRole("cell")[index]; // the first column is the row number
};

test("it shows the rule, how many rows fail, and asks the API for the first page", async () => {
  const { fetched, dialog } = await open({ [ruleRowsPath()]: ruleRowsShown() });
  expect(within(dialog).getByText("price_positive")).toBeTruthy();
  expect(within(dialog).getByText("Price is above zero")).toBeTruthy();
  expect(await within(dialog).findByText("1 of 6 rows fail")).toBeTruthy();
  expect(within(dialog).getByText("1 waived")).toBeTruthy();
  expect(fetched).toHaveBeenCalledWith(ruleRowsPath().replace("GET ", ""), expect.objectContaining({
    headers: expect.objectContaining({ "X-Dev-User": "dev-data-steward" }) }));
});

test("the rule's column header is highlighted and its cells are green, red or amber, each with its word", async () => {
  const { dialog } = await open({ [ruleRowsPath()]: ruleRowsShown() });
  await within(dialog).findByText("1 of 6 rows fail");
  const headers = within(dialog).getAllByRole("columnheader");
  expect(headers.find((h) => h.textContent?.startsWith("bid_price"))?.getAttribute("data-affected")).toBe("true");
  expect(headers.find((h) => h.textContent?.startsWith("instrument_id"))?.getAttribute("data-affected")).toBeNull();
  for (const [row, state, word] of [[1, "passes", "passes"], [2, "fails", "fails"], [3, "waived", "waived"]] as const) {
    const colored = cell(dialog, row, "bid_price");
    expect(colored.getAttribute("data-state")).toBe(state);
    expect(colored.style.backgroundColor).toBe(`var(--amz-${state === "passes" ? "pass" : state === "fails" ? "fail" : "waived"})`);
    expect(colored.textContent).toContain(`${word}:`); // the word is in the cell, not only the colour
  }
  const plain = cell(dialog, 2, "instrument_id");
  expect(plain.getAttribute("data-state")).toBeNull();
  expect(plain.textContent).toBe("syn--5");
});

test("a legend names the three colours", async () => {
  const { dialog } = await open({ [ruleRowsPath()]: ruleRowsShown() });
  const legend = await within(dialog).findByLabelText("Colours");
  expect(legend.textContent).toContain("passes");
  expect(legend.textContent).toContain("fails");
  expect(legend.textContent).toContain("waived");
});

test("Show failing rows only asks for the failing rows again, from the first page", async () => {
  const { fetched, dialog } = await open({
    [ruleRowsPath()]: ruleRowsShown({ page: 1, size: 2, counts: { rows: 6, failing: 1, waived: 1, shown: 6 } }),
    [ruleRowsPath("failing_only=true&page=1")]: ruleRowsShown({ rows: ruleRowsShown().rows.slice(1, 2),
      counts: { rows: 6, failing: 1, waived: 1, shown: 1 } }),
  });
  fireEvent.click(await within(dialog).findByRole("checkbox", { name: "Show failing rows only" }));
  await waitFor(() => expect(fetched).toHaveBeenCalledWith(ruleRowsPath("failing_only=true&page=1").replace("GET ", ""),
                                                         expect.anything()));
  await waitFor(() => expect(within(dialog).getAllByRole("row")).toHaveLength(2)); // header + the one failing row
});

test("paging: Next and Previous, each disabled at its end", async () => {
  const { fetched, dialog } = await open({
    [ruleRowsPath()]: ruleRowsShown({ size: 3 }),
    [ruleRowsPath("failing_only=false&page=2")]: ruleRowsShown({ page: 2, size: 3 }),
  });
  const previous = await within(dialog).findByRole("button", { name: "Previous page" });
  const next = within(dialog).getByRole("button", { name: "Next page" });
  expect(within(dialog).getByText("Page 1 of 2")).toBeTruthy();
  expect((previous as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(next);
  await waitFor(() => expect(within(dialog).getByText("Page 2 of 2")).toBeTruthy());
  expect(fetched).toHaveBeenCalledWith(ruleRowsPath("failing_only=false&page=2").replace("GET ", ""), expect.anything());
  expect((within(dialog).getByRole("button", { name: "Next page" }) as HTMLButtonElement).disabled).toBe(true);
});

test("Escape closes the panel and the focus returns to the rule's button", async () => {
  const { opener, dialog } = await open({ [ruleRowsPath()]: ruleRowsShown() });
  await within(dialog).findByText("1 of 6 rows fail");
  fireEvent.keyDown(dialog, { key: "Escape" });
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  await waitFor(() => expect(document.activeElement).toBe(opener));
});

test.each([
  [{ outcome: "no_file", reason: "The feed has no fetched file yet." }, "The feed has no fetched file yet."],
  [{ outcome: "sql_refused", reason: "a rule is a SELECT, not DELETE" }, "The rule's SQL was refused: a rule is a SELECT, not DELETE"],
  [{ outcome: "rule_not_found" }, "This rule was not found."],
])("a typed refusal is said in words: %j", async (answer, words) => {
  const { dialog } = await open({ [ruleRowsPath()]: answer });
  expect(await within(dialog).findByText(words)).toBeTruthy();
  expect(within(dialog).queryByRole("table")).toBeNull();
});
