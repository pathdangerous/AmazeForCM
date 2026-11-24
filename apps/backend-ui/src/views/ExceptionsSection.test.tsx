import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { FEED_ID, renderWithTheme, stubFetch } from "../test/fixtures";
import ExceptionsSection from "./ExceptionsSection";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const FEED_PATH = `GET /api/amaze/feeds/${FEED_ID}/exceptions`;
const counts = (over = {}) => ({ load_id: "l1", received_at: "2026-11-24T08:00:00Z", status: "held", purpose: "scheduled",
  rows: 10, flagged_rows: 2, exceptions: 3, ...over });
const group = (over = {}) => ({ group_key: "g1", rule_key: "best_sales_not_negative", description: "Best sales is not negative.",
  elements: ["best_sales"], pattern: "-9.9", exceptions: 2, state: "open",
  samples: [{ row_number: 4, values: { best_sales: "-12.5", isin: "X" } }], ...over });
const view = (over = {}) => ({ load_id: "l1", feed_id: FEED_ID, status: "held", can_fix: true, rows: 10, flagged_rows: 2,
  exceptions: 3, total_groups: 1, limit: 50, offset: 0, groups: [group()], ...over });

test("nothing is shown for a feed whose loads have no exception", async () => {
  const fetchStub = stubFetch({ [FEED_PATH]: [counts({ exceptions: 0, flagged_rows: 0 })] });
  renderWithTheme(<ExceptionsSection feedId={FEED_ID} devUser="dev-operator" />);
  await waitFor(() => expect(fetchStub).toHaveBeenCalled());
  expect(screen.queryByTestId("section-exceptions")).toBeNull();
});

test("a held load's groups show the rule, the pattern, the count and sample rows of the rule's columns", async () => {
  stubFetch({ [FEED_PATH]: [counts()], "GET /api/amaze/loads/l1/exceptions?offset=0": view() });
  renderWithTheme(<ExceptionsSection feedId={FEED_ID} devUser="dev-operator" />);
  const row = await screen.findByTestId("exception-group-g1");
  expect(row.textContent).toContain("best_sales_not_negative");
  expect(row.textContent).toContain("-9.9");
  expect(row.textContent).toContain("row 4: best_sales=-12.5");
  expect(row.textContent).not.toContain("isin");
  expect(screen.getByTestId("exceptions-summary").textContent).toContain("3 exceptions in 2 of 10 rows, 1 groups");
  expect(screen.getByTestId("exceptions-summary").textContent).toContain("each group can take a fix");
});

const fix = (over = {}) => ({ proposal_id: "p1", kind: "exception_fix", action: "waive", reason: "Credits are real.",
  origin: "agent", author: null, status: "pending", approvals: 1, required_approver_roles: ["data_steward", "data_owner"],
  steps: [], ...over });

test("each group shows its fix, who made it and who decides next", async () => {
  stubFetch({ [FEED_PATH]: [counts()], "GET /api/amaze/loads/l1/exceptions?offset=0": view({ groups: [
    group({ state: "waiting_for_owner", fix: fix() }),
    group({ group_key: "g2", state: "fix_pending", fix: fix({ proposal_id: "p2", kind: "transformation", action: "transformation",
      origin: "manual", author: "dev-data-steward", approvals: 0, required_approver_roles: ["data_steward"], steps: [{ op: "trim" }] }) }),
  ] }) });
  renderWithTheme(<ExceptionsSection feedId={FEED_ID} devUser="dev-data-steward" roles={["data_steward"]} />);
  const waived = await screen.findByTestId("exception-group-g1");
  expect(within(waived).getByTestId("exception-fix").textContent).toContain("Waive: Credits are real. (agent)");
  expect(within(waived).getByTestId("exception-fix").textContent).toContain("waits for the data owner");
  expect(within(screen.getByTestId("exception-group-g2")).getByTestId("exception-fix").textContent).toContain(
    "Transformation trim (added by dev-data-steward)");
});

test("a steward proposes fixes for the open groups", async () => {
  const fetchStub = stubFetch({ [FEED_PATH]: [counts()], "GET /api/amaze/loads/l1/exceptions?offset=0": view(),
    "POST /api/amaze/loads/l1/exceptions/propose": { result: { outcome: "fixes_proposed", log_id: "x", proposal_ids: ["p1"], no_fix: [] },
      exceptions: view({ groups: [group({ state: "fix_pending", fix: fix({ approvals: 0 }) })] }) } });
  renderWithTheme(<ExceptionsSection feedId={FEED_ID} devUser="dev-data-steward" roles={["data_steward"]} />);
  fireEvent.click(await screen.findByRole("button", { name: "Propose fixes" }));
  await waitFor(() => expect(fetchStub.mock.calls.some(([u, i]) => String(u).endsWith("/exceptions/propose") && i?.method === "POST")).toBe(true));
  expect((await screen.findByTestId("exception-fix")).textContent).toContain("waits for the data steward");
});

