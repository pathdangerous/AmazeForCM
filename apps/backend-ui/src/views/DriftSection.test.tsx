import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { FEED_ID, Reply, renderWithTheme, stubFetch } from "../test/fixtures";
import DriftSection from "./DriftSection";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const PATH = `GET /api/amaze/feeds/${FEED_ID}/drift`;
const action = (over = {}) => ({ proposal_id: "p1", action: "map_new_column", columns: ["BEST_FCF"], params: {}, applied_by: "agent",
  reason: "BEST_FCF is new in the delivery.", origin: "rule", status: "pending", ...over });
const drift = (actions = [action()]) => ({
  load_id: "l1", actions,
  report: { new_columns: ["BEST_FCF"], missing_columns: [], order_changed: true, order_before: [], order_after: [],
            type_changes: [], rule_rises: [{ rule_key: "px_present", was: 0.01, now: 0.1 }] } });

test("nothing is shown for a feed whose deliveries never drifted", async () => {
  const fetchStub = stubFetch({ [PATH]: { load_id: null, report: drift().report, actions: [] } });
  renderWithTheme(<DriftSection feedId={FEED_ID} devUser="dev-operator" canAct />);
  await waitFor(() => expect(fetchStub).toHaveBeenCalled());
  expect(screen.queryByTestId("drift")).toBeNull();
});

test("the report says what changed, the order as information only, and each action says who does it", async () => {
  stubFetch({ [PATH]: drift([action(), action({ proposal_id: "p2", action: "add_rule", applied_by: "manual", origin: "manual" })]) });
  renderWithTheme(<DriftSection feedId={FEED_ID} devUser="dev-operator" canAct />);
  const report = await screen.findByTestId("drift-report");
  expect(report.textContent).toContain("New columns: BEST_FCF");
  expect(report.textContent).toContain("information only");
  expect(report.textContent).toContain("px_present (1.0% to 10.0%)");
  expect(screen.getByTestId("drift-action-p1").textContent).toContain("Agent applies, proposed by rules");
  expect(screen.getByTestId("drift-action-p2").textContent).toContain("I'll do it, added by hand");
});

test("Select approves the action; Reject needs a reason", async () => {
  const fetchStub = stubFetch({ [PATH]: drift(), "POST /api/amaze/proposals/p1/decision": { proposal: {} } });
  renderWithTheme(<DriftSection feedId={FEED_ID} devUser="dev-operator" canAct />);
  await screen.findByTestId("drift-action-p1");
  fireEvent.click(screen.getByRole("button", { name: "Select" }));
  await waitFor(() => expect(fetchStub.mock.calls.some(([u, i]) => String(u).endsWith("/p1/decision") && String(i?.body).includes("approved"))).toBe(true));
  fireEvent.click(screen.getByRole("button", { name: "Reject" }));
  const confirm = screen.getByRole("button", { name: "Confirm" }) as HTMLButtonElement;
  expect(confirm.disabled).toBe(true);
  fireEvent.change(screen.getByLabelText("Why it is rejected"), { target: { value: "Not needed." } });
  expect(confirm.disabled).toBe(false);
});

test("a person adds an action; the API's refusal is shown", async () => {
  stubFetch({ [PATH]: drift(), [`POST /api/amaze/feeds/${FEED_ID}/drift/actions`]: new Reply(422, { detail: "The file has no column 'x'" }) });
  renderWithTheme(<DriftSection feedId={FEED_ID} devUser="dev-operator" canAct />);
  await screen.findByTestId("drift-action-p1");
  fireEvent.click(screen.getByRole("button", { name: "Add action" }));
  fireEvent.change(screen.getByLabelText("Column"), { target: { value: "x" } });
  fireEvent.change(screen.getByLabelText("Why"), { target: { value: "Check it." } });
  fireEvent.click(screen.getByRole("button", { name: "Add item" }));
  expect((await screen.findByRole("alert")).textContent).toContain("no column");
});

test("without the right to act there are no buttons", async () => {
  stubFetch({ [PATH]: drift() });
  renderWithTheme(<DriftSection feedId={FEED_ID} devUser="dev-requester" canAct={false} />);
  await screen.findByTestId("drift-action-p1");
  expect(screen.queryByRole("button")).toBeNull();
});
