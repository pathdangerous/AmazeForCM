// Small fetch helper for the Amaze API, plus the shapes backend-ui reads.
// consumer-ui has its own copy of the helper (plan, phase 0 assumption 14).

export type Health = {
  status: "ok" | "error";
  database: "ok" | "unreachable" | "not_configured";
  adapters: Record<string, string>;
  unavailable: string[]; // selected adapters that are still Azure stubs
  four_eyes?: boolean; // REQUIRE_FOUR_EYES as the database says it (phase 7.8)
};

export type User = {
  id: string;
  display_name: string;
  email: string;
  roles: string[];
  app: "backend" | "consumer";
};

export type DomainCount = { domain: string; label: string; count: number };

// Read-only operational facts. Missing certification is null, never a fabricated zero.
export type OperationsOverview = {
  days: number;
  feeds_live: number;
  feeds_paused: number;
  onboardings_active: number;
  loads_count: number;
  average_trust_score: number | null;
  release_threshold: number;
  quality: null | {
    rows: number; flagged_rows: number; passing_rows: number; pass_percent: number;
    by_feed: { feed_id: string; feed_code: string; flagged_rows: number }[];
  };
  pending_decisions: {
    proposal_id: string; feed_id: string; feed_code: string; feed_name: string; session_id: string | null;
    kind: string; summary: string; agent: string; confidence: number | null;
  }[];
  recent_loads: {
    id: string; feed_id: string; feed_code: string; feed_name: string; source_code: string | null;
    received_at: string; status: string; purpose: string; rows: number | null;
    flagged_rows: number | null; trust_score: number | null;
  }[];
};

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
  stage: "candidate" | "registered" | "paused" | "rejected"; // registered = live; paused (phase 7.9) = live, not fetched
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
  todo_actions?: { proposal_id: string; action: string; columns: string[]; reason: string }[]; // phase 7.12, T1
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

// ---- phase 7.9: pause, resume and delete of a feed
export const pauseFeed = (feedId: string, reason: string, devUser: string) =>
  apiPost<{ result: { outcome: "paused" }; feed: FeedPage }>(`/api/amaze/feeds/${feedId}/pause`, { reason }, devUser);
export const resumeFeed = (feedId: string, reason: string, devUser: string) =>
  apiPost<{ result: { outcome: "resumed" }; feed: FeedPage }>(`/api/amaze/feeds/${feedId}/resume`, { reason }, devUser);
export type FeedDeleted = {
  outcome: "deleted"; feed_id: string; code: string; rows_deleted: Record<string, number>; blobs_deleted: number;
  blobs_failed: number; requirements_reset: number; notified: number;
};
export async function deleteFeed(feedId: string, reason: string, confirmCode: string, devUser: string): Promise<FeedDeleted> {
  const path = `/api/amaze/feeds/${feedId}`;
  const response = await fetch(path, {
    method: "DELETE",
    headers: { Accept: "application/json", "Content-Type": "application/json", "X-Dev-User": devUser },
    body: JSON.stringify({ reason, confirm_code: confirmCode }),
  });
  if (!response.ok) throw await failure(path, response);
  return (await response.json()) as FeedDeleted;
}
export type FeedDeletion = {
  id: string; at: string; feed_id: string; code: string; name: string; by: string; reason: string;
  rows_deleted: Record<string, number>;
};

// ---- phase 7.8, task 2.5: a file uploaded straight to a new feed (POST /api/amaze/uploads...)

export type InspectedUpload = {
  outcome: "inspected"; upload_id: string; filename: string; format: "csv" | "xlsx" | "pdf"; sheets: string[];
  sheet: string | null; header_row: number; encoding: string | null; delimiter: string | null; columns: string[];
  row_count: number; preview: (string | null)[][]; skipped: Record<string, number>;
};
export type NeedsImageExtraction = { outcome: "needs_image_extraction"; upload_id: string; pages: number[] };
export type UploadRefusal = {
  outcome: "too_large" | "unsupported_format" | "not_a_table"; detail: string; upload_id?: string | null;
};
export type UploadAnswer = InspectedUpload | NeedsImageExtraction | UploadRefusal;
export type UploadedFeed = {
  outcome: "uploaded"; feed_id: string; feed_code: string; fetch_id: string; row_count: number; columns: string[];
  onboarding_id?: string; // the workspace session the confirm started (task 5.4)
};
export type UploadConditions = { licence?: string; permitted_use?: string; redistribution?: string; description?: string };
export type ReadOptions = { sheet?: string | null; header_row?: number | null };

