import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { inspected, Reply, renderWithTheme, stubFetch, UPLOAD_ID, UPLOAD_PATH, USERS } from "../test/fixtures";
import UploadView, { feedNameFromFilename } from "./UploadView";

const TEAM = USERS[0];
const INSPECT = `POST /api/amaze/uploads/${UPLOAD_ID}/inspect`;
const CONFIRM = `POST /api/amaze/uploads/${UPLOAD_ID}/confirm`;
const UPLOAD = `POST ${UPLOAD_PATH}`;

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function chooseFile(name = "Security Master 2026.xlsx") {
  const file = new File(["x"], name, { type: "application/octet-stream" });
  fireEvent.change(screen.getByLabelText("File to onboard"), { target: { files: [file] } });
  return file;
}

async function uploaded(routes: Record<string, unknown>, onUploaded = vi.fn()) {
  const fetchStub = stubFetch({ [UPLOAD]: inspected(), ...routes });
  renderWithTheme(<UploadView me={TEAM} onUploaded={onUploaded} />);
  chooseFile();
  fireEvent.click(screen.getByTestId("upload-send"));
  await screen.findByTestId("upload-summary");
  return { fetchStub, onUploaded };
}

test("the Upload button is disabled until a file is chosen", () => {
  stubFetch({});
  renderWithTheme(<UploadView me={TEAM} onUploaded={vi.fn()} />);
  const send = screen.getByTestId("upload-send") as HTMLButtonElement;
  expect(send.disabled).toBe(true);
  chooseFile();
  expect(send.disabled).toBe(false);
});

test("the help text states the measured Excel limit and is tied to the file input", () => {
  stubFetch({});
  renderWithTheme(<UploadView me={TEAM} onUploaded={vi.fn()} />);
  const help = screen.getByTestId("upload-help");
  expect(help.textContent).toContain("5,000 rows");
  expect(help.textContent).toContain("0.9 s");
  expect(help.textContent).toContain("20,000 rows");
  expect(screen.getByLabelText("File to onboard").getAttribute("aria-describedby")).toBe("upload-help");
});

test("an inspected answer renders the summary, the preview rows and the sheet select", async () => {
  const { fetchStub } = await uploaded({});
  const summary = screen.getByTestId("upload-summary").textContent ?? "";
  expect(summary).toContain("xlsx");
  expect(summary).toContain("3 rows");
  expect(summary).toContain("2 columns");
  expect(summary).toContain("blank: 2");
  const rows = screen.getByTestId("upload-preview").querySelectorAll("tbody tr");
  expect(rows.length).toBe(3);
  expect(screen.getByTestId("upload-preview").querySelectorAll("thead th").length).toBe(2);
  const select = screen.getByLabelText("Sheet") as HTMLSelectElement;
  expect(Array.from(select.options).map((o) => o.value)).toEqual(["Master", "Notes"]);
  const [, init] = fetchStub.mock.calls[0];
  expect((init?.headers as Record<string, string>)["Content-Type"]).toBe("application/octet-stream");
  expect((init?.headers as Record<string, string>)["X-Dev-User"]).toBe(TEAM.id);
});

test("choosing the second sheet calls inspect with it", async () => {
  const { fetchStub } = await uploaded({ [INSPECT]: inspected({ sheet: "Notes" }) });
  fireEvent.change(screen.getByLabelText("Sheet"), { target: { value: "Notes" } });
  await waitFor(() => expect(fetchStub.mock.calls.some(([u]) => String(u).endsWith("/inspect"))).toBe(true));
  const call = fetchStub.mock.calls.find(([u]) => String(u).endsWith("/inspect"))!;
  expect(JSON.parse(call[1]?.body as string)).toMatchObject({ filename: "Security Master 2026.xlsx", sheet: "Notes" });
});

