// Component checks (phase 7.5 plan, assumption 9): synthetic answers for a stubbed fetch. Synthetic data only.
import { MantineProvider } from "@mantine/core";
import { render } from "@testing-library/react";
import { createElement, type ReactElement } from "react";
import { vi } from "vitest";
import type {
  CannotMapColumn, Conditions, FeedPage, LoadOnPage, OnboardingItem, OnboardingRow, RuleRowsShown, StepInputs, StepName, StepState,
  TimelineEntry, User, Workspace,
} from "../api";
import { cssVariablesResolver, theme } from "../theme";
import type { ItemActions } from "../views/steps/shared";

// Renders inside the app's MantineProvider (main.tsx does the same). jsdom has no matchMedia, which Mantine reads
// for the colour scheme (a stub that never matches: light), and no ResizeObserver (Table.ScrollContainer).
export function renderWithTheme(ui: ReactElement) {
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: false, media: query, onchange: null, addEventListener: () => undefined,
    removeEventListener: () => undefined, addListener: () => undefined, removeListener: () => undefined,
    dispatchEvent: () => false,
  }));
  return render(createElement(MantineProvider, { theme, cssVariablesResolver, children: ui }));
}

export const USERS: User[] = [
  { id: "dev-backend-team", display_name: "Blake Backend", email: "blake@example.test", roles: ["backend_team"], app: "backend" },
  { id: "dev-data-steward", display_name: "Sam Steward", email: "sam@example.test", roles: ["data_steward"], app: "backend" },
];

export type Routes = Record<string, unknown>; // "GET /api/path?query" -> the JSON body; anything else is a 404

// A route answer with another status than 200 (the upload routes: 413, 422, 409).
export class Reply {
  status: number;
  body: unknown;
  constructor(status: number, body: unknown) {
    this.status = status;
    this.body = body;
  }
}

// Replaces global fetch: a request whose method and path are in `routes` gets that body with 200 (or a Reply's
// status and body), any other 404.
export function stubFetch(routes: Routes) {
  const fetchStub = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const key = `${(init?.method ?? "GET").toUpperCase()} ${url.replace(/^https?:\/\/[^/]+/, "")}`;
    if (key in routes) {
      const answer = routes[key];
      const [status, body] = answer instanceof Reply ? [answer.status, answer.body] : [200, answer];
      return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
    }
    return new Response(JSON.stringify({ detail: `not stubbed: ${key}` }), {
      status: 404, headers: { "Content-Type": "application/json" },
    });
  });
  vi.stubGlobal("fetch", fetchStub);
  return fetchStub;
}

// The answers every screen of the shell asks for.
export function shellRoutes(user: User = USERS[0]): Routes {
  return {
    "GET /api/dev/users": USERS,
    "GET /api/health": { four_eyes: true },
    "GET /api/me": user,
  };
}

// ---- one synthetic feed page (task 8 onwards): a registered feed, one requirement, one mapped and one pending column
export const FEED_ID = "00000000-0000-4000-8000-0000000000f1";
export const REQUIREMENT_ID = "00000000-0000-4000-8000-0000000000a1";
const PROFILE_ID = "00000000-0000-4000-8000-0000000000b1";
const AT = "2026-11-18T09:00:00Z";
const CONDITIONS: Conditions = { licence: "Synthetic licence", permitted_use: "Internal analysis",
                                 redistribution: "Not allowed", external_approvals: [] };

export const PEOPLE = [
  { id: "dev-backend-team", display_name: "Blake Backend" }, { id: "dev-data-owner", display_name: "Devon Owner" },
  { id: "dev-data-steward", display_name: "Sam Steward" }, { id: "dev-requester", display_name: "Riley Requester" },
];