const REFUSALS = ["too_large", "unsupported_format", "not_a_table"];

// The API answers a refused file with 413 or 422 and the outcome in the body: that is an answer, not a failure.
async function uploadAnswer(path: string, response: Response): Promise<UploadAnswer> {
  if (response.ok) return (await response.json()) as UploadAnswer;
  if (response.status === 413 || response.status === 422) {
    const body = (await response.clone().json().catch(() => null)) as { outcome?: string } | null;
    if (body && typeof body.outcome === "string" && REFUSALS.includes(body.outcome)) return body as UploadRefusal;
  }
  throw await failure(path, response);
}

// The file goes up as the raw body; the first read may name a sheet and a header row.
export async function uploadFile(file: File, devUser: string, opts: ReadOptions = {}): Promise<UploadAnswer> {
  const query = new URLSearchParams({ filename: file.name });
  if (opts.sheet) query.set("sheet", opts.sheet);
  if (opts.header_row) query.set("header_row", String(opts.header_row));
  const path = `/api/amaze/uploads?${query.toString().replaceAll("+", "%20")}`;
  const response = await fetch(path, {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/octet-stream", "X-Dev-User": devUser },
    body: file,
  });
  return uploadAnswer(path, response);
}

// A new file of an uploaded feed, written into its drop folder (it loads nothing; the first load step does).
export async function uploadFeedFile(feedId: string, file: File, devUser: string): Promise<{ stored_as: string }> {
  const path = `/api/amaze/feeds/${feedId}/files?filename=${encodeURIComponent(file.name)}`;
  const response = await fetch(path, {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/octet-stream", "X-Dev-User": devUser },
    body: file,
  });
  if (!response.ok) throw await failure(path, response);
  return (await response.json()) as { stored_as: string };
}

// The stored original, read again with another sheet or header row.
export async function reinspect(uploadId: string, filename: string, devUser: string, opts: ReadOptions): Promise<UploadAnswer> {
  const path = `/api/amaze/uploads/${uploadId}/inspect`;
  const response = await fetch(path, {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json", "X-Dev-User": devUser },
    body: JSON.stringify({ filename, sheet: opts.sheet ?? null, header_row: opts.header_row ?? null }),
  });
  return uploadAnswer(path, response);
}

// Creates the feed. A 409 (feed code taken) throws an ApiError; a table that no longer reads is a refusal answer.
export async function confirmUpload(uploadId: string, devUser: string, body: {
  filename: string; feed_code: string; feed_name: string } & ReadOptions & UploadConditions): Promise<UploadedFeed | UploadRefusal> {
  const path = `/api/amaze/uploads/${uploadId}/confirm`;
  const response = await fetch(path, {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json", "X-Dev-User": devUser },
    body: JSON.stringify(body),
  });
  const answer = await uploadAnswer(path, response);
  return answer as UploadedFeed | UploadRefusal;
}

// ---- phase 7.8, task 7.2: the onboarding workspace (/api/amaze/onboardings)

export const ONBOARDING_STEPS = ["upload", "profile", "glossary", "mapping", "transformations", "rules", "go_live",
                                 "first_load", "product_ready"] as const;
export type StepName = (typeof ONBOARDING_STEPS)[number];
export type StepState = "waiting" | "agent_working" | "needs_decision" | "ready" | "done" | "stale" | "failed";

