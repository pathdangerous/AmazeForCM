import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import type { GlossaryUploadPage } from "../api";
import { PEOPLE, Reply, renderWithTheme, stubFetch } from "../test/fixtures";
import GlossaryUploads from "./GlossaryUploads";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const NAMES = Object.fromEntries(PEOPLE.map((p) => [p.id, p.display_name]));
const AT = "2026-11-25T09:00:00Z";
const DOMAINS = [{ domain: "pricing", label: "Pricing", count: 12 }, { domain: "security_master", label: "Security master", count: 19 }];
const row = (over: Partial<GlossaryUploadPage["rows"][number]>) => ({
  row: 1, key: "best_fcf", label: "new" as const, detail: null, changes: [], used_by_mappings: null, element: {},
  proposal_id: "p1", status: "pending" as const, decided_by: null, note: null, ...over });
const PAGE: GlossaryUploadPage = {
  id: "u1", created_at: AT, uploaded_by: "dev-glossary-owner", file_name: "march.csv", counts: { new: 1, update: 1, invalid: 1, alias_conflict: 1 },
  rows: [
    row({}),
    row({ row: 2, key: "close_price", label: "update", changes: ["definition"], used_by_mappings: 2, proposal_id: "p2" }),
    row({ row: 3, key: "Bad Key", label: "invalid", detail: "bad key 'Bad Key'", proposal_id: null, status: null }),
    row({ row: 4, key: "my_close", label: "alias_conflict", detail: "close_price", proposal_id: null, status: null }),
  ],
};
const SUMMARY = { id: "u1", created_at: AT, uploaded_by: "dev-glossary-owner", file_name: "march.csv", counts: PAGE.counts };

function show(canDecide = true, extra: Record<string, unknown> = {}) {
  const fetchStub = stubFetch({
    "GET /api/amaze/glossary/uploads": [SUMMARY],
    "GET /api/amaze/glossary/uploads/u1": PAGE,
    "GET /api/amaze/glossary/template": "key,name\n",
    ...extra,
  });
  const changed = vi.fn();
  renderWithTheme(<GlossaryUploads devUser="dev-glossary-owner" canDecide={canDecide} people={NAMES} changed={0}
                                   domains={DOMAINS} onChange={changed} />);
  return { fetchStub, changed };
}

const sent = (fetchStub: ReturnType<typeof stubFetch>, method: string, path: string) =>
  fetchStub.mock.calls.filter(([url, init]) => String(url) === path && (init?.method ?? "GET") === method);

test("the owner sees Upload glossary file and Download template; the upload waits for a file", async () => {
  show();
  expect(await screen.findByRole("heading", { name: "Upload glossary file" })).toBeTruthy();
  expect((screen.getByTestId("upload-glossary") as HTMLButtonElement).disabled).toBe(true);
  expect(screen.getByRole("button", { name: "Download template" })).toBeTruthy();
});

test("another role reads the rows but cannot upload or decide", async () => {
  show(false);
  expect(await screen.findByTestId("glossary-upload-rows")).toBeTruthy();
  expect(screen.queryByTestId("upload-glossary")).toBeNull();
  expect(screen.queryByTestId("approve-selected")).toBeNull();
  expect(screen.getByText("Only the glossary owner uploads and decides.")).toBeTruthy();
});

test("a chosen file is posted raw and its rows come back labelled, with a filter by label", async () => {
  const { fetchStub } = show(true, { "POST /api/amaze/glossary/uploads?filename=march.csv": {
    outcome: "uploaded", upload_id: "u1", file_name: "march.csv", counts: PAGE.counts, rows: PAGE.rows, proposals: 2 } });
  const file = new File(["key,name\n"], "march.csv", { type: "text/csv" });
  fireEvent.change(await screen.findByTestId("glossary-file"), { target: { files: [file] } });
  fireEvent.click(screen.getByTestId("upload-glossary"));
  const rows = await screen.findAllByTestId("glossary-upload-row");
  expect(rows.map((r) => r.getAttribute("data-label"))).toEqual(["new", "update", "invalid", "alias_conflict"]);
  expect(sent(fetchStub, "POST", "/api/amaze/glossary/uploads?filename=march.csv")).toHaveLength(1);
  expect(rows[1].textContent).toContain("used by 2 mappings");
  expect(rows[1].textContent).toContain("definition");
  expect(rows[2].textContent).toContain("invalid");
  expect(rows[2].textContent).toContain("bad key 'Bad Key'");
  expect(rows[3].textContent).toContain("alias_conflict (close_price)");
  fireEvent.change(screen.getByLabelText("Filter by label"), { target: { value: "invalid" } });
  expect(screen.getAllByTestId("glossary-upload-row")).toHaveLength(1);
});

test("a refused file says why and shows no rows", async () => {
  show(true, { "POST /api/amaze/glossary/uploads?filename=g.txt": new Reply(422, {
    outcome: "file_refused", reason: "unsupported_format", detail: "Upload a .csv or .xlsx file" }) });
  const file = new File(["x"], "g.txt", { type: "text/plain" });
  fireEvent.change(await screen.findByTestId("glossary-file"), { target: { files: [file] } });
  fireEvent.click(screen.getByTestId("upload-glossary"));
  expect((await screen.findByRole("alert")).textContent).toContain("Upload a .csv or .xlsx file");
});