export function feedPage(overrides: Partial<FeedPage> = {}): FeedPage {
  const base: FeedPage = {
    feed: { id: FEED_ID, created_at: AT, code: "syn_test_feed", name: "Test feed", stage: "registered",
            requirement_id: REQUIREMENT_ID, owner_id: "dev-data-owner", source_ref: "files/test_feed.csv",
            registered_at: AT, source_code: "syn_test_feed" },
    source: { code: "syn_test_feed", name: "Test feed", provider: "Synthetic provider", conditions: CONDITIONS },
    requirements: [{ id: REQUIREMENT_ID, title: "Daily volume", status: "onboarding", requester_id: "dev-requester",
                     basis: "requester_pick", raised_at: AT, accepted_conditions: CONDITIONS }],
    last_file: { outcome: "fetched", created_at: AT, blob_ref: "sources/test_feed.csv", detail: null, id: "fetch-1" },
    profile: {
      id: PROFILE_ID, created_at: AT, version: 1, outcome: "profiled", row_count: 10, engine: "duckdb",
      profiled_by: "dev-backend-team", detail: null, fetch_id: "fetch-1",
      columns: [{ name: "instr_id", type: "VARCHAR", null_count: 0, null_rate: 0, distinct_count: 10 },
                { name: "qty", type: "BIGINT", null_count: 0, null_rate: 0, distinct_count: 9 }],
      coverage: [{ requirement_id: REQUIREMENT_ID, missing: [], items: [{ item: "volume", kind: "element", declared: true,
                   column: "qty", in_file: true, null_rate: 0, distinct_count: 9 }] }],
      spec: { source_code: "syn_test_feed", columns: { instr_id: "instrument_id", qty: "volume" } },
    },
    undeclared_columns: [], missing_columns: [],
    profiles: [{ version: 1, outcome: "profiled", at: AT }],
    summary: { proposal_id: "summary-1", summary: "10 rows; 1 of 1 requested items is in the file.",
               recommended_outcome: "meets", gaps: [], reasons: [] },
    summary_status: "approved",
    signoffs: [{ id: "signoff-1", created_at: AT, version: 1, profile_id: PROFILE_ID, outcome: "meets", gaps: [],
                 reasons: [], signed_off_by: "dev-backend-team" }],
    registration: { owner_id: "dev-data-owner", licence: "Synthetic licence", permitted_use: "Internal analysis",
                    redistribution: "Not allowed", visibility: "open", registered_by: "dev-backend-team", created_at: AT },
    next_action: null,
    glossary_run: { id: "run-1", created_at: AT, feed_id: FEED_ID, reason: "registered", columns: [],
                    requested_by: "dev-backend-team", status: "done", attempt: 1, started_at: AT, finished_at: AT,
                    results: {}, error: null },
    columns: [
      { name: "instr_id", type: "VARCHAR", source_says: "instrument_id", mapped_to: "instrument_id", mapped_version: 1,
        pending: null, why_none: null, versions: [{ id: "mapping-1", created_at: AT, feed_id: FEED_ID,
        source_field: "instr_id", element_key: "instrument_id", version: 1, proposal_id: "proposal-1" }] },
      { name: "qty", type: "BIGINT", source_says: "volume", mapped_to: null, mapped_version: null,
        pending: { proposal_id: "proposal-2", candidates: ["volume"], recommended: "volume", reason: "Same name." },
        why_none: null, versions: [] },
    ],
    rules: { current: [], pending: [], rejected_keys: [] },
    go_live: { latest: null, hypercare_active: false, pending_summary: null },
    loads: [],
    product: null,
    parked_columns: [],
    coverage_titles: { [REQUIREMENT_ID]: "Daily volume" },
  };
  return { ...base, ...overrides };
}

export function scheduledLoad(overrides: Partial<LoadOnPage> = {}): LoadOnPage {
  return {
    id: "load-1", purpose: "scheduled", status: "released", received_at: AT, finished_at: AT, failed_stage: null,
    failure: null, go_live_id: "go-live-1", arrival_outcome: "on_time_complete", arrival_detail: {},
    stages: (["raw", "curated", "validated", "standard"] as const).map((stage) => ({
      stage, outcome: "stage_completed" as const, rows_in: 10, rows_out: 10, detail: {} })),
    certification: { rows: 10, flagged_rows: 0, trust_score: "100.0", threshold: "90.0", outcome: "released", warned: [] },
    ...overrides,
  };
}

