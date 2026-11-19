import { act, cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import type { OnboardingItem, StepName } from "../api";
import {
  glossaryItem, mappingItem, onboardingItem, profileItem, Reply, renderWithTheme, reviewItem, ruleItem, SESSION_ID,
  stepInputs, stubFetch, TIMELINE, tieItem, transformationItem, workspace, WS_PATH,
} from "../test/fixtures";
import WorkspaceView from "./WorkspaceView";

const GET = `GET ${WS_PATH}`;
const base = (extra: Record<string, unknown> = {}): Record<string, unknown> => ({
  "GET /api/health": { status: "ok", four_eyes: false },
  [`GET ${WS_PATH}/timeline`]: TIMELINE,
  ...extra,
});
const show = () => renderWithTheme(<WorkspaceView id={SESSION_ID} devUser="dev-backend-team" />);

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

test("it renders the stepper, the items, the agent panel and the footer", async () => {
  stubFetch(base({ [GET]: workspace("mapping") }));
  show();
  expect(await screen.findByTestId("item-00000000-0000-4000-8000-0000000000e1")).toBeTruthy();
  expect(screen.getByRole("navigation", { name: "Onboarding steps" })).toBeTruthy();
  expect(screen.getByTestId("agent-panel")).toBeTruthy();
  expect(screen.getByTestId("workspace-footer")).toBeTruthy();
  const approve = screen.getByTestId("workspace-approve") as HTMLButtonElement;
  expect(approve.disabled).toBe(false);
  expect(approve.textContent).toBe("Approve & next");
});

test("an item that needs a decision disables Approve and shows why", async () => {
  const blocked = "1 item needs a decision";
  stubFetch(base({ [GET]: workspace("mapping", "needs_decision", {
    items: [onboardingItem({ needs_decision: true, pre_accepted: false, confidence: 0.4 })],
    can_approve: false, blocked_by: blocked }) }));
  show();
  const approve = (await screen.findByTestId("workspace-approve")) as HTMLButtonElement;
  expect(approve.disabled).toBe(true);
  expect(screen.getByTestId("workspace-footer").textContent).toContain(blocked);
  expect(screen.getByText("needs decision", { selector: "[data-testid^=item-badge]" })).toBeTruthy();
});

test("a 409 decision_needed marks the items and tells the person", async () => {
  const item = onboardingItem({ needs_decision: true, confidence: 0.4 });
  stubFetch(base({
    [GET]: workspace("mapping"),
    [`POST ${WS_PATH}/approve`]: new Reply(409, { detail: { outcome: "decision_needed", message: "Decide first", items: [item] } }),
  }));
  show();
  fireEvent.click(await screen.findByTestId("workspace-approve"));
  await waitFor(() => expect(screen.getByTestId("workspace-footer").textContent).toContain("Decide the marked items first"));
  expect(screen.getByTestId(`item-${item.proposal_id}`).getAttribute("data-marked")).toBe("true");
});

test("Approve & next sends the call and shows the next workspace", async () => {
  const next = workspace("transformations", "ready", { items: [onboardingItem({ proposal_id: "00000000-0000-4000-8000-0000000000e9", kind: "transformation" })] });
  const fetchStub = stubFetch(base({
    [GET]: workspace("mapping"),
    [`POST ${WS_PATH}/approve`]: { advance: { moved: true, current_step: "transformations", approved: [], waiting_for: null }, workspace: next },
  }));
  show();
  fireEvent.click(await screen.findByTestId("workspace-approve"));
  expect(await screen.findByTestId("item-00000000-0000-4000-8000-0000000000e9")).toBeTruthy();
  const call = fetchStub.mock.calls.find(([, init]) => init?.method === "POST");
  expect(JSON.parse(String(call?.[1]?.body))).toEqual({}); // visibility goes with go-live only
});

test("the profile step has no visibility choice", async () => {
  stubFetch(base({ [GET]: workspace("profile", "ready", { items: [profileItem()] }) }));
  show();
  await screen.findByTestId("profile-outcome");
  expect(screen.queryByLabelText("Open to every user")).toBeNull();
});

test("the go-live page asks for the visibility (default: the registration's) and sends it there", async () => {
  const fetchStub = stubFetch(base({
    [GET]: workspace("go_live", "ready", { items: [reviewItem()], visibility: "open" }),
    [`POST ${WS_PATH}/approve`]: { advance: { moved: true, current_step: "first_load", approved: [], waiting_for: null }, workspace: workspace("first_load") },
  }));
  show();
  const open = (await screen.findByLabelText("Open to every user")) as HTMLInputElement;
  expect(open.type).toBe("radio");
  expect(open.checked).toBe(true);
  fireEvent.click(screen.getByLabelText("Restricted to entitled users"));
  fireEvent.click(screen.getByTestId("workspace-approve"));
  await waitFor(() => expect(fetchStub.mock.calls.some(([, init]) => init?.method === "POST")).toBe(true));
  const call = fetchStub.mock.calls.find(([, init]) => init?.method === "POST");
  expect(JSON.parse(String(call?.[1]?.body))).toEqual({ visibility: "restricted" });
});

test("one decision click calls the decision endpoint once, and no buttons remain on that item", async () => {
  const item = tieItem();
  const decided = { ...item, status: "approved", needs_decision: false };
  const fetchStub = stubFetch(base({
    [GET]: workspace("mapping", "needs_decision", { items: [item], can_approve: false, blocked_by: "1 item needs a decision" }),
    [`POST ${WS_PATH}/items/${item.proposal_id}/decision`]: { result: {}, workspace: workspace("mapping", "ready", { items: [decided] }) },
  }));
  show();
  const row = await screen.findByTestId(`item-${item.proposal_id}`);
  fireEvent.change(within(row).getByLabelText("Choice for Price"), { target: { value: "1" } });
  fireEvent.click(within(row).getByRole("button", { name: "Approve" }));
  await waitFor(() => expect(within(screen.getByTestId(`item-${item.proposal_id}`)).queryAllByRole("button").length).toBe(0));
  const calls = fetchStub.mock.calls.filter(([u]) => String(u).endsWith("/decision"));
  expect(calls.length).toBe(1);
  expect(JSON.parse(String(calls[0][1]?.body))).toEqual({ decision: "approved", choice: "ask_price" });
});

test("a pre-accepted item has no Approve button, and the footer says what Approve & next will do", async () => {
  stubFetch(base({ [GET]: workspace("mapping", "ready", { items: [mappingItem(), mappingItem({
    proposal_id: "00000000-0000-4000-8000-0000000000e7", column: "Name" })] }) }));
  show();
  const rows = await screen.findAllByText("Pre-accepted");
  expect(rows.length).toBe(2);
  expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
  expect(screen.getByTestId("workspace-preaccepted").textContent).toBe("Approves 2 pre-accepted items");
});

test("with no pre-accepted items the footer says nothing about them", async () => {
  stubFetch(base({ [GET]: workspace("mapping", "needs_decision", { items: [tieItem()], can_approve: false, blocked_by: "x" }) }));
  show();
  await screen.findByTestId(`item-${tieItem().proposal_id}`);
  expect(screen.queryByTestId("workspace-preaccepted")).toBeNull();
});

test("a rejected item can be edited with the kind's form; the changed field is sent", async () => {
  const id = "00000000-0000-4000-8000-0000000000e1";
  const fetchStub = stubFetch(base({
    [GET]: workspace("mapping"),
    [`POST ${WS_PATH}/items/${id}/decision`]: { result: {}, workspace: workspace("mapping") },
    [`POST ${WS_PATH}/items/${id}/edit`]: { proposal: {}, workspace: workspace("mapping") },
  }));
  show();
  const row = await screen.findByTestId(`item-${id}`);
  fireEvent.click(within(row).getByRole("button", { name: "Reject" }));
  fireEvent.change(within(row).getByLabelText("Why it is rejected"), { target: { value: "Wrong element" } });
  fireEvent.click(within(row).getByRole("button", { name: "Confirm" }));
  await waitFor(() => expect(fetchStub.mock.calls.some(([u]) => String(u).endsWith("/decision"))).toBe(true));
  const decision = fetchStub.mock.calls.find(([u]) => String(u).endsWith("/decision"))!;
  expect(JSON.parse(String(decision[1]?.body))).toEqual({ decision: "rejected", note: "Wrong element" }); // the note goes in the request

  fireEvent.click(within(row).getByRole("button", { name: "Edit" }));
  expect(screen.queryByLabelText("Edited proposal (JSON)")).toBeNull();
  fireEvent.change(screen.getByLabelText("Element"), { target: { value: "instrument_ticker" } });
  fireEvent.click(screen.getByRole("button", { name: "Save edit" }));
  await waitFor(() => expect(fetchStub.mock.calls.some(([u]) => String(u).endsWith("/edit"))).toBe(true));
  const call = fetchStub.mock.calls.find(([u]) => String(u).endsWith("/edit"))!;
  expect(JSON.parse(String(call[1]?.body))).toEqual({ payload: expect.objectContaining({
    source_field: "Ticker", candidates: ["instrument_ticker"], recommended: "instrument_ticker" }) });
});

test("an edit the API refuses shows its message in the form", async () => {
  const id = "00000000-0000-4000-8000-0000000000e1";
  stubFetch(base({
    [GET]: workspace("mapping"),
    [`POST ${WS_PATH}/items/${id}/edit`]: new Reply(422, { detail: "The edited mapping proposal is not valid" }),
  }));
  show();
  fireEvent.click(within(await screen.findByTestId(`item-${id}`)).getByRole("button", { name: "Edit" }));
  fireEvent.click(screen.getByRole("button", { name: "Save edit" }));
  expect((await screen.findByRole("alert")).textContent).toContain("The edited mapping proposal is not valid");
});

test("an empty step offers to run the agent; a failed one offers to try again", async () => {
  const fetchStub = stubFetch(base({
    [GET]: workspace("glossary", "waiting", { items: [], agent_notes: [] }),
    [`POST ${WS_PATH}/steps/glossary/enter`]: workspace("glossary", "waiting", { items: [], agent_notes: [] }),
  }));
  show();
  fireEvent.click(await screen.findByRole("button", { name: "Run the agent" }));
  await waitFor(() => expect(fetchStub.mock.calls.some(([u]) => String(u).endsWith("/steps/glossary/enter"))).toBe(true));
  cleanup();
  stubFetch(base({ [GET]: workspace("glossary", "failed", {
    items: [], steps: workspace("glossary", "failed").steps.map((s) => s.name === "glossary" ? { ...s, detail: "The model call failed" } : s),
    can_approve: false, blocked_by: "The step failed" }) }));
  show();
  expect(await screen.findByText("The model call failed")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Try again" })).toBeTruthy();
  expect((screen.getByTestId("workspace-approve") as HTMLButtonElement).disabled).toBe(true);
});

const HELD_LOAD = () => workspace("first_load", "failed", {
  items: [], inputs: stepInputs({ facts: [{ label: "Load", value: "held" }] }), can_approve: false, blocked_by: "The step failed",
  steps: workspace("first_load", "failed").steps.map((s) => s.name === "first_load" ? { ...s, detail: "The first load ended held." } : s) });

test("a first load that did not release offers Back to Rules and Upload a new file, and Back to Rules goes back", async () => {
  const fetchStub = stubFetch(base({
    [GET]: HELD_LOAD(),
    [`POST ${WS_PATH}/back`]: workspace("rules", "ready", { items: [ruleItem()] }),
  }));
  show();
  expect(await screen.findByText("The first load ended held.")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
  expect(screen.getByRole("button", { name: "Upload a new file" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Back to Rules" }));
  await waitFor(() => expect(fetchStub.mock.calls.some(([u]) => String(u).endsWith("/back"))).toBe(true));
  const call = fetchStub.mock.calls.find(([u]) => String(u).endsWith("/back"))!;
  expect(JSON.parse(String(call[1]?.body))).toEqual({ to_step: "rules" });
});

test("the alert above a failed first load tells about the latest load, from the same data as the facts", async () => {
  const held = HELD_LOAD();
  stubFetch(base({ [GET]: { ...held, inputs: { ...held.inputs!, load_summary: "The latest load ended held: score 80.0." } } }));
  show();
  expect((await screen.findByRole("alert")).textContent).toBe("The latest load ended held: score 80.0.");
});

test("Upload a new file writes the file to the feed's drop folder, then loads again", async () => {
  const fetchStub = stubFetch(base({
    [GET]: HELD_LOAD(),
    [`POST /api/amaze/feeds/${HELD_LOAD().session.feed_id}/files?filename=next.csv`]: { stored_as: "next.csv" },
    [`POST ${WS_PATH}/steps/first_load/enter`]: workspace("first_load", "agent_working", { items: [] }),
  }));
  show();
  await screen.findByText("The first load ended held.");
  const file = new File(["a,b\n1,2\n"], "next.csv", { type: "text/csv" });
  fireEvent.change(screen.getByTestId("first-load-file"), { target: { files: [file] } });
  await waitFor(() => expect(fetchStub.mock.calls.some(([u]) => String(u).endsWith("/steps/first_load/enter"))).toBe(true));
  const urls = fetchStub.mock.calls.map(([u]) => String(u));
  expect(urls.findIndex((u) => u.includes("/files?filename=next.csv"))).toBeLessThan(urls.findIndex((u) => u.endsWith("/steps/first_load/enter")));
});

test("a first file that was never fetched still offers Try again", async () => {
  stubFetch(base({ [GET]: workspace("first_load", "failed", { items: [], can_approve: false, blocked_by: "The step failed",
    steps: workspace("first_load", "failed").steps.map((s) => s.name === "first_load" ? { ...s, detail: "The first file was not fetched: source_down." } : s) }) }));
  show();
  expect(await screen.findByRole("button", { name: "Try again" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Back to Rules" })).toBeNull();
});

test("while the agent works it shows the progress and polls every 2 seconds, then stops", async () => {
  vi.useFakeTimers();
  const working = workspace("glossary", "agent_working", {
    items: [], job: { id: "j1", session_id: SESSION_ID, step: "glossary", status: "running", error: null },
    progress: "Checking column 3 of 6", can_approve: false, blocked_by: "The agent is working" });
  const routes = base({ [GET]: working });
  const fetchStub = stubFetch(routes);
  const gets = () => fetchStub.mock.calls.filter(([u, init]) => String(u) === WS_PATH && !init?.method).length;
  show();
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  expect(screen.getByTestId("workspace-working").textContent).toContain("Checking column 3 of 6");
  expect(gets()).toBe(1);
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  expect(gets()).toBe(2);
  routes[GET] = workspace("glossary", "ready");
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  expect(gets()).toBe(3);
  expect(screen.queryByTestId("workspace-working")).toBeNull();
  await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
  expect(gets()).toBe(3); // done: no more polling
});

test("polling stops when the view goes away", async () => {
  vi.useFakeTimers();
  const working = workspace("glossary", "agent_working", {
    items: [], job: { id: "j1", session_id: SESSION_ID, step: "glossary", status: "queued", error: null } });
  const fetchStub = stubFetch(base({ [GET]: working }));
  const { unmount } = show();
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  const before = fetchStub.mock.calls.length;
  unmount();
  await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
  expect(fetchStub.mock.calls.length).toBe(before);
});

test("before go-live the main panel is the summary page and the button says Approve go-live", async () => {
  stubFetch(base({ [GET]: workspace("go_live", "ready", {
    items: [reviewItem()], inputs: stepInputs({ facts: [{ label: "Test load", value: "loaded, 70 rows" }] }) }) }));
  show();
  const page = await screen.findByTestId("summary-page");
  expect(page.textContent).toContain("All 6 columns are mapped; the test load passed.");
  expect(page.textContent).toContain("Test load: loaded, 70 rows");
  expect(page.textContent).toContain("Glossary");
  expect(screen.getByTestId("workspace-approve").textContent).toBe("Approve go-live");
});

test("when the product is ready it links to the consumer app and offers another file", async () => {
  stubFetch(base({ [GET]: workspace("product_ready", "done", { items: [] }) }));
  show();
  const link = await screen.findByRole("link", { name: /Open the product/ });
  expect(link.getAttribute("href")).toBe("http://127.0.0.1:5174/#/product/security_master");
  expect(screen.getByRole("link", { name: "Onboard another file" }).getAttribute("href")).toBe("#/onboard");
});

test("with four-eyes on a banner points to Feeds", async () => {
  stubFetch(base({ "GET /api/health": { status: "ok", four_eyes: true }, [GET]: workspace("mapping") }));
  show();
  expect(await screen.findByText("Four-eyes is on: a second person decides the next step")).toBeTruthy();
  expect(screen.getByText("Use Feeds for the multi-role flow")).toBeTruthy();
});

test("Back is one control: disabled with a hint until an earlier done step is viewed", async () => {
  stubFetch(base({ [GET]: workspace("mapping") }));
  show();
  const back = (await screen.findByTestId("workspace-back")) as HTMLButtonElement;
  expect(screen.queryByRole("combobox", { name: "Go back to" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Back to the current step" })).toBeNull();
  expect(back.textContent).toBe("Back");
  expect(back.disabled).toBe(true);
  expect(screen.getByTestId("workspace-footer").textContent).toContain("Pick an earlier step in the list to go back to it");
});

test("viewing a done step fetches ?step=, shows it without decision buttons, and Back goes to it", async () => {
  const done = workspace("mapping", "ready", {
    viewed_step: "glossary", items: [glossaryItem({ status: "approved", pre_accepted: false })] });
  const fetchStub = stubFetch(base({
    [GET]: workspace("mapping"),
    [`${GET}?step=glossary`]: done,
    [`POST ${WS_PATH}/back`]: workspace("glossary", "stale"),
  }));
  show();
  await screen.findByTestId("item-00000000-0000-4000-8000-0000000000e1");
  fireEvent.click(screen.getByTestId("step-glossary"));
  const row = await screen.findByTestId(`item-${glossaryItem().proposal_id}`);
  expect(fetchStub.mock.calls.some(([u]) => String(u) === `${WS_PATH}?step=glossary`)).toBe(true);
  expect(within(row).queryAllByRole("button").length).toBe(0);
  expect(screen.getByTestId("workspace-approve")).toHaveProperty("disabled", true);
  const back = screen.getByTestId("workspace-back") as HTMLButtonElement;
  expect(back.textContent).toBe("Go back to Glossary");
  expect(back.disabled).toBe(false);
  fireEvent.click(back);
  await waitFor(() => expect(fetchStub.mock.calls.some(([u]) => String(u).endsWith("/back"))).toBe(true));
  const call = fetchStub.mock.calls.find(([u]) => String(u).endsWith("/back"))!;
  expect(JSON.parse(String(call[1]?.body))).toEqual({ to_step: "glossary" });
});

test("clicking the current step again returns to it", async () => {
  stubFetch(base({
    [GET]: workspace("mapping"),
    [`${GET}?step=glossary`]: workspace("mapping", "ready", { viewed_step: "glossary", items: [] }),
  }));
  show();
  await screen.findByTestId("item-00000000-0000-4000-8000-0000000000e1");
  fireEvent.click(screen.getByTestId("step-glossary"));
  await waitFor(() => expect(screen.queryByTestId("item-00000000-0000-4000-8000-0000000000e1")).toBeNull());
  fireEvent.click(screen.getByTestId("step-mapping"));
  expect(await screen.findByTestId("item-00000000-0000-4000-8000-0000000000e1")).toBeTruthy();
  expect((screen.getByTestId("workspace-back") as HTMLButtonElement).disabled).toBe(true);
});

test("each step's panel is chosen by the step, and no panel shows 'x of 0'", async () => {
  const cases: [StepName, OnboardingItem[], string][] = [
    ["profile", [profileItem()], "profile-outcome"], ["glossary", [glossaryItem()], "Coupon rate"],
    ["mapping", [mappingItem()], "Score"], ["transformations", [transformationItem()], "placeholder to null"],
    ["rules", [ruleItem()], "rule-sql"], ["go_live", [reviewItem()], "summary-page"],
  ];
  for (const [step, items, marker] of cases) {
    cleanup();
    stubFetch(base({ [GET]: workspace(step, "ready", { items }) }));
    const { container } = show();
    await screen.findByTestId("workspace-approve");
    await waitFor(() => expect(screen.queryByTestId("workspace-loading")).toBeNull());
    expect(container.querySelector(`[data-testid="${marker}"]`) ?? screen.getAllByText(new RegExp(marker)).length).toBeTruthy();
    expect(container.textContent ?? "").not.toMatch(/\b\d+ of 0\b/);
  }
});

test("the first-load step shows its panel", async () => {
  stubFetch(base({ [GET]: workspace("first_load", "ready", { items: [] }) }));
  show();
  expect(await screen.findByTestId("first-load-outcome")).toBeTruthy();
});
