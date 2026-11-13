// Fetch helpers and types for the Amaze API. Phase 2 adds apiPost and the consumer types, so this file
// is no longer identical to backend-ui's copy (phase 2 plan, assumption 41).

export type Health = {
  status: "ok" | "error";
  database: "ok" | "unreachable" | "not_configured";
  adapters: Record<string, string>;
  unavailable: string[]; // selected adapters that are still Azure stubs
};

export type User = {
  id: string;
  display_name: string;
  email: string;
  roles: string[];
  app: "backend" | "consumer";
};

// ---- step 17

export type Match = {
  elements: { key: string; ticked: boolean }[];
  ambiguous: { phrase: string; candidates: string[] }[];
  domains: string[];
  terms: string[];
};

export type ProductSummary = { code: string; name: string; description: string; domain: string; element_keys: string[] };

export type Hit =
  | { outcome: "found"; product: ProductSummary }
  | { outcome: "not_entitled_visible"; product: ProductSummary; route: string };

export type Discovery = { match: Match; hits: Hit[] };

// ---- routing and step 01

export type RoutingOutcome = "reuse" | "compose" | "create_product" | "raise_requirement";

export type RequestResult = {
  request_id: string;
  routing: { outcome: RoutingOutcome };
  requirement: { outcome: "defined" | "element_not_in_glossary"; requirement: { id: string; status: string } } | null;
  message: string;
  notes: string[];
  notified: string[];
};

export type RequestSummary = {
  id: string;
  created_at: string;
  query: string;
  outcome: RoutingOutcome;
  product_codes: string[];
  message: string;
  requirement_id: string | null;
  requirement_status: string | null;
};

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

function headersFor(devUser?: string): Record<string, string> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (devUser) headers["X-Dev-User"] = devUser;
  return headers;
}

// The API's own reason: FastAPI's detail is a sentence, or a list of validation errors.
async function failure(path: string, response: Response): Promise<ApiError> {
  let message = `${path} returned HTTP ${response.status}`;
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string") message = body.detail;
    else if (Array.isArray(body.detail)) {
      message = body.detail.map((d: { loc?: unknown[]; msg?: string }) =>
        `${(d.loc ?? []).slice(1).join(".") || "input"}: ${d.msg ?? "invalid"}`).join("; ");
    }
  } catch {
    // no JSON body: keep the status line
  }
  return new ApiError(response.status, message);
}

// GET a JSON endpoint. acceptStatuses lists non-2xx statuses whose body is still wanted (health's 503).
export async function apiGet<T>(path: string, devUser?: string, acceptStatuses: number[] = []): Promise<T> {
  const response = await fetch(path, { headers: headersFor(devUser) });
  if (!response.ok && !acceptStatuses.includes(response.status)) throw await failure(path, response);
  return (await response.json()) as T;
}

export async function apiPost<T>(path: string, body: unknown, devUser?: string): Promise<T> {
  const response = await fetch(path, {
    method: "POST",
    headers: { ...headersFor(devUser), "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw await failure(path, response);
  return (await response.json()) as T;
}

const UNREACHABLE = "API unreachable. Is it running on port 8000?";

// A sentence for the person, never a bare status code.
export function describeError(err: unknown): string {
  if (err instanceof ApiError) {
    // The Vite dev proxy answers 502 (or 504) when the API on port 8000 is not running.
    if (err.status === 502 || err.status === 504) return UNREACHABLE;
    if (err.status === 401) return "Not signed in: choose a dev user above.";
    if (err.status === 403) return "Not allowed: the marketplace is for requesters.";
    if (err.status === 422) return `Not accepted: ${err.message}`;
    if (err.status === 503) return `Service unavailable: ${err.message}`;
    return err.message;
  }
  return UNREACHABLE;
}
