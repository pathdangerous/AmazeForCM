// Component checks (phase 7.5 plan, assumption 9): synthetic answers for a stubbed fetch. Synthetic data only.
import { vi } from "vitest";
import type { User } from "../api";

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
