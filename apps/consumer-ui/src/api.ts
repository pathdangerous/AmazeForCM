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

// element_names: the glossary name of every element the requester can tick (key -> name).
export type Discovery = { match: Match; hits: Hit[]; element_names: Record<string, string> };

// ---- routing and step 01

export type RoutingOutcome = "reuse" | "compose" | "create_product" | "raise_requirement";

export type RequestResult = {
  request_id: string;
  // product is set for reuse; entitled false means "Access restricted"
  routing: { outcome: RoutingOutcome; product?: { entitled: boolean } };
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

// ---- phase 3: the custom data product

export type SourceConditions = {
  licence: string;
  permitted_use: string;
  redistribution: string;
  external_approvals: string[];
};

export type SourceSummary = { code: string; name: string; provider: string; visibility: string; conditions: SourceConditions };

// 3b: the scope S1 proposes, the sources S2 lists, and the history of choices.
export type ScopeProposed = {
  proposal_id: string;
  visibility: "public" | "private";
  provider: string;
  where_to_search: string[];
  reason: string;
};

export type SourceCandidate = {
  proposal_id: string;
  rank: number;
  source: SourceSummary;
  coverage: { elements: string[]; terms: string[]; missing: string[] };
  reason: string;
};

export type ChoiceOnPage = {
  version: number;
  action: "selected" | "released";
  source_code: string;
  source_name: string;
  reason: "source_down_n_days" | "requester_asked" | "candidate_rejected" | null;
  at: string;
};

export type NextAction = "find_source" | "confirm_scope" | "ask_for_scope" | "list_sources" | "pick_source";

export type RequirementPage = {
  id: string;
  created_at: string;
  title: string;
  description: string;
  status: string;
  message: string;
  elements: string[];
  terms: string[];
  source: (SourceSummary & { feed_code: string }) | null;
  last_fetch: { outcome: "fetched" | "source_down"; at: string; detail: string | null } | null;
  next_action: NextAction | null;
  proposed_scope: ScopeProposed | null;
  scope: { visibility: string; provider: string; where_to_search: string[]; version: number } | null;
  listed: SourceCandidate[];
  choices: ChoiceOnPage[];
  can_release: boolean;
  // Phase 4: the gaps of an accepting sign-off, shown under the onboarding message.
  signoff_note: string | null;
  // Phase 7: where the data is while onboarding; the product once ready or restricted; a later held delivery.
  progress: { state: "checking_file" | "setting_up" | "approved" | "delivery_held" | "part_missing"; text: string } | null;
  product: { code: string; name: string; entitled: boolean; route: string | null } | null;
  held_note: string | null;
  element_names: Record<string, string>; // phase 7.5, P1: key -> current glossary name
};

export type Notification = { id: string; created_at: string; subject: string; body: string; link: string | null;
                             read_at: string | null };

type FetchOutcome = { outcome: "fetched" | "source_down" } | null;

export type FindSourceResponse = {
  check: { outcome: "known_source" | "no_known_source"; reason?: string };
  fetch: FetchOutcome;
  // S1's typed result with no known source: the proposed scope, or no_fit (no channel to search)
  scope: ScopeProposed | { outcome: "no_fit"; reason: string } | null;
  message: string;
  requirement: RequirementPage;
};

// Every 3b step answers with its result, the requester's message and the page after it.
export type StepResponse = {
  result: { outcome?: string; [key: string]: unknown };
  fetch?: FetchOutcome;
  message: string;
  requirement: RequirementPage;
};

// ---- 3b: the DiscoveryAssistant beside the search

export type Suggestion = { term: string; element_key: string; element_name: string; reason: string; products: Hit[] };
export type Assistance = { log_id: string | null; suggestions: Suggestion[] };

// ---- phase 7.8, task 7.4: the catalogue and a product's page (core/steps/product_page.py)

export type Freshness = { released_at: string; rows: number };
export type CatalogueProduct = {
  code: string; name: string; description: string; owner_id: string; owner_name: string; visibility: string;
  freshness: Freshness | null; access: "entitled" | "not_entitled_visible"; route: string | null;
  paused_since?: string | null; // phase 7.9: the product's feed is paused since this moment
};
export type DomainGroup = { domain: string; label: string; products: CatalogueProduct[] };
export type DictionaryRow = {
  element_key: string; name: string; definition: string; data_type: string; unit: string | null; label: string;
};
export type ProductPage = {
  outcome: "product_page"; code: string; name: string; description: string; domain: string; domain_label: string;
  owner: { id: string; name: string }; visibility: string; access: "entitled" | "not_entitled_visible";
  route: string | null; freshness: Freshness | null; columns: string[]; dictionary: DictionaryRow[];
  paused_since?: string | null; // phase 7.9
};
export type ProductPreview = { outcome: "preview"; columns: string[]; rows: unknown[][] };

export const productPath = (code: string, tail = "") => `/api/consumer/products/${encodeURIComponent(code)}${tail}`;
export const catalogue = (devUser: string) => apiGet<DomainGroup[]>("/api/consumer/catalogue", devUser);

// The typed refusals of the product endpoints: 403 {outcome: "not_entitled", route}, 404 {outcome: "no_data"}.
export type ProductRefusal = { kind: "not_entitled"; route: string } | { kind: "no_data" };

export class ProductRefused extends Error {
  refusal: ProductRefusal;
  constructor(refusal: ProductRefusal) {
    super(refusal.kind);
    this.refusal = refusal;
  }
}

async function productFetch(path: string, devUser: string): Promise<Response> {
  const response = await fetch(path, { headers: headersFor(devUser) });
  if (response.ok) return response;
  let detail: unknown;
  try {
    detail = ((await response.clone().json()) as { detail?: unknown }).detail;
  } catch {
    detail = undefined;
  }
  const typed = (detail ?? {}) as { outcome?: string; route?: string };
  if (response.status === 403 && typed.outcome === "not_entitled") {
    throw new ProductRefused({ kind: "not_entitled", route: typed.route ?? "" });
  }
  if (response.status === 404 && typed.outcome === "no_data") throw new ProductRefused({ kind: "no_data" });
  throw await failure(path, response);
}

export async function productGet<T>(path: string, devUser: string): Promise<T> {
  return (await (await productFetch(path, devUser)).json()) as T;
}

// A download needs the dev-user header, which a plain link cannot send: fetch, then save the Blob.
export async function downloadProduct(code: string, kind: "csv" | "xlsx", devUser: string): Promise<void> {
  const blob = await (await productFetch(productPath(code, `/download.${kind}`), devUser)).blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${code}.${kind}`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

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