test("a steward adds a fix by hand; a person without the role sees no button", async () => {
  const fetchStub = stubFetch({ [FEED_PATH]: [counts()], "GET /api/amaze/loads/l1/exceptions?offset=0": view(),
    "POST /api/amaze/loads/l1/exceptions/fixes": { proposal: {}, exceptions: view() } });
  const { unmount } = renderWithTheme(<ExceptionsSection feedId={FEED_ID} devUser="dev-data-steward" roles={["data_steward"]} />);
  fireEvent.click(await screen.findByRole("button", { name: "Add a fix" }));
  fireEvent.change(screen.getByLabelText("Fix"), { target: { value: "refer" } });
  fireEvent.change(screen.getByLabelText("Reason"), { target: { value: "Ask the provider." } });
  fireEvent.click(screen.getByRole("button", { name: "Add fix" }));
  await waitFor(() => expect(fetchStub.mock.calls.some(([u, i]) => String(u).endsWith("/exceptions/fixes")
    && String(i?.body).includes('"action":"refer"') && String(i?.body).includes('"group_key":"g1"'))).toBe(true));
  unmount();
  renderWithTheme(<ExceptionsSection feedId={FEED_ID} devUser="dev-data-owner" roles={["data_owner"]} />);
  await screen.findByTestId("exception-group-g1");
  expect(screen.queryByRole("button", { name: "Propose fixes" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Add a fix" })).toBeNull();
});

test("selected groups are decided in bulk; a rejection needs a note", async () => {
  const fetchStub = stubFetch({ [FEED_PATH]: [counts()], "GET /api/amaze/loads/l1/exceptions?offset=0": view({ groups: [
    group({ state: "fix_pending", fix: fix({ approvals: 0 }) }), group({ group_key: "g2", rule_key: "other", fix: null })] }),
    "POST /api/amaze/loads/l1/exceptions/decision": { result: { outcome: "fixes_decided" }, exceptions: view() } });
  renderWithTheme(<ExceptionsSection feedId={FEED_ID} devUser="dev-data-steward" roles={["data_steward"]} />);
  await screen.findByTestId("exception-group-g1");
  expect(screen.queryByLabelText("Select group g2")).toBeNull();  // no fix to decide yet
  const approve = screen.getByRole("button", { name: "Approve selected" }) as HTMLButtonElement;
  expect(approve.disabled).toBe(true);
  fireEvent.click(screen.getByLabelText("Select group g1"));
  expect(approve.disabled).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "Reject selected" }));
  const confirm = screen.getByRole("button", { name: "Confirm rejection" }) as HTMLButtonElement;
  expect(confirm.disabled).toBe(true);
  fireEvent.click(approve);
  await waitFor(() => expect(fetchStub.mock.calls.some(([u, i]) => String(u).endsWith("/exceptions/decision")
    && String(i?.body).includes('"group_keys":["g1"]') && String(i?.body).includes('"decision":"approved"'))).toBe(true));
});

test("a held load is rerun with a reason; the typed result is said in words", async () => {
  const fetchStub = stubFetch({ [FEED_PATH]: [counts()], "GET /api/amaze/loads/l1/exceptions?offset=0": view(),
    "POST /api/amaze/loads/l1/rerun": { result: { outcome: "rerun_still_held", load_id: "l2", trust_score: "83.3",
      previous_score: "50.0", open_groups: 1 } } });
  renderWithTheme(<ExceptionsSection feedId={FEED_ID} devUser="dev-data-steward" roles={["data_steward"]} />);
  await screen.findByTestId("exception-group-g1");
  const rerun = screen.getByRole("button", { name: "Rerun this load" }) as HTMLButtonElement;
  expect(rerun.disabled).toBe(true);  // a reason first
  fireEvent.change(screen.getByLabelText("Reason for the rerun"), { target: { value: "Waived." } });
  fireEvent.click(rerun);
  await waitFor(() => expect(fetchStub.mock.calls.some(([u, i]) => String(u).endsWith("/loads/l1/rerun")
    && String(i?.body).includes('"reason":"Waived."'))).toBe(true));
  const said = await screen.findByTestId("rerun-result");
  expect(said.textContent).toContain("still held");
  expect(said.textContent).toContain("83.3");
  expect(said.textContent).toContain("50.0");
});

test("a rerun that needs the owner's go-live says so", async () => {
  stubFetch({ [FEED_PATH]: [counts()], "GET /api/amaze/loads/l1/exceptions?offset=0": view(),
    "POST /api/amaze/loads/l1/rerun": { result: { outcome: "rerun_needs_go_live", load_id: "l1", detail: "The owner approves a go-live." } } });
  renderWithTheme(<ExceptionsSection feedId={FEED_ID} devUser="dev-data-steward" roles={["data_steward"]} />);
  await screen.findByTestId("exception-group-g1");
  fireEvent.change(screen.getByLabelText("Reason for the rerun"), { target: { value: "Trimmed." } });
  fireEvent.click(screen.getByRole("button", { name: "Rerun this load" }));
  expect((await screen.findByTestId("rerun-result")).textContent).toContain("needs the data owner's go-live");
});

test("a released load's groups are for information only", async () => {
  stubFetch({ [FEED_PATH]: [counts({ status: "released" })],
              "GET /api/amaze/loads/l1/exceptions?offset=0": view({ status: "released", can_fix: false }) });
  renderWithTheme(<ExceptionsSection feedId={FEED_ID} devUser="dev-operator" />);
  expect((await screen.findByTestId("exceptions-summary")).textContent).toContain("for information only");
});
