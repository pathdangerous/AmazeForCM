// Component checks (phase 7.5 plan, assumption 9): synthetic answers for a stubbed fetch. Synthetic data only.
import { MantineProvider } from "@mantine/core";
import { render } from "@testing-library/react";
import { createElement, type ReactElement } from "react";
import { vi } from "vitest";
import type { DomainGroup, ProductPage, ProductPreview, RequirementPage, User } from "../api";
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
  { id: "dev-requester", display_name: "Riley Requester", email: "riley@example.test", roles: ["requester"], app: "consumer" },
  { id: "dev-analyst", display_name: "Ari Analyst", email: "ari@example.test", roles: ["requester"], app: "consumer" },
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
    "GET /api/dev/users": USERS,
    "GET /api/health": { status: "ok", database: "ok", adapters: {}, unavailable: [], four_eyes: true },
    "GET /api/me": user,
  };
}

// ---- one synthetic requirement page (task 15): onboarding from a picked source
export const REQUIREMENT_ID = "00000000-0000-4000-8000-0000000000a1";
const AT = "2026-11-18T09:00:00Z";

export function requirementPage(overrides: Partial<RequirementPage> = {}): RequirementPage {
  return {
    id: REQUIREMENT_ID, created_at: AT, title: "coupon rate", description: "", status: "onboarding",
    message: "Your data is getting onboarded; we will notify you once it is ready.", elements: ["coupon_rate"],
    terms: [], source: null, last_fetch: null, next_action: null, proposed_scope: null, scope: null, listed: [],
    choices: [], can_release: false, signoff_note: null,
    progress: { state: "setting_up", text: "Bond terms is registered; the backend team is setting up how its data is read and checked." },
    product: null, held_note: null, element_names: { coupon_rate: "Coupon rate" },
    ...overrides,
  };
}

// ---- phase 7.8, task 7.4: the catalogue and a product page (synthetic)
export const FRESHNESS = { released_at: "2026-11-18T12:44:00", rows: 150 };

export const CATALOGUE: DomainGroup[] = [
  { domain: "reference", label: "Reference data", products: [
    { code: "demo_bond_terms", name: "Bond terms", description: "Terms of bonds.", owner_id: "dev-owner",
      owner_name: "Olly Owner", visibility: "internal", freshness: FRESHNESS, access: "entitled", route: null },
  ] },
  { domain: "market", label: "Market data", products: [
    { code: "demo_prices", name: "Daily prices", description: "End of day prices.", owner_id: "dev-owner",
      owner_name: "Olly Owner", visibility: "restricted", freshness: null, access: "not_entitled_visible",
      route: "Ask Olly Owner for access to Daily prices" },
  ] },
];

export function productPage(overrides: Partial<ProductPage> = {}): ProductPage {
  return {
    outcome: "product_page", code: "demo_bond_terms", name: "Bond terms", description: "Terms of bonds.",
    domain: "reference", domain_label: "Reference data", owner: { id: "dev-owner", name: "Olly Owner" },
    visibility: "internal", access: "entitled", route: null, freshness: FRESHNESS,
    columns: ["coupon_rate", "maturity"],
    dictionary: [
      { element_key: "coupon_rate", name: "Coupon rate", definition: "Yearly interest rate.", data_type: "decimal",
        unit: "percent", label: "Coupon" },
      { element_key: "maturity", name: "Maturity date", definition: "Date the bond ends.", data_type: "date",
        unit: null, label: "Maturity" },
    ],
    ...overrides,
  };
}

export function previewRows(n: number): ProductPreview {
  return { outcome: "preview", columns: ["Coupon", "Maturity"],
           rows: Array.from({ length: n }, (_, i) => [`${i}.5`, "2030-01-01"]) };
}
