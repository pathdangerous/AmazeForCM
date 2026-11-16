// Small fetch helper for the Amaze API, plus the shapes backend-ui reads.
// consumer-ui has its own copy of the helper (plan, phase 0 assumption 14).

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

export type DomainCount = { domain: string; label: string; count: number };

export type GlossaryElement = {
  id: string;
  created_at: string;
  key: string;
  version: number;
  name: string;
  domain: string;
  data_type: string;
  definition: string;
  aliases: string[];
  created_by: string;
  proposal_id: string | null;
};

export type ElementDetail = { element: GlossaryElement; history: GlossaryElement[] };

export type Feed = {
  id: string;
  created_at: string;
  code: string;
  name: string;
  stage: "candidate" | "registered" | "rejected";
  requirement_id: string | null;
  owner_id: string | null;
  source_ref: string | null;
  registered_at: string | null;
};

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

// GET a JSON endpoint. acceptStatuses lists non-2xx statuses whose body is still wanted (health's 503).
export async function apiGet<T>(path: string, devUser?: string, acceptStatuses: number[] = []): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (devUser) headers["X-Dev-User"] = devUser;
  const response = await fetch(path, { headers });
  if (!response.ok && !acceptStatuses.includes(response.status)) {
    throw new ApiError(response.status, `${path} returned HTTP ${response.status}`);
  }
  return (await response.json()) as T;
}

// ---- phase 4: one feed's page and the steps 02 to 04

export type FileColumn = { name: string; type: string; null_count: number; null_rate: number | null; distinct_count: number };
export type ItemCoverage = {
  item: string; kind: "element" | "term"; declared: boolean; column: string | null; in_file: boolean | null;
  null_rate: number | null; distinct_count: number | null;
};
export type RequirementCoverage = { requirement_id: string; items: ItemCoverage[]; missing: string[] };
export type Conditions = { licence: string; permitted_use: string; redistribution: string; external_approvals: string[] };
export type FeedProfile = {
  id: string; created_at: string; version: number; outcome: "profiled" | "spec_only_low_confidence";
  row_count: number | null; columns: FileColumn[]; coverage: RequirementCoverage[];
  spec: { source_code: string; columns: Record<string, string> }; detail: string | null; engine: string; profiled_by: string;
};
export type FeedSignoff = {
  id: string; created_at: string; version: number; profile_id: string; outcome: SignoffOutcome;
  gaps: string[]; reasons: string[]; signed_off_by: string;
};
export type SignoffOutcome = "meets" | "meets_with_gaps" | "rejected";
export type FeedPage = {
  feed: Feed & { source_code: string | null };
  source: { code: string; name: string; provider: string; conditions: Conditions } | null;
  requirements: { id: string; title: string; status: string; requester_id: string; basis: string | null;
                  accepted_conditions: Conditions | null }[];
  last_file: { outcome: string; created_at: string; blob_ref: string | null; detail: string | null } | null;
  profile: FeedProfile | null;
  undeclared_columns: string[];
  missing_columns: string[];
  profiles: { version: number; outcome: string; at: string }[];
  summary: { proposal_id: string; summary: string; recommended_outcome: SignoffOutcome; gaps: string[];
             reasons: string[] } | null;
  summary_status: string | null;
  signoffs: FeedSignoff[];
  registration: { owner_id: string; licence: string; permitted_use: string; redistribution: string;
                  visibility: "open" | "restricted"; registered_by: string; created_at: string } | null;
  next_action: "profile" | "summarise" | "sign_off" | "register" | null;
};
export type Owner = { id: string; display_name: string };

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

export async function apiPost<T>(path: string, body: unknown, devUser: string): Promise<T> {
  const response = await fetch(path, {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json", "X-Dev-User": devUser },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw await failure(path, response);
  return (await response.json()) as T;
}

// A sentence for the person, never a bare status code.
export function describeError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 401) return "Not signed in: choose a dev user above.";
    if (err.status === 403) return "Not allowed: this view needs a backend role.";
    if (err.status === 503) return "Service unavailable: check the Status view.";
    if (err.status === 409) return `Not now: ${err.message}`;
    if (err.status === 422) return `Not accepted: ${err.message}`;
    return err.message;
  }
  return "The API cannot be reached. Is it running on port 8000?";
}