// ---- the upload routes (phase 7.8, task 2.5)
export const UPLOAD_ID = "00000000-0000-4000-8000-0000000000c1";
export const UPLOAD_PATH = "/api/amaze/uploads?filename=Security%20Master%202026.xlsx";

export function inspected(overrides: Record<string, unknown> = {}) {
  return {
    outcome: "inspected", upload_id: UPLOAD_ID, filename: "Security Master 2026.xlsx", format: "xlsx",
    sheets: ["Master", "Notes"], sheet: "Master", header_row: 1, encoding: null, delimiter: null,
    columns: ["instr_id", "qty"], row_count: 3,
    preview: [["A1", "10"], ["A2", "20"], ["A3", null]], skipped: { blank: 2 },
    ...overrides,
  };
}

// ---- phase 7.8, task 7.2: the onboarding workspace
export const SESSION_ID = "00000000-0000-4000-8000-0000000000d1";
export const WS_PATH = `/api/amaze/onboardings/${SESSION_ID}`;
const STEP_ORDER: StepName[] = ["upload", "profile", "mapping", "glossary", "transformations", "rules", "go_live",
                                "first_load", "product_ready"];

// phase 7.22: a column no glossary element fits
export const cannotMapColumn = (overrides: Partial<CannotMapColumn> = {}): CannotMapColumn => ({
  column: "Coupon", reason: "no_candidate", state: "undecided", glossary_status: null, ...overrides });

export function onboardingItem(overrides: Partial<OnboardingItem> = {}): OnboardingItem {
  return {
    proposal_id: "00000000-0000-4000-8000-0000000000e1", kind: "mapping", column: "Ticker",
    summary: "Ticker -> instrument_ticker", status: "pending", confidence: 0.93, pre_accepted: true,
    needs_decision: false, waiting_for: null, edited: false, edited_by: null, edited_from: null,
    reason: "The name matches the glossary element exactly.",
    payload: { source_field: "Ticker", column_type: "VARCHAR", candidates: ["instrument_ticker"],
               recommended: "instrument_ticker", reason: "Name matches." },
    decided_by: null, decided_at: null, ...overrides,
  };
}

export function stepInputs(overrides: Partial<StepInputs> = {}): StepInputs {
  return {
    row_count: 120, column_count: 3, columns: ["Ticker", "Name", "Currency"], issues: [], key_columns: [],
    duplicate_key_count: null, candidate_counts: {}, facts: [], ...overrides,
  };
}

const item = (proposal: string, kind: string, column: string | null, summary: string, payload: Record<string, unknown>,
              overrides: Partial<OnboardingItem> = {}): OnboardingItem =>
  onboardingItem({ proposal_id: `00000000-0000-4000-8000-${proposal}`, kind, column, summary, payload, ...overrides });

export const profileItem = (overrides: Partial<OnboardingItem> = {}) => item(
  "0000000000p1", "profile_summary", null, "120 rows; one issue", {
    summary: "120 rows and 3 columns; Currency holds a placeholder.", recommended_outcome: "meets_with_gaps",
    gaps: ["Currency has placeholders"], reasons: [], key_columns: ["Ticker"], key_reason: "Ticker is unique per row.",
  }, { reason: "120 rows and 3 columns. Ticker is unique per row.", confidence: 0.9, ...overrides });

export const glossaryItem = (overrides: Partial<OnboardingItem> = {}) => item(
  "0000000000g1", "glossary_element", "Coupon", "Coupon: new element coupon_rate", {
    source_field: "Coupon", outcome: "proposed", recommended: 0, clashes: [], reason: "No element covers Coupon.",
    run_id: "run-1", options: [{ kind: "new_element", key: "coupon_rate", alias: null, element: {
      key: "coupon_rate", name: "Coupon rate", domain: "reference", data_type: "decimal",
      definition: "The annual coupon as a percentage.", aliases: [], unit: "percent" } }],
  }, { reason: "No glossary element covers Coupon, so coupon_rate is proposed.", confidence: 0.8, ...overrides });

