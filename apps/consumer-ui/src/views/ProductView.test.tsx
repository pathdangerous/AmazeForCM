import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { previewRows, productPage, renderWithTheme, stubFetch } from "../test/fixtures";
import ProductView from "./ProductView";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const PAGE = "GET /api/consumer/products/demo_bond_terms";
const PREVIEW = `${PAGE}/preview`;

function show() {
  renderWithTheme(<ProductView devUser="dev-requester" code="demo_bond_terms" />);
}

function downloadStub(file: string) {
  const fetchStub = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith(file)) return new Response("a,b\n1,2\n", { status: 200 });
    return new Response(JSON.stringify(url.endsWith("/preview") ? previewRows(2) : productPage()), { status: 200 });
  });
  vi.stubGlobal("fetch", fetchStub);
  const create = vi.fn(() => "blob:test");
  Object.assign(URL, { createObjectURL: create, revokeObjectURL: vi.fn() });
  const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
  return { fetchStub, create, click };
}

test("phase 7.9: a paused product says since when it is not updating, and still shows its last release", async () => {
  stubFetch({ [PAGE]: productPage({ paused_since: "2026-11-20T09:30:00" }), [PREVIEW]: previewRows(2) });
  show();
  const note = await screen.findByTestId("product-paused");
  expect(note.textContent).toBe("Paused: not updating since 20 Nov 2026, 09:30");
  expect(screen.getByText(/Updated 18 Nov 2026, 12:44/)).toBeTruthy();
  expect(screen.getByTestId("product-dictionary")).toBeTruthy(); // the product is still usable
});

test("phase 7.9: a product that is not paused shows no paused note", async () => {
  stubFetch({ [PAGE]: productPage(), [PREVIEW]: previewRows(2) });
  show();
  await screen.findByRole("heading", { name: "Bond terms" });
  expect(screen.queryByTestId("product-paused")).toBeNull();
});

test("phase 7.21: the page says how often the product is updated, or that it is a one-time load", async () => {
  for (const [label, shown] of [["Updated daily", "Updated daily"], ["Updated on business days", "Updated on business days"],
                                ["One-time load", "One-time load"]]) {
    stubFetch({ [PAGE]: productPage({ update_frequency: label }), [PREVIEW]: previewRows(2) });
    show();
    expect((await screen.findByTestId("product-update-frequency")).textContent).toBe(shown);
    cleanup();
  }
});

test("phase 7.21: a product whose feed has no schedule says nothing about how often it is updated", async () => {
  stubFetch({ [PAGE]: productPage({ update_frequency: null }), [PREVIEW]: previewRows(2) });
  show();
  await screen.findByRole("heading", { name: "Bond terms" });
  expect(screen.queryByTestId("product-update-frequency")).toBeNull();
});

test("header, dictionary and preview rows", async () => {
  stubFetch({ [PAGE]: productPage(), [PREVIEW]: previewRows(3) });
  show();
  expect(await screen.findByRole("heading", { name: "Bond terms" })).toBeTruthy();
  expect(screen.getByText(/Olly Owner/)).toBeTruthy();
  expect(screen.getByText(/Updated 18 Nov 2026, 12:44/)).toBeTruthy();
  const dictionary = screen.getByTestId("product-dictionary");
  expect(within(dictionary).getAllByRole("row")).toHaveLength(3); // header + 2
  expect(dictionary.textContent).toContain("Yearly interest rate.");
  expect(dictionary.textContent).toContain("percent");
  const preview = await screen.findByTestId("product-preview");
  expect(within(preview).getAllByRole("row")).toHaveLength(4); // header + 3
  expect(screen.queryByText(/Showing 100 of/)).toBeNull();
});

test("100 rows of 150 say so", async () => {
  stubFetch({ [PAGE]: productPage(), [PREVIEW]: previewRows(100) });
  show();
  expect(await screen.findByText("Showing 100 of 150 rows")).toBeTruthy();
});

test("the CSV download fetches the API path with the dev-user header", async () => {
  const { fetchStub, create, click } = downloadStub("/download.csv");
  show();
  fireEvent.click(await screen.findByTestId("product-download-csv"));
  await waitFor(() => expect(click).toHaveBeenCalled());
  const call = fetchStub.mock.calls.find((c) => String(c[0]).endsWith("/download.csv"))!;
  expect(call[0]).toBe("/api/consumer/products/demo_bond_terms/download.csv");
  expect((call[1] as RequestInit).headers).toMatchObject({ "X-Dev-User": "dev-requester" });
  expect(create).toHaveBeenCalled();
});

test("the Excel download uses its own path", async () => {
  const { fetchStub, click } = downloadStub("/download.xlsx");
  show();
  fireEvent.click(await screen.findByTestId("product-download-xlsx"));
  await waitFor(() => expect(click).toHaveBeenCalled());
  const call = fetchStub.mock.calls.find((c) => String(c[0]).endsWith("/download.xlsx"))!;
  expect(call[0]).toBe("/api/consumer/products/demo_bond_terms/download.xlsx");
  expect((call[1] as RequestInit).headers).toMatchObject({ "X-Dev-User": "dev-requester" });
});

test("not entitled: an info panel with the route replaces preview and downloads", async () => {
  stubFetch({ [PAGE]: productPage({ access: "not_entitled_visible", route: "Ask Olly Owner for access to Bond terms" }) });
  show();
  expect((await screen.findByTestId("product-route")).textContent).toContain("Ask Olly Owner for access to Bond terms");
  expect(screen.queryByTestId("product-preview")).toBeNull();
  expect(screen.queryByTestId("product-download-csv")).toBeNull();
});

test("a 403 on the preview shows the API's route", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => String(input).endsWith("/preview")
    ? new Response(JSON.stringify({ detail: { outcome: "not_entitled", route: "Ask Olly Owner" } }), { status: 403 })
    : new Response(JSON.stringify(productPage()), { status: 200 })));
  show();
  expect((await screen.findByTestId("product-route")).textContent).toContain("Ask Olly Owner");
});

test("no data released yet", async () => {
  vi.stubGlobal("fetch", vi.fn(async () =>
    new Response(JSON.stringify({ detail: { outcome: "no_data" } }), { status: 404 })));
  show();
  expect(await screen.findByText("No data has been released for this product yet.")).toBeTruthy();
});
