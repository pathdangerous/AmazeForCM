import { cleanup, fireEvent, screen, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import type { ConfigProposal } from "../api";
import { FEED_ID, PEOPLE, renderWithTheme, stubFetch } from "../test/fixtures";
import GlossaryView from "./GlossaryView";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const NAMES = Object.fromEntries(PEOPLE.map((p) => [p.id, p.display_name]));
const AT = "2026-11-18T09:00:00Z";

function proposal(over: Partial<ConfigProposal> & { payload: ConfigProposal["payload"] }): ConfigProposal {
  return { id: "p1", kind: "glossary_element", status: "pending", created_at: AT, decided_at: null, feed_id: FEED_ID,
           feed_code: "syn_test_feed", feed_name: "Test feed", column: "venue_mic_code", reason: "It reads like it.",
           required_approver_roles: ["glossary_owner"], decisions: [], ...over };
}

const ALIAS = proposal({ payload: { outcome: "duplicate_alias", recommended: 0, clashes: ["exchange_mic"],
  options: [{ kind: "alias", key: "exchange_mic", alias: "venue_mic_code", element: null }] } });
const DECIDED = proposal({ id: "p2", status: "rejected", column: "maturity", decided_at: AT,
  payload: { outcome: "conflict_to_owner", recommended: null, clashes: ["maturity_date"], options: [] },
  decisions: [{ id: "d1", created_at: AT, step: 1, role: "glossary_owner", decided_by: "dev-glossary-owner",
                decision: "rejected", note: "A tenor, not a date" }] });

function show() {
  stubFetch({
    "GET /api/amaze/glossary/domains": [{ domain: "security_master", label: "Security master", count: 1 }],
    "GET /api/amaze/proposals?kind=glossary_element&status=pending": [ALIAS],
    "GET /api/amaze/proposals?kind=glossary_element&status=approved": [],
    "GET /api/amaze/proposals?kind=glossary_element&status=rejected": [DECIDED],
    "GET /api/amaze/glossary/elements?": [],
  });
  renderWithTheme(<GlossaryView devUser="dev-glossary-owner" canDecide={true} changed={0} people={
    { ...NAMES, "dev-glossary-owner": "Glenn Glossary" }} onChange={() => undefined} />);
}

test("task 12: the glossary is converted; P14: an alias says Same as, not Clashes with", async () => {
  show();
  const card = await screen.findByTestId("glossary-proposal");
  expect(card.textContent).toContain("Same as: exchange_mic");
  expect(card.textContent).not.toContain("Clashes with");
  expect(screen.getByRole("heading", { name: "Glossary" }).className).toContain("mantine-Title");
  expect(within(card).getByTestId("approve").tagName).toBe("BUTTON");
});

test("7.22: opened from an element link (#/glossary/<key>), the element is selected and its detail is shown", async () => {
  const element = { id: "e1", created_at: AT, key: "coupon_rate", version: 1, name: "Coupon rate", domain: "security_master",
                    data_type: "decimal", definition: "The annual coupon.", aliases: ["coupon"], unit: "percent",
                    proposal_id: null, created_by: "seed" };
  const fetchStub = stubFetch({
    "GET /api/amaze/glossary/domains": [{ domain: "security_master", label: "Security master", count: 1 }],
    "GET /api/amaze/proposals?kind=glossary_element&status=pending": [],
    "GET /api/amaze/glossary/elements?": [element],
    "GET /api/amaze/glossary/elements/coupon_rate": { element, history: [element] },
  });
  renderWithTheme(<GlossaryView devUser="dev-glossary-owner" canDecide={false} changed={0} people={NAMES}
                                onChange={() => undefined} initialKey="coupon_rate" />);
  await screen.findByText("The annual coupon.");
  expect(fetchStub.mock.calls.some(([u]) => String(u).endsWith("/glossary/elements/coupon_rate"))).toBe(true);
  expect(screen.getAllByText(/Coupon rate/).length).toBeGreaterThan(0);
});

test("a decided proposal of a deleted feed says so and does not link to the feed", async () => {
  stubFetch({
    "GET /api/amaze/glossary/domains": [],
    "GET /api/amaze/proposals?kind=glossary_element&status=pending": [],
    "GET /api/amaze/proposals?kind=glossary_element&status=approved": [{ ...DECIDED, status: "approved", feed_deleted: true }],
    "GET /api/amaze/proposals?kind=glossary_element&status=rejected": [],
    "GET /api/amaze/glossary/elements?": [],
  });
  renderWithTheme(<GlossaryView devUser="dev-glossary-owner" canDecide={true} changed={0} people={NAMES}
                                onChange={() => undefined} />);
  fireEvent.click(await screen.findByRole("button", { name: "Decided" }));
  const row = await screen.findByTestId("glossary-decided-row");
  expect(row.textContent).toContain("feed deleted");
  expect(within(row).queryByRole("link")).toBeNull();
});

test("P16: Decided lists each decided proposal with the decision, the person and the note", async () => {
  show();
  fireEvent.click(await screen.findByRole("button", { name: "Decided" }));
  const row = await screen.findByTestId("glossary-decided-row");
  expect(row.textContent).toContain("maturity");
  expect(row.textContent).toContain("Rejected by Glenn Glossary: A tenor, not a date");
  expect(screen.queryByTestId("glossary-proposal")).toBeNull(); // never the pending testid (plan, 10.3)
});