export const aliasItem = (overrides: Partial<OnboardingItem> = {}) => item(
  "0000000000g2", "glossary_element", "Ccy", "Ccy: alias ccy of currency_code", {
    source_field: "Ccy", outcome: "duplicate_alias", recommended: 0, clashes: [], reason: "Same meaning.", run_id: "run-1",
    options: [{ kind: "alias", key: "currency_code", alias: "ccy", element: null }],
  }, overrides);

export const conflictItem = (overrides: Partial<OnboardingItem> = {}) => item(
  "0000000000g3", "glossary_element", "Rate", "Rate: clashes with coupon_rate, yield_rate", {
    source_field: "Rate", outcome: "conflict_to_owner", recommended: null, clashes: ["coupon_rate", "yield_rate"],
    reason: "Two elements fit.", run_id: "run-1",
    options: [{ kind: "alias", key: "coupon_rate", alias: "rate", element: null },
              { kind: "alias", key: "yield_rate", alias: "rate", element: null }],
  }, { needs_decision: true, pre_accepted: false, confidence: 0.5, ...overrides });

export const mappingItem = (overrides: Partial<OnboardingItem> = {}) => item(
  "0000000000m1", "mapping", "Ticker", "Ticker -> instrument_ticker", {
    source_field: "Ticker", column_type: "VARCHAR", candidates: ["instrument_ticker"], recommended: "instrument_ticker",
    reason: "Name matches.",
  }, overrides);

export const tieItem = (overrides: Partial<OnboardingItem> = {}) => item(
  "0000000000m2", "mapping", "Price", "Price: tie between bid_price, ask_price", {
    source_field: "Price", column_type: "DOUBLE", candidates: ["bid_price", "ask_price"], recommended: null,
    reason: "Two candidates score the same.",
  }, { needs_decision: true, pre_accepted: false, confidence: 0.6, ...overrides });

export const transformationItem = (overrides: Partial<OnboardingItem> = {}) => item(
  "0000000000t1", "transformation", "Currency", "Currency: trim, placeholder_to_null", {
    source_field: "Currency", element_key: "currency_code", reason: "Currency holds N/A.", confidence: 0.9,
    steps: [{ op: "trim" }, { op: "placeholder_to_null", values: ["N/A"] }],
  }, overrides);

export const ruleItem = (overrides: Partial<OnboardingItem> = {}) => item(
  "0000000000r1", "rule", "price_positive", "price_positive: Price is above zero (flags 2 of 120 rows of this file)", {
    rule_key: "price_positive", sql: "SELECT _row FROM data WHERE price <= 0", description: "Price is above zero",
    element_keys: ["bid_price"], template: null,
    backtest: { status: "ran", total_rows: 120, flagged_rows: 2, share: 0.02, deliveries: [
      { label: "current file", rows: 120, flagged: 2, excluded: null }] },
  }, overrides);

export const reviewItem = (overrides: Partial<OnboardingItem> = {}) => item(
  "0000000000s1", "review_summary", null, "All 6 columns are mapped; the test load passed.", {
    summary: "All 6 columns are mapped; the test load passed.", highlights: ["Six columns mapped"],
    risks: ["Currency has placeholders"], unmapped_columns: [], rejected_glossary: [],
  }, overrides);