test("changing the header row calls inspect with it", async () => {
  const { fetchStub } = await uploaded({ [INSPECT]: inspected({ header_row: 3 }) });
  fireEvent.change(screen.getByLabelText("Header row"), { target: { value: "3" } });
  await waitFor(() => expect(fetchStub.mock.calls.some(([u]) => String(u).endsWith("/inspect"))).toBe(true));
  const call = fetchStub.mock.calls.find(([u]) => String(u).endsWith("/inspect"))!;
  expect(JSON.parse(call[1]?.body as string)).toMatchObject({ header_row: 3 });
});

test("the feed code and name are prefilled from the file name, snake case", async () => {
  await uploaded({});
  expect((screen.getByLabelText("Feed code") as HTMLInputElement).value).toBe("security_master_2026");
  expect((screen.getByLabelText("Feed name") as HTMLInputElement).value).toBe("Security Master 2026");
  expect((screen.getByTestId("upload-confirm") as HTMLButtonElement).disabled).toBe(false);
});

test("the feed name default turns underscores and hyphens into single spaces, case kept; the code stays snake case", async () => {
  stubFetch({ "POST /api/amaze/uploads?filename=security_master-2019.csv": inspected({
    filename: "security_master-2019.csv", format: "csv", sheets: [], sheet: null, encoding: "utf-8", delimiter: "," }) });
  renderWithTheme(<UploadView me={TEAM} onUploaded={vi.fn()} />);
  chooseFile("security_master-2019.csv");
  fireEvent.click(screen.getByTestId("upload-send"));
  await screen.findByTestId("upload-summary");
  expect((screen.getByLabelText("Feed name") as HTMLInputElement).value).toBe("security master 2019");
  expect((screen.getByLabelText("Feed code") as HTMLInputElement).value).toBe("security_master_2019");
});

test("the file name default collapses runs of separators and trims them", () => {
  expect(feedNameFromFilename("__Sec__Master--  2019 _.csv")).toBe("Sec Master 2019");
});

test("the preview shows 10 rows at most, in a container that scrolls sideways on its own", async () => {
  const preview = Array.from({ length: 14 }, (_, i) => [`R${i}`, String(i)]);
  await uploaded({ [UPLOAD]: inspected({ preview, row_count: 14 }) });
  expect(screen.getByTestId("upload-preview").querySelectorAll("tbody tr").length).toBe(10);
  expect(screen.getByTestId("upload-preview-scroll").style.overflowX).toBe("auto");
});

test("placeholder and empty cells are highlighted with the warning fill and say why in their title", async () => {
  await uploaded({ [UPLOAD]: inspected({ preview: [["ALL", "10"], [" n/a ", "-"], ["N/A", null], ["", "x"], ["AAA", "y"]] }) });
  const table = screen.getByTestId("upload-preview");
  const cells = Array.from(table.querySelectorAll("tbody td")) as HTMLElement[];
  const titled = cells.filter((c) => c.getAttribute("title"));
  expect(titled.map((c) => c.getAttribute("title"))).toEqual(
    ["Placeholder", "Placeholder", "Placeholder", "Placeholder", "Empty", "Empty"]);
  expect(titled[0].style.background).toContain("var(--amz-warning)");
  expect(titled[0].style.color).toBe("var(--amz-on-bright)");
  const plain = cells.filter((c) => !c.getAttribute("title"));
  expect(plain.map((c) => c.textContent)).toEqual(["10", "x", "AAA", "y"]);
  expect(screen.getByTestId("upload-legend").textContent).toContain("Highlighted cells hold a placeholder");
});

test("header cells do not wrap", async () => {
  await uploaded({});
  const th = screen.getByTestId("upload-preview").querySelector("thead th") as HTMLElement;
  expect(th.style.whiteSpace).toBe("nowrap");
});

test("an invalid code shows the inline error and disables confirm", async () => {
  await uploaded({});
  fireEvent.change(screen.getByLabelText("Feed code"), { target: { value: "Bad Code" } });
  expect(screen.getByText(/lower-case letters, digits and underscores/)).toBeTruthy();
  expect((screen.getByTestId("upload-confirm") as HTMLButtonElement).disabled).toBe(true);
});

