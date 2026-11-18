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
  fetch_id: string; // the file it profiled (phase 7.5, P6)
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
                  raised_at: string; accepted_conditions: Conditions | null }[]; // raised_at: phase 7.5, P7
  last_file: { id: string; outcome: string; created_at: string; blob_ref: string | null; detail: string | null } | null;
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
  glossary_run: GlossaryRun | null; // phase 5: the latest
  columns: ColumnMapping[]; // phase 5: a registered feed's mapping table
  rules: RulesOnPage; // phase 6
  go_live: GoLiveOnPage;
  loads: LoadOnPage[]; // the latest 10, newest first
  product: { code: string; name: string; visibility: "open" | "restricted"; owner_id: string; created_at: string } | null;
  parked_columns: string[]; // phase 7: new columns step 12 found, not mapped yet
  coverage_titles: Record<string, string>; // phase 7.5, P11: requirement id -> title, for every coverage card
};

// Phase 7.5: a registry row (P4) and one person's display name (GET /api/amaze/people, P3 and P8).
export type FeedRow = Feed & { source_name: string | null };
export type Person = { id: string; display_name: string };

// ---- phase 7: steps 12, 15 and 20, and the inbox
export type ArrivalOutcome = "on_time_complete" | "resent_quarantined" | "additive_parked" | "breaking_held";
export type Certification = {
  rows: number; flagged_rows: number; trust_score: string; threshold: string;
  outcome: "released" | "held_consumers_warned"; warned: string[];
};
export type Notification = { id: string; created_at: string; subject: string; body: string; link: string | null;
                             read_at: string | null };

// ---- phase 6: steps 09, 11 and 13

export type Delivery = { label: string; rows: number; flagged: number; excluded: string | null };
export type Backtest = {
  status: "ran" | "not_back_tested"; total_rows: number; flagged_rows: number; share: number | null;
  deliveries: Delivery[];
};
export type RuleOnPage = {
  id: string; rule_key: string; version: number; description: string; sql: string; proposal_id: string;
  created_at: string; backtest: Backtest | null;
};
export type PendingRule = {
  id: string; status: "pending" | "approved" | "rejected"; created_at: string;
  payload: { rule_key: string; sql: string; description: string; element_keys: string[]; template: string | null;
             backtest: Backtest };
  required_approver_roles: string[]; decisions: Decision[];
};
export type RulesOnPage = { current: RuleOnPage[]; pending: PendingRule[]; rejected_keys: string[] };
export type GoLive = {
  id: string; created_at: string; feed_id: string; version: number; proposal_id: string; approved_by: string;
  mapping_ids: string[]; rule_ids: string[]; test_load_id: string; hypercare_until: string;
};
export type SummaryOnPage = {
  id: string; status: "pending" | "approved" | "rejected"; created_at: string;
  payload: { summary: string; highlights: string[]; risks: string[]; unmapped_columns: string[];
             rejected_glossary: { source_field: string; note: string | null }[] };
  required_approver_roles: string[]; decisions: Decision[];
};
export type GoLiveOnPage = { latest: GoLive | null; hypercare_active: boolean; pending_summary: SummaryOnPage | null };
export type LoadStageRow = {
  stage: "raw" | "curated" | "validated" | "standard"; outcome: "stage_completed" | "failed_rolled_back";
  rows_in: number; rows_out: number; detail: Record<string, unknown>;
};
export type LoadOnPage = {
  id: string; purpose: "go_live_test" | "scheduled"; status: string; received_at: string; finished_at: string | null;
  failed_stage: string | null; failure: string | null; go_live_id: string | null; stages: LoadStageRow[];
  arrival_outcome: ArrivalOutcome | null;
  arrival_detail: { reason?: string | null; columns?: string[]; missing?: string[]; added?: string[];
                    same_as?: string; not_parked?: string } | null;
  certification: Certification | null;
};
export type ReturnedRule = {
  rule_key: string; sql: string; description: string; share: number; flagged_rows: number; total_rows: number;
};
export type RefusedRule = { rule_key: string; sql: string; reason: string };
export type Owner = { id: string; display_name: string };

// ---- phase 5: steps 05 and 06

export type GlossaryRun = {
  id: string; created_at: string; feed_id: string; reason: "registered" | "requested" | "sent_back";
  columns: string[]; requested_by: string; status: "queued" | "running" | "done" | "failed"; attempt: number;
  started_at: string | null; finished_at: string | null; results: Record<string, string> | null; error: string | null;
};
export type RunOnBanner = {
  id: string; feed_id: string; feed_code: string; feed_name: string; status: string; reason: string; attempt: number;
  created_at: string; finished_at: string | null; error: string | null; counts: Record<string, number>;
};
export type RunsBanner = { active: RunOnBanner[]; finished: RunOnBanner[]; waiting_proposals: number };

export type Mapping = { id: string; created_at: string; feed_id: string; source_field: string; element_key: string;
                        version: number; proposal_id: string };
export type ColumnMapping = {
  name: string; type: string | null; source_says: string | null; mapped_to: string | null; mapped_version: number | null;
  pending: { proposal_id: string; candidates: string[]; recommended: string | null; reason: string } | null;
  why_none: "with_glossary_builder" | "rejected_by_glossary_owner" | "type_does_not_fit" | null;
  versions: Mapping[];
};
export type ElementDraft = { key: string; name: string; domain: string; data_type: string; definition: string;
                             aliases: string[] };
export type ElementOption = { kind: "new_element" | "alias"; key: string; alias: string | null; element: ElementDraft | null };
export type Decision = { id: string; created_at: string; step: number; role: string; decided_by: string;
                         decision: "approved" | "rejected"; note: string | null };
export type ConfigProposal = {
  id: string; kind: "glossary_element" | "mapping"; status: "pending" | "approved" | "rejected"; created_at: string;
  decided_at: string | null; feed_id: string; feed_code: string; feed_name: string; column: string;
  // glossary_element: outcome, options, recommended (an index), clashes; mapping: candidates, recommended (a key)
  payload: { outcome?: "proposed" | "duplicate_alias" | "conflict_to_owner"; options?: ElementOption[];
             recommended: number | string | null; clashes?: string[]; candidates?: string[]; column_type?: string | null };
  reason: string; required_approver_roles: string[]; decisions: Decision[];
};

// One decision on a glossary or mapping proposal. choice: an option index (glossary) or an element key (mapping).
export function decideProposal(id: string, decision: "approved" | "rejected", choice: number | string | null,
                               note: string | null, devUser: string): Promise<{ proposal: ConfigProposal }> {
  return apiPost(`/api/amaze/proposals/${id}/decision`, { decision, choice, note: note || null }, devUser);
}

export function words(value: string): string {
  return value.replaceAll("_", " ");
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
    if (err.status === 403) return err.message.includes("needs role")
      ? `Not allowed: ${err.message}` : "Not allowed: this view needs a backend role.";
    if (err.status === 503) return "Service unavailable: check the Status view.";
    if (err.status === 409) return `Not now: ${err.message}`;
    if (err.status === 422) return `Not accepted: ${err.message}`;
    return err.message;
  }
  return "The API cannot be reached. Is it running on port 8000?";
}