// current: the step the session is on; states: the state of each step before it is done, the current one as given.
export function workspace(current: StepName = "mapping", state: StepState = "ready",
                          overrides: Partial<Workspace> = {}): Workspace {
  const at = STEP_ORDER.indexOf(current);
  const states = Object.fromEntries(STEP_ORDER.map((s, i) => [s, i < at ? "done" : i === at ? state : "waiting"]));
  return {
    session: { id: SESSION_ID, feed_id: FEED_ID, current_step: current, step_states: states as Record<string, StepState>,
               status: "active", updated_at: "2026-11-19T10:00:00Z", model_calls: 3 },
    feed_code: "security_master", current_step: current, viewed_step: current, visibility: null, inputs: stepInputs(),
    steps: STEP_ORDER.map((name) => ({ name, state: states[name] as StepState, detail: null })),
    items: [onboardingItem()], job: null, progress: null,
    agent_notes: [{
      proposal_id: "00000000-0000-4000-8000-0000000000e1", agent: "MappingAgent", provider: "mock", model: "mock-1",
      model_version: "1", prompt_id: "mapping", prompt_version: "v2", latency_ms: 12, input_ref: "input:6 columns, 2 issues",
      reasoning: "Ticker is a close match for the glossary element.",
    }],
    key_policy: null, model_calls: 3, model_call_cap: 40, can_approve: true, blocked_by: null, cannot_map: [], ...overrides,
  };
}

export function onboardingRow(overrides: Partial<OnboardingRow> = {}): OnboardingRow {
  const ws = workspace("mapping");
  return {
    id: SESSION_ID, feed_id: FEED_ID, feed_code: "security_master", feed_name: "Security master",
    current_step: "mapping", step_states: ws.session.step_states, status: "active", created_by: "dev-backend-team",
    created_at: "2026-11-19T09:00:00Z", updated_at: "2026-11-19T10:00:00Z", model_calls: 3, model_call_cap: 40, ...overrides,
  };
}

export const TIMELINE: TimelineEntry[] = [
  { at: "2026-11-19T09:05:00Z", kind: "event", actor: "system", step: "mapping", title: "Agent finished", proposal_id: null, detail: {} },
  { at: "2026-11-19T09:10:00Z", kind: "edit", actor: "dev-backend-team", step: "mapping", title: "Edited Ticker mapping",
    proposal_id: "00000000-0000-4000-8000-0000000000e1", detail: { edited_by: "dev-backend-team" } },
];

// ---- phase 7.8, task 7.3b: what a step panel gets from the workspace view (mocks for the callbacks)
export function itemActions(overrides: Partial<ItemActions> = {}): ItemActions {
  return {
    readOnly: false, busy: false, marked: [], editing: null, editError: null,
    decide: vi.fn(), startEdit: vi.fn(), saveEdit: vi.fn(), cancelEdit: vi.fn(), ...overrides,
  };
}

// ---- phase 7.20: the rows of a rule. Row 2 fails, row 3 is waived, the rest pass; bid_price is the rule's column.
export const RULE_ID = "00000000-0000-4000-8000-0000000000r1";
export const ruleRowsPath = (query = "failing_only=false&page=1") => `GET /api/amaze/rules/${RULE_ID}/rows?${query}`;
export function ruleRowsShown(overrides: Partial<RuleRowsShown> = {}): RuleRowsShown {
  const values = (price: number) => ({ bid_price: price, instrument_id: `syn-${price}` });
  return {
    outcome: "rows_shown", rule: { rule_key: "price_positive", description: "Price is above zero",
                                   sql: "SELECT _row FROM curated WHERE bid_price <= 0", element_keys: ["bid_price"] },
    source: "draft", load_id: null,
    columns: [{ key: "bid_price", affected: true }, { key: "instrument_id", affected: false }],
    rows: [{ row: 1, values: values(10), passes: true, waived: false }, { row: 2, values: values(-5), passes: false, waived: false },
           { row: 3, values: values(-1), passes: false, waived: true }],
    counts: { rows: 6, failing: 1, waived: 1, shown: 6 }, page: 1, size: 3, ...overrides,
  };
}

// What FeedView reads: the user, the page, the owners and (from task 9) the people.
export function feedRoutes(page: FeedPage, user: User = USERS[1]): Routes {
  return {
    "GET /api/me": user,
    [`GET /api/amaze/feeds/${page.feed.id}`]: page,
    "GET /api/amaze/owners": [{ id: "dev-data-owner", display_name: "Devon Owner" }],
    "GET /api/amaze/people": PEOPLE,
  };
}
