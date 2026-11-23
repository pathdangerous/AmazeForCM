import { cleanup, screen, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { previewRows, productPage, renderWithTheme, stubFetch } from "../test/fixtures";
import ProductView from "./ProductView";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const PAGE = "GET /api/consumer/products/demo_bond_terms";
const PREVIEW = `${PAGE}/preview`;
// Test responses only. Operational metadata will be supplied by the product API,
// never populated in production from this fixture or the mockup's sample figures.
const certification = { trust_score: 87.5, rows: 24, flagged_rows: 3 };
const lineage = [
  { stage: "Source", name: "Terms delivery.csv", rows: null },
  { stage: "Curated", name: "Curated terms", rows: 24 },
  { stage: "Validated", name: "Validated terms", rows: 24 },
  { stage: "Standard model", name: "Standard terms", rows: 24 },
  { stage: "Product", name: "Bond terms", rows: 24 },
];

function show() {
  renderWithTheme(<ProductView devUser="dev-requester" code="demo_bond_terms" />);
}

test("entitled product shows the supplied released-load trust score and ordered factual lineage", async () => {
  stubFetch({
    [PAGE]: { ...productPage(), certification, lineage },
    [PREVIEW]: previewRows(2),
  });
  show();
  expect(await screen.findByRole("img", { name: /Trust score.*87.5/ })).toBeTruthy();
  expect(screen.getByText(/3 of 24 rows flagged/)).toBeTruthy();
  expect(screen.getByRole("heading", { name: "Where this data comes from" })).toBeTruthy();
  const stages = within(screen.getByRole("list", { name: "Data lineage" })).getAllByRole("listitem");
  expect(stages).toHaveLength(5);
  lineage.forEach((entry, index) => {
    expect(stages[index].textContent).toContain(entry.stage);
    expect(stages[index].textContent).toContain(entry.name);
    if (entry.rows !== null) expect(stages[index].textContent).toContain(`${entry.rows} rows`);
  });
  expect(stages[0].textContent).not.toContain("0 rows");
  expect(screen.queryByText(/Azure OpenAI|Internal use only|transformations applied/)).toBeNull();
  expect(screen.getByTestId("product-download-xlsx")).toBeTruthy();
  expect(await screen.findByTestId("product-preview")).toBeTruthy();
});

test("missing released-load metadata omits trust and lineage instead of inventing defaults", async () => {
  stubFetch({ [PAGE]: productPage(), [PREVIEW]: previewRows(2) });
  show();
  await screen.findByRole("heading", { name: "Bond terms" });
  expect(screen.queryByRole("img", { name: /Trust score/ })).toBeNull();
  expect(screen.queryByRole("heading", { name: "Where this data comes from" })).toBeNull();
  expect(screen.queryByRole("list", { name: "Data lineage" })).toBeNull();
});

test("restricted product never renders operational metadata or download controls", async () => {
  stubFetch({
    [PAGE]: {
      ...productPage({ access: "not_entitled_visible", route: "Ask the owner for access" }),
      certification, lineage,
    },
  });
  show();
  await screen.findByTestId("product-route");
  expect(screen.queryByRole("img", { name: /Trust score/ })).toBeNull();
  expect(screen.queryByRole("list", { name: "Data lineage" })).toBeNull();
  expect(screen.queryByTestId("product-download-xlsx")).toBeNull();
  expect(screen.queryByTestId("product-download-csv")).toBeNull();
});
