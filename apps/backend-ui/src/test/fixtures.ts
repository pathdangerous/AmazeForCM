// Component checks (phase 7.5 plan, assumption 9): synthetic answers for a stubbed fetch. Synthetic data only.
import { MantineProvider } from "@mantine/core";
import { render } from "@testing-library/react";
import { createElement, type ReactElement } from "react";
import { vi } from "vitest";
import type { Conditions, FeedPage, LoadOnPage, User } from "../api";
import { cssVariablesResolver, theme } from "../theme";

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

// Replaces global fetch: a request whose method and path are in `routes` gets that body with 200, any other 404.
export function stubFetch(routes: Routes) {
  const fetchStub = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const key = `${(init?.method ?? "GET").toUpperCase()} ${url.replace(/^https?:\/\/[^/]+/, "")}`;
    if (key in routes) {
      return new Response(JSON.stringify(routes[key]), { status: 200, headers: { "Content-Type": "application/json" } });
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
    "GET /api/dev/users?app=backend": USERS,
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

// What FeedView reads: the user, the page, the owners and (from task 9) the people.
export function feedRoutes(page: FeedPage, user: User = USERS[1]): Routes {
  return {
    "GET /api/me": user,
    [`GET /api/amaze/feeds/${page.feed.id}`]: page,
    "GET /api/amaze/owners": [{ id: "dev-data-owner", display_name: "Devon Owner" }],
    "GET /api/amaze/people": PEOPLE,
  };
}