export type OnboardingRow = {
  id: string; feed_id: string; feed_code: string; feed_name: string; current_step: StepName;
  step_states: Record<string, StepState>; status: "active" | "done"; created_by: string; created_at: string;
  updated_at: string; model_calls: number; model_call_cap: number;
};
export type OnboardingItem = {
  proposal_id: string; kind: string; column: string | null; summary: string; status: string; confidence: number | null;
  pre_accepted: boolean; needs_decision: boolean; waiting_for: string | null; edited: boolean; edited_by: string | null;
  edited_from: string | null; reason: string | null; payload: Record<string, unknown>;
  origin?: "agent" | "manual"; author?: string | null; // a manual item (T7) has an author and no model call
  decided_by: string | null; decided_at: string | null; // the latest approval decision; null while pending
};
// T2: GET /api/amaze/feeds/{id}/completion, and `inputs.completion` at go-live
export type Completion = {
  feed_id: string; columns_in_file: number; ready_for_go_live: boolean; words: string[];
  mappings: { done: number; pending: number; excluded: number; undecided: number;
              mapped_columns: { column: string; element_key: string }[]; excluded_columns: string[] };
  transformations: { current: { column: string; steps: string[] }[]; pending: number };
  rules: { current: { rule_key: string; description: string; backtest_status: string; flagged_rows: number | null;
                      total_rows: number | null; made_by: string }[]; pending: number };
  actions: { by_agent: number; by_person: number; rejected: number; pending: number };
  items: { agent: number; manual: number; manual_by_kind: Record<string, number>; agent_by_kind: Record<string, number> };
};
// A rule the rules step offers a person to pick instead of writing SQL (T7).
export type RuleTemplate = { template: string; rule_key: string; description: string; sql: string; element_keys: string[] };
// What a person types to add an item at a stage (core/domain/manual.py); the API answers 409 or 422 with a message.
export type ManualItemBody =
  | { kind: "glossary_element"; source_field: string; reason: string;
      option: { kind: "new_element"; key: string; element: Record<string, unknown> } | { kind: "alias"; key: string; alias: string } }
  | { kind: "mapping"; source_field: string; reason: string; element_key?: string; excluded?: boolean }
  | { kind: "transformation"; source_field: string; reason: string; steps: Record<string, unknown>[] }
  | { kind: "rule"; template?: string; rule_key?: string; sql?: string; description?: string; element_keys?: string[] }
  | { kind: "review_summary"; note: string }
  | { kind: "profile_action"; action: string; columns: string[]; reason: string; params?: Record<string, string> };
export type AgentNote = {
  proposal_id: string; agent: string; provider: string; model: string; model_version: string; prompt_id: string;
  prompt_version: string; latency_ms: number; input_ref: string; reasoning: string | null;
};
export type OnboardingJob = {
  id: string; session_id: string; step: StepName; status: "queued" | "running" | "done" | "failed" | "discarded";
  error: string | null;
};
export type StepView = { name: StepName; state: StepState; detail: string | null };
export type ProfileIssue = {
  kind: "duplicate_key" | "placeholder_only" | "placeholders" | "date_as_text" | "all_empty" | "constant";
  columns: string[]; count: number; detail: string;
};
export type InputFact = { label: string; value: string };
// "Inputs it saw": counts, names and flags from the database; never a sample value.
export type RuleFlags = { rule_key: string; flagged_rows: number; total_rows: number; sample_rows: number[] };
export type LoadBreakdown = {
  load_id: string; status: string; rows: number; flagged_rows: number; trust_score: string | null; threshold: string;
  below_threshold: boolean; rules: RuleFlags[]; next_action: string;
};
export type ExpectedScore = { rows: number; flagged_rows: number; trust_score: string; threshold: string; below_threshold: boolean };
export type StepInputs = {
  row_count: number | null; column_count: number | null; columns: string[]; issues: ProfileIssue[];
  key_columns: string[]; duplicate_key_count: number | null; candidate_counts: Record<string, number>;
  facts: InputFact[];
  load_summary?: string | null; // first load: one sentence about the latest scheduled load
  load_breakdown?: LoadBreakdown | null; // first load: per rule, the score and the next action (computed)
  expected_score?: ExpectedScore | null; // go-live: the score the test load predicts (computed)
  rule_templates?: RuleTemplate[]; // rules step: templates a person may pick (T7)
  completion?: Completion | null; // go-live: the completion report (T2), read only
};
export type KeyPolicy = { policy: "hold" | "keep_last" | "flag"; key_columns: string[]; reason: string };
export type Visibility = "open" | "restricted";
export type Workspace = {
  session: { id: string; feed_id: string; current_step: StepName; step_states: Record<string, StepState>;
             status: "active" | "done"; updated_at: string; model_calls: number };
  feed_code: string; steps: StepView[]; current_step: StepName; viewed_step: StepName; visibility: Visibility | null;
  inputs: StepInputs | null; items: OnboardingItem[]; job: OnboardingJob | null;
  progress: string | null; agent_notes: AgentNote[]; key_policy: KeyPolicy | null;
  model_calls: number; model_call_cap: number; can_approve: boolean; blocked_by: string | null;
};
export type StepAdvance = { moved: boolean; current_step: StepName; approved: string[]; waiting_for: string | null };
export type TimelineEntry = {
  at: string; kind: "event" | "proposal" | "edit" | "approval"; actor: string; step: string | null; title: string;
  proposal_id: string | null; detail: Record<string, unknown>;
};

