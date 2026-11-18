// Component checks (phase 7.5 plan, assumption 9): synthetic answers for a stubbed fetch. Synthetic data only.
import { MantineProvider } from "@mantine/core";
import { render } from "@testing-library/react";
import { createElement, type ReactElement } from "react";
import { vi } from "vitest";
import type { User } from "../api";
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
    "GET /api/dev/users?app=consumer": USERS,
    "GET /api/me": user,
  };
}
