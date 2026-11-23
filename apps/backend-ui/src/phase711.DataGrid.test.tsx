import { MantineProvider } from "@mantine/core";
import { cleanup, fireEvent, screen, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import DataGrid, { type DataGridColumn } from "../../ui-shared/DataGrid";
import { renderWithTheme } from "./test/fixtures";
import { cssVariablesResolver, theme } from "./theme";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

// Local test fixtures, not dashboard seeds or sample figures from the references.
type Row = { id: string; feed: string; source: string; rows: number; score: number | null };
const rows: Row[] = [
  { id: "a", feed: "Zulu", source: "Upload", rows: 12, score: null },
  { id: "b", feed: "Alpha", source: "Vendor", rows: 103, score: 98.2 },
  { id: "c", feed: "Delta", source: "Upload", rows: 3, score: 87.1 },
  { id: "d", feed: "Bravo", source: "Vendor", rows: 27, score: 94.3 },
  { id: "e", feed: "Echo", source: "Upload", rows: 8, score: 91.7 },
];
const columns: DataGridColumn<Row>[] = [
  { key: "feed", label: "Feed", value: (row) => row.feed },
  { key: "source", label: "Source", value: (row) => row.source },
  { key: "rows", label: "Rows", value: (row) => row.rows },
  { key: "score", label: "Trust score", value: (row) => row.score },
];

function grid(data = rows) {
  return renderWithTheme(
    <DataGrid rows={data} columns={columns} rowKey={(row) => row.id} pageSize={2}
              aria-label="Recent loads" data-testid="recent-loads" />,
  );
}

function bodyText() {
  const table = screen.getByRole("table", { name: "Recent loads" });
  return within(table.querySelector("tbody")!).getAllByRole("row").map((row) => row.textContent);
}

test("pages real rows, exposes range and page, and disables boundary controls", () => {
  grid();
  expect(screen.getByTestId("recent-loads")).toBeTruthy();
  expect(bodyText()).toHaveLength(2);
  expect(screen.getByText("1 to 2 of 5")).toBeTruthy();
  expect(screen.getByText("Page 1 of 3")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Previous page" })).toHaveProperty("disabled", true);
  fireEvent.click(screen.getByRole("button", { name: "Next page" }));
  expect(bodyText().join(" ")).toContain("Delta");
  expect(screen.getByText("3 to 4 of 5")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Next page" }));
  expect(bodyText()).toHaveLength(1);
  expect(bodyText()[0]).toContain("Echo");
  expect(screen.getByRole("button", { name: "Next page" })).toHaveProperty("disabled", true);
  fireEvent.click(screen.getByRole("button", { name: "Previous page" }));
  expect(screen.getByText("Page 2 of 3")).toBeTruthy();
});

test("per-column filters are case-insensitive, combined, and reset pagination", () => {
  grid();
  fireEvent.click(screen.getByRole("button", { name: "Next page" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Filter source" }), { target: { value: "uPLOAD" } });
  expect(screen.getByText("1 to 2 of 3")).toBeTruthy();
  expect(screen.getByText("Page 1 of 2")).toBeTruthy();
  fireEvent.change(screen.getByRole("textbox", { name: "Filter feed" }), { target: { value: "delta" } });
  expect(bodyText()).toHaveLength(1);
  expect(bodyText()[0]).toContain("Delta");
  fireEvent.change(screen.getByRole("textbox", { name: "Filter feed" }), { target: { value: "" } });
  expect(screen.getByText("1 to 2 of 3")).toBeTruthy();
});

test("sorting is numeric, applies before paging, toggles direction and announces aria-sort", () => {
  grid();
  const header = screen.getByRole("columnheader", { name: /Rows/ });
  fireEvent.click(within(header).getByRole("button", { name: /Rows/ }));
  expect(header.getAttribute("aria-sort")).toBe("ascending");
  expect(bodyText()[0]).toContain("Delta");
  expect(bodyText()[1]).toContain("Echo");
  fireEvent.click(screen.getByRole("button", { name: "Next page" }));
  fireEvent.click(within(header).getByRole("button", { name: /Rows/ }));
  expect(header.getAttribute("aria-sort")).toBe("descending");
  expect(screen.getByText("Page 1 of 3")).toBeTruthy();
  expect(bodyText()[0]).toContain("Alpha");
  expect(bodyText()[1]).toContain("Bravo");
});

test("sorts text within filtered rows, rather than only the visible page", () => {
  grid();
  fireEvent.change(screen.getByRole("textbox", { name: "Filter source" }), { target: { value: "upload" } });
  fireEvent.click(within(screen.getByRole("columnheader", { name: /Feed/ })).getByRole("button"));
  expect(bodyText()[0]).toContain("Delta");
  expect(bodyText()[1]).toContain("Echo");
  fireEvent.click(screen.getByRole("button", { name: "Next page" }));
  expect(bodyText()[0]).toContain("Zulu");
});

test("no matches has an explicit empty state, zero range and no phantom pages", () => {
  grid();
  fireEvent.change(screen.getByRole("textbox", { name: "Filter feed" }), { target: { value: "not-present" } });
  expect(screen.getByText("No matching rows")).toBeTruthy();
  expect(screen.getByText("0 to 0 of 0")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Next page" })).toHaveProperty("disabled", true);
  expect(screen.getByRole("button", { name: "Previous page" })).toHaveProperty("disabled", true);
  fireEvent.change(screen.getByRole("textbox", { name: "Filter feed" }), { target: { value: "" } });
  expect(screen.getByText("1 to 2 of 5")).toBeTruthy();
});

test("clamps the page when upstream rows shrink and never renders null as a number", () => {
  const view = grid();
  expect(bodyText()[0]).not.toContain("null");
  fireEvent.click(screen.getByRole("button", { name: "Next page" }));
  fireEvent.click(screen.getByRole("button", { name: "Next page" }));
  // Keep the same provider/component instance, as a real refetch would.
  view.rerender(
    <MantineProvider theme={theme} cssVariablesResolver={cssVariablesResolver}>
      <DataGrid rows={rows.slice(0, 1)} columns={columns} rowKey={(row) => row.id} pageSize={2}
                aria-label="Recent loads" data-testid="recent-loads" />
    </MantineProvider>,
  );
  expect(screen.getByText("1 to 1 of 1")).toBeTruthy();
  expect(bodyText()[0]).toContain("Zulu");
});