test("Approve selected sends the ticked pending rows and reports each one", async () => {
  const { fetchStub, changed } = show(true, { "POST /api/amaze/glossary/imports/decision": { items: [
    { outcome: "approved", proposal_id: "p1", element: { key: "best_fcf", version: 1 } },
    { outcome: "refused", proposal_id: "p2", reason: "alias_conflict", detail: "Another element already owns x (alpha)" }] } });
  fireEvent.change(await screen.findByLabelText("Uploaded file"), { target: { value: "u1" } });
  await screen.findAllByTestId("glossary-upload-row");
  expect(screen.queryByLabelText("Select row 3")).toBeNull(); // an invalid row has no proposal: nothing to tick
  fireEvent.click(screen.getByLabelText("Select all pending rows"));
  fireEvent.click(screen.getByTestId("approve-selected"));
  const message = await screen.findByTestId("glossary-import-result");
  expect(message.textContent).toContain("Approved 1");
  expect(message.textContent).toContain("Refused 1");
  expect(message.textContent).toContain("close_price");
  const [, init] = sent(fetchStub, "POST", "/api/amaze/glossary/imports/decision")[0];
  expect(JSON.parse(String(init?.body))).toEqual({ proposal_ids: ["p1", "p2"], decision: "approved", note: null });
  await waitFor(() => expect(changed).toHaveBeenCalled());
});

test("Reject selected needs a note", async () => {
  const { fetchStub } = show(true, { "POST /api/amaze/glossary/imports/decision": { items: [
    { outcome: "rejected", proposal_id: "p1" }] } });
  fireEvent.change(await screen.findByLabelText("Uploaded file"), { target: { value: "u1" } });
  await screen.findAllByTestId("glossary-upload-row");
  fireEvent.click(screen.getByLabelText("Select row 1"));
  const reject = screen.getByTestId("reject-selected") as HTMLButtonElement;
  expect(reject.disabled).toBe(true);
  fireEvent.change(screen.getByLabelText("Note for the selected rows"), { target: { value: "Not now." } });
  expect(reject.disabled).toBe(false);
  fireEvent.click(reject);
  await screen.findByTestId("glossary-import-result");
  const [, init] = sent(fetchStub, "POST", "/api/amaze/glossary/imports/decision")[0];
  expect(JSON.parse(String(init?.body))).toEqual({ proposal_ids: ["p1"], decision: "rejected", note: "Not now." });
});

test("Add element posts a one-row file through the same endpoint and says what became of it", async () => {
  const { fetchStub } = show(true, { "POST /api/amaze/glossary/uploads?filename=add-element.csv": {
    outcome: "uploaded", upload_id: "u1", file_name: "add-element.csv", counts: { new: 1 }, proposals: 1,
    rows: [row({})] } });
  fireEvent.click(await screen.findByRole("button", { name: "Add element" }));
  const form = screen.getByTestId("add-element-form");
  fireEvent.change(within(form).getByLabelText("Key"), { target: { value: "best_fcf" } });
  fireEvent.change(within(form).getByLabelText("Name"), { target: { value: 'Best "free" cash flow' } });
  fireEvent.change(within(form).getByLabelText("Domain"), { target: { value: "pricing" } });
  fireEvent.change(within(form).getByLabelText("Data type"), { target: { value: "decimal" } });
  fireEvent.change(within(form).getByLabelText("Definition"), { target: { value: "Consensus, free cash flow." } });
  fireEvent.change(within(form).getByLabelText("Aliases (separated by ;)"), { target: { value: "fcf_consensus;fcf_est" } });
  fireEvent.click(within(form).getByRole("button", { name: "Propose this element" }));
  expect((await screen.findByTestId("add-element-result")).textContent).toContain("waits for the glossary owner");
  const [, init] = sent(fetchStub, "POST", "/api/amaze/glossary/uploads?filename=add-element.csv")[0];
  expect(await (init?.body as Blob).text()).toBe(
    'key,name,domain,data_type,definition,aliases\r\n"best_fcf","Best ""free"" cash flow","pricing","decimal",' +
    '"Consensus, free cash flow.","fcf_consensus;fcf_est"\r\n');
});

test("Add element names the label when the row is not proposed", async () => {
  show(true, { "POST /api/amaze/glossary/uploads?filename=add-element.csv": {
    outcome: "uploaded", upload_id: "u2", file_name: "add-element.csv", counts: { alias_conflict: 1 }, proposals: 0,
    rows: [row({ label: "alias_conflict", detail: "close_price", proposal_id: null, status: null })] } });
  fireEvent.click(await screen.findByRole("button", { name: "Add element" }));
  const form = screen.getByTestId("add-element-form");
  for (const [label, value] of [["Key", "my_close"], ["Name", "My close"], ["Definition", "Mine."]]) {
    fireEvent.change(within(form).getByLabelText(label), { target: { value } });
  }
  fireEvent.click(within(form).getByRole("button", { name: "Propose this element" }));
  expect((await screen.findByTestId("add-element-result")).textContent).toContain("alias_conflict (close_price)");
});