test("the conditions of use default to internal use and can be edited", async () => {
  await uploaded({});
  fireEvent.click(screen.getByText("Conditions of use"));
  expect((screen.getByLabelText("Licence") as HTMLInputElement).value).toBe("Internal use only");
  expect((screen.getByLabelText("Permitted use") as HTMLInputElement).value).toBe("Internal analysis");
  expect((screen.getByLabelText("Redistribution") as HTMLInputElement).value).toBe("None");
  expect(screen.getByLabelText("Description")).toBeTruthy();
});

test("too_large renders the error panel with the detail and keeps the file input usable", async () => {
  stubFetch({ [UPLOAD]: new Reply(413, { outcome: "too_large", size_bytes: 9, max_bytes: 5,
                                         detail: "The file is over the limit of 5 MB" }) });
  renderWithTheme(<UploadView me={TEAM} onUploaded={vi.fn()} />);
  chooseFile();
  fireEvent.click(screen.getByTestId("upload-send"));
  const panel = await screen.findByTestId("upload-error");
  expect(panel.textContent).toContain("The file is over the limit of 5 MB");
  expect((screen.getByLabelText("File to onboard") as HTMLInputElement).disabled).toBe(false);
});

test("unsupported_format and not_a_table render the error panel", async () => {
  stubFetch({ [UPLOAD]: new Reply(422, { outcome: "unsupported_format", filename: "a.doc", detail: "Not a table format" }) });
  renderWithTheme(<UploadView me={TEAM} onUploaded={vi.fn()} />);
  chooseFile();
  fireEvent.click(screen.getByTestId("upload-send"));
  expect((await screen.findByTestId("upload-error")).textContent).toContain("Not a table format");
});

test("needs_image_extraction renders the info panel", async () => {
  stubFetch({ "POST /api/amaze/uploads?filename=scan.pdf": { outcome: "needs_image_extraction", upload_id: UPLOAD_ID, pages: [1, 2] } });
  renderWithTheme(<UploadView me={TEAM} onUploaded={vi.fn()} />);
  chooseFile("scan.pdf");
  fireEvent.click(screen.getByTestId("upload-send"));
  const panel = await screen.findByTestId("upload-info");
  expect(panel.textContent).toContain("This PDF is scanned: the model will read its pages (next step)");
});

test("uploaded calls onUploaded with the feed id", async () => {
  const { fetchStub, onUploaded } = await uploaded({
    [CONFIRM]: { outcome: "uploaded", feed_id: "feed-123", feed_code: "security_master_2026", fetch_id: "f", row_count: 3,
                 columns: ["instr_id", "qty"] } });
  fireEvent.click(screen.getByTestId("upload-confirm"));
  await waitFor(() => expect(onUploaded).toHaveBeenCalledWith("feed-123"));
  const call = fetchStub.mock.calls.find(([u]) => String(u).endsWith("/confirm"))!;
  expect(JSON.parse(call[1]?.body as string)).toMatchObject({
    feed_code: "security_master_2026", feed_name: "Security Master 2026", sheet: "Master", header_row: 1 });
});

test("a 409 on confirm says the feed code is taken, next to the code field", async () => {
  await uploaded({ [CONFIRM]: new Reply(409, { detail: "Feed code already exists" }) });
  fireEvent.click(screen.getByTestId("upload-confirm"));
  expect(await screen.findByText("The feed code is taken")).toBeTruthy();
});

test("a user without the backend_team role sees the role message and no form", () => {
  stubFetch({});
  renderWithTheme(<UploadView me={USERS[1]} onUploaded={vi.fn()} />);
  expect(screen.getByText("Uploading needs the backend team role.")).toBeTruthy();
  expect(screen.queryByTestId("upload-file")).toBeNull();
});

test("loading shows the reading message", async () => {
  stubFetch({ [UPLOAD]: inspected() });
  renderWithTheme(<UploadView me={TEAM} onUploaded={vi.fn()} />);
  chooseFile();
  fireEvent.click(screen.getByTestId("upload-send"));
  expect(screen.getByText("Reading the file…")).toBeTruthy();
  await screen.findByTestId("upload-summary");
});