// Approve & next refused: these items need a person's decision first (HTTP 409 decision_needed).
export class DecisionNeeded extends Error {
  items: OnboardingItem[];
  constructor(message: string, items: OnboardingItem[]) {
    super(message);
    this.items = items;
  }
}

const ONBOARDINGS = "/api/amaze/onboardings";

export const getOnboardings = (devUser: string) => apiGet<OnboardingRow[]>(ONBOARDINGS, devUser);
// step: view another step than the current one, read only (the API answers its items, notes and inputs)
export const getWorkspace = (id: string, devUser: string, step?: StepName) =>
  apiGet<Workspace>(step ? `${ONBOARDINGS}/${id}?step=${step}` : `${ONBOARDINGS}/${id}`, devUser);
export const getTimeline = (id: string, devUser: string) => apiGet<TimelineEntry[]>(`${ONBOARDINGS}/${id}/timeline`, devUser);
export const enterStep = (id: string, step: StepName, devUser: string) =>
  apiPost<Workspace>(`${ONBOARDINGS}/${id}/steps/${step}/enter`, {}, devUser);
export const goBack = (id: string, toStep: StepName, devUser: string) =>
  apiPost<Workspace>(`${ONBOARDINGS}/${id}/back`, { to_step: toStep }, devUser);
// choice: a glossary conflict's option index, or a mapping tie's element key; none: the recommended one
export const decideItem = (id: string, proposalId: string, decision: "approved" | "rejected", devUser: string,
                           choice?: number | string, note?: string) =>
  apiPost<{ workspace: Workspace }>(`${ONBOARDINGS}/${id}/items/${proposalId}/decision`,
                                    { decision, ...(choice === undefined ? {} : { choice }), ...(note ? { note } : {}) }, devUser);
export const addItem = (id: string, body: ManualItemBody, devUser: string) =>
  apiPost<{ workspace: Workspace }>(`${ONBOARDINGS}/${id}/items`, body, devUser);
export const editItem = (id: string, proposalId: string, payload: Record<string, unknown>, devUser: string) =>
  apiPost<{ workspace: Workspace }>(`${ONBOARDINGS}/${id}/items/${proposalId}/edit`, { payload }, devUser);

export async function setKeyPolicy(id: string, body: KeyPolicy, devUser: string): Promise<{ workspace: Workspace }> {
  const path = `${ONBOARDINGS}/${id}/key-policy`;
  const response = await fetch(path, {
    method: "PUT",
    headers: { Accept: "application/json", "Content-Type": "application/json", "X-Dev-User": devUser },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw await failure(path, response);
  return (await response.json()) as { workspace: Workspace };
}

// visibility is sent at go-live only (task 7.3a); any other step sends an empty body
export async function approveStep(id: string, visibility: Visibility | undefined, devUser: string):
    Promise<{ advance: StepAdvance; workspace: Workspace }> {
  const path = `${ONBOARDINGS}/${id}/approve`;
  const response = await fetch(path, {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json", "X-Dev-User": devUser },
    body: JSON.stringify(visibility ? { visibility } : {}),
  });
  if (response.status === 409) {
    const body = (await response.clone().json().catch(() => null)) as
      { detail?: { outcome?: string; message?: string; items?: OnboardingItem[] } } | null;
    if (body?.detail?.outcome === "decision_needed") {
      throw new DecisionNeeded(body.detail.message ?? "Decide the marked items first", body.detail.items ?? []);
    }
  }
  if (!response.ok) throw await failure(path, response);
  return (await response.json()) as { advance: StepAdvance; workspace: Workspace };
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
