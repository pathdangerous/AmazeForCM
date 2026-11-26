# Amaze for CM PoC — Build plan

One end-to-end path, built in phases. Each phase ends with something that runs and a test that proves it. Do not start a phase until the previous one passes.

**The PoC story:** a consumer asks for data that does not exist yet. The custom data product finds sources; the requester picks one and sees "being onboarded". The file is profiled with SQL before registration and the backend team signs it off. The glossary builder proposes elements in the background and a person approves them. The load runs on approved SQL rules, is certified and released, and the requester is told it is ready. A later delivery with a changed structure is held and a person is notified.

## Phase 0 — Skeleton and placeholders

- Repository layout as in `CLAUDE.md`. No Docker.
- `scripts/setup_db.ps1` creates the `amaze` database and user in the local PostgreSQL; Alembic runs the first migration.
- Python virtual environment for the API; `npm` projects for both UIs; `scripts/dev.ps1` starts the API and both UIs.
- All ports in `ports/` (renamed from `platform/`, which hides Python's built-in module) with local adapters working and Azure adapters as stubs (signatures, config keys, `NotImplementedError`).
- `LLM_PROVIDER=mock` by default; `MockProvider` returns schema-valid outputs from fixtures.
- `ProposalLog` table and `record()`; seeded dev users for each role.
- Import-boundary test: `core/engine` and `core/entitlement` cannot import `agents` or `ports.llm`.
- **Done when:** `scripts/dev.ps1` starts the API and both UIs, both UIs load in the browser, and tests pass.

## Phase 1 — Domain, repositories and glossary

- Models and tables: Requirement, Feed (candidate and registered), GlossaryElement, Mapping (level 1), Rule, Load, Product, Proposal, Approval, Notification.
- Seed a small synthetic enterprise glossary (security master and pricing elements).
- Feed registry and glossary views in `backend-ui`.
- **Done when:** a person can browse the glossary and the empty feed registry.

## Phase 2 — Consumption request and routing (17, routing checks, 01)

- `consumer-ui`: search (step 17), with "not entitled, shown with route to access".
- Routing checks in order: product exists → reuse; compose (stub); data in domains → create; else raise requirement (step 01).
- Requester status page showing the request's state.
- **Done when:** a search for missing data raises a requirement and the requester sees its status.

## Phase 3 — Custom data product ingestion (S1–S4)

Built in two parts (phase 3 plan): **3a** without agents (Known source?, S4 fetch, the daily job, connectors) and **3b** with them (S1–S3, the agent base, the DiscoveryAssistant). The requester's acceptance of a source's stated conditions is the only approval of a source; the backend team reviews the data in phase 4.

- IntentAgent and SourceDiscoveryAgent (mock) list candidate sources from `fixtures/sources/`, with licence and approvals needed.
- Requester picks a source in `consumer-ui`; sees "Your data is getting onboarded; we will notify you once it is ready."
- Local HTTP or file connector fetches a sample; local scheduler registers a daily job (Azure Functions stub in place).
- Negative outcomes: `no_fit`, `approval_missing`, `source_down`.
- The agent base class logs every proposal itself: `agents/base.Agent.run()` is the only public entry point; it calls the `LLMProvider`, then `ProposalLog.record()`, then creates the proposal rows linked to that log row. A subclass cannot replace `run()`. Tests prove a subclass defining `run()` is refused when the class is created, and that every proposal points at its `proposal_log` row (phase 1 plan, section 18).
- `HttpConnector` closes its connections: it gets `close()` and works in a `with` block; a test asserts the connection is closed after a fetch, including a failed one.
- Local scheduler jobs are kept in memory today: store them in PostgreSQL or re-register them from the database at start-up (phase 0 plan, risk 1).
- DiscoveryAssistant (step 17): search by element, domain or purpose, on `agents/base.Agent.run()`, beside phase 2's deterministic search (moved from phase 2; phase 2 plan, section 17).
- A selected source that stays down is released, by the requester or after `SOURCE_DOWN_RELEASE_DAYS` failed days in a row, and the requester picks again; every choice and release is a versioned `source_choices` row. The daily job fetches registered feeds always, and candidate feeds only while a requirement selects them (phase 3 plan, assumptions 49 to 55).
- **Done when:** picking a source produces a fetched file handed to step 02.

## Phase 4 — Profile, sign-off, register (02–04)

- DuckDB profiles the file directly: columns, types, null rates, distinct counts, coverage against the requirement. It reads the file only through `BlobStore.get()`, in memory, with no database connection held. A file with no data rows is `spec_only_low_confidence`.
- ProfilerAgent (mock) summarises; backend team signs off: `meets`, `meets_with_gaps`, `rejected`. The sign-off is the backend team's decision on the summary: approved when the person keeps the recommended outcome, rejected when they choose another. `meets` is refused on a spec-only profile.
- A rejection is final in the PoC: the feed is rejected, every requirement that selects it is released (`candidate_rejected`) and its requester is told the reasons; the source is not offered again.
- Register with owner (a data owner), licence (permitted use, redistribution, copied from the catalogue) and who may see it (open or restricted). A requirement handed to a source whose feed is already registered is `duplicate_linked`: by source code, with nothing fetched or registered again.
- **Done when:** a rejected candidate keeps its reasons and the requester is told; an accepted one is registered.

## Phase 5 — Glossary builder and mapping (05–06)

- GlossaryBuilderAgent runs in the background on new columns only; progress banner in `backend-ui`; glossary owner approves.
- MappingAgent proposes source → standard model mappings; steward approves; ties go to the steward.
- Gold mappings in `fixtures/gold/` and an evaluation script that scores any `LLMProvider` against them (needed when Azure OpenAI replaces the mock).
- **Done when:** approved mappings are stored as a versioned configuration.
- As built (phase 5 plan, `docs/plans/phase-5.md`): registering a feed queues one `glossary_runs` row (one active run per feed), committed with the registration; the worker claims it with a lease (`GLOSSARY_RUN_LEASE_SECONDS`), calls the builder with no connection held and records one proposal per new column (`proposed`, `duplicate_alias` as an alias proposal, or `conflict_to_owner`). Both agents see at most 10 candidate elements per column, never the whole glossary; the DiscoveryAssistant too. Step 06 runs in the steward's request; a column with no element goes back to step 05 as a `sent_back` run. Migration `0016` makes `mappings` and non-seed `glossary_elements` match their approved proposal, allows one pending proposal per feed column, and guards the run table. `python -m agents.evaluate_mappings` logs to `amaze_eval` (`EVAL_DATABASE_URL`); the mock scores 25 of 31.

## Phase 6 — Rules, approval and the run engine (09, 11, 13)

- RuleRecommenderAgent proposes SQL rules; back-test on fixture history; steward approves, then checker.
- ReviewSummariserAgent drafts the go-live summary; data owner approves; job scheduled; hypercare flag set.
- Run engine: raw → curated → validated → standard model, each stage tracked; failed file rolled back. No AI.
- Mastering and transformations: minimal pass-through in the PoC, clearly marked.
- **Done when:** an approved feed loads end to end from configuration alone.
- **As built (phase 6, `docs/plans/phase-6.md`):** migration `0018` (go-lives, load stages, standard records, rule
  and load guards G1 to G11); `core/engine/` (sandbox, curate, pass-through, run); steps 09 and 11
  (`s09_rules.py`, `s11_go_live.py`); the daily job loads a feed after its fetch once it has a go-live, and
  operations' Load now; backend-ui Rules, Go-live and Loads sections. The done-when test is
  `test_api_amaze_go_live.py::test_done_when_an_approved_feed_loads_end_to_end_from_configuration_alone`.

## Phase 7 — Every load and release (12, 15) and the consumer loop closing

- Step 12: arrival check and deterministic structure check against the template: `additive_parked`, `breaking_held`, both notify.
- Step 15: certification with a simple trust score; `released` or `held_consumers_warned`.
- On release, the product is created (step 20) and the requester gets "Ready" (or "Access restricted").
- **Done when:** the Playwright test runs the full PoC story, including one held delivery.
- **As built (phase 7, `docs/plans/phase-7.md`):** migration `0019` (arrival fields and the `quarantined` and
  `abandoned` statuses on loads, `load_certifications`, `products.feed_id`, the `structure_change` glossary run
  reason; guards G12 to G16); steps 12, 15 and 20 (`s12_arrival.py`, `s15_certify.py`, `s20_create_product.py`),
  run in one finishing transaction after the engine; the stale clear; the daily job and Load now run 12, 13, 15
  and 20; the back-test also reads the feed's own loads; requester states up to "Ready"; `GET /api/notifications`
  and a Notifications tab in both UIs; parallel test runs (`pytest -q -n 6`, a schema per worker). The done-when
  test is `e2e/tests/poc_story.spec.ts`, on `amaze_test` schema `e2e_story`.

## Phase 7.5 — UI polish

- Work through `docs/ui-polish.md`.
- First decide on a component library and the visual reference; then apply them to both UIs.
- **Done when:** the Playwright test still passes and the screenshots are approved.
- **As built (phase 7.5, `docs/plans/phase-7.5.md`):** Mantine 9.7.4 and one `src/theme.ts` (the Hexaware
  palette, derived dark values, every text/background pair at 4.5:1; byte-identical in both apps, checked by
  `tests/test_ui_shared_look.py`); both UIs converted; component checks (`npm test`, vitest) in both apps; the
  screens harness (`e2e/playwright.screens.config.ts`) and the before and after shots in
  `docs/phase75-screenshots/`. Read fields only, no migration: `GET /api/amaze/people`, `source_name`,
  `raised_at`, `coverage_titles`, `element_names`, the builder's own rule for `why_none`; notification links at
  five sites. Each polish line's fate is in `docs/ui-polish.md`.

## Phase 7.9 — Feed lifecycle

- Existing scope and results: `docs/plans/phase-7.9.md`.

## Phase 7.10 — One entrance, two personas

- Owner-approved scope: reproducible Replit setup, one preview entrance, Marketplace under `/market/`,
  and shared development persona selection. Plan: `docs/plans/phase-7.10.md`.
- **Done when:** fresh runtime setup, both B10 stories, both UI suites and the full Python suite pass;
  both apps work through port 5000 and locally. Push the phase branch; do not merge.

## Phase 7.11 — UI redesign

- Owner-approved references: `docs/design/phase-7.11/`; implementation plan:
  `docs/plans/phase-7.11.md`.
- Match the supplied tokens and mockups with Mantine and shared components.
  Operations overview uses real data only; existing behavior and test IDs stay intact.
- **Done when:** both UI suites, full Python suite, both B10 stories, contrast and
  keyboard checks pass; screens are written to `docs/phase75-screenshots/phase711/`.
  Push `phase-7.11`; do not merge.

## Phase 7.12 — Business-user profile, completion report, versioned code, drift, manual items

Plan: `docs/plans/phase-7.12.md`. Tasks: T0 decisions, T1 profile for business users and agent actions, T2 completion
report, T3 generated code (migration 0029), T4 drift on a new delivery, T5 overview, T6 demo and roadmap, T7 manual items.

Decisions (T0):
- Agents draft code; a person approves it; only approved code runs, and it runs with no model (rule 2).
- Exception fixes: agents propose, a person approves in bulk; an agent never changes data alone (rule 1).
- Erasing profiling history is a recorded erase, like 7.9's feed delete (rule 7): never an edit in place.
- The demo covers T1-T4 and T7; whatever is not built goes on the roadmap (T6).
- **Done when:** `pytest -q -n 6`, both UI suites and all Playwright stories pass. Push `phase-7.12`; do not merge.

## Phase 7.13 — Run all agents ahead, review the chain once

Plan: `docs/plans/phase-7.13.md`. One click on the onboarding ("Run all agents", session `run_mode = chain`) makes the
worker run glossary, mapping, transformations and rules in turn on **draft** upstream proposals; a person reviews the
whole chain once and approves top to bottom. Tasks: T0 design (stop for review), T1 chain worker and drafts
(migration 0032; T1a, T1b, T1c), T2 chain review screen, T3 approval order and stale reruns, T4 the system-rejection
defect (Back), T5 demo (`docs/demo.md`) and finish.

Decisions (T0, approved 2026-11-24):
- Rule 1 unchanged: every item is approved by a person, one approval row per item; "Approve all confident" loops the
  existing per-item decisions. Nothing is written to `mappings`, `rules` or `transformations` before its approval.
- A draft records the drafts it used (`proposal_bases`); "stale" is derived (an upstream draft was edited, rejected or
  is itself stale), never stored. An edit or rejection re-runs only the agents of the stale drafts.
- The database refuses the approval of a draft whose upstream drafts are not approved.
- The profile approval stays the one click that registers the feed; the chain runs after it. Go-live and steps 12-15
  are unchanged.
- **Done when:** `pytest -q -n 6`, both UI suites and all Playwright stories pass. Push `phase-7.13`; do not merge.

## Phase 7.14 — Exceptions, agent-proposed fixes, bulk approval, rerun

Plan: `docs/plans/phase-7.14.md`. Tasks: T0 design (stop for answers), T1 exceptions view per load and feed (migration
0033: the value-pattern function), T2 ExceptionFixAgent and resolutions (migration 0034), T3 bulk approve and rerun of a
held load, T4 owner notifications and Overview counts (by agent / by hand).

Decisions (T0, answered 2026-11-24): a load rerun after a transformation fix waits for the owner's go-live; waivers are
not pinned by the go-live, are approved by the steward and then the data owner named at registration, count as unflagged
and are shown separately in the certification; a rerun is `purpose = 'scheduled'` + `rerun_of_load_id` + reason; fixes
are transformation, waive and refer. Exception fixes are kept out of 7.13's `draft_config()` and have no bases.

- Exception = one flagged row and rule; group = rule + value pattern. An agent proposes one fix per group (transformation,
  waive with reason, refer to provider); a person approves in bulk; an agent never changes data (rule 1); records stay
  append-only (a waiver is read by the engine at the rerun, never written into an old record).
- A rerun is a new, recorded scheduled load of the held load's file (`rerun_of_load_id`, reason); step 12 skips the
  resend check against that one load only.
- Out of scope: owner reports and recurring-failure patterns (7.15).
- **Done when:** `pytest -q -n 6`, both UI suites and all Playwright stories pass; `docs/demo.md` covers 7.14.
  Push `phase-7.14`; do not merge.

## Phase 7.15 — Reports, recurring patterns, audit view

Plan: `docs/plans/phase-7.15.md`. Tasks: T0 plan, T1 recurring failure patterns (code only, no model), T2 a report per data
owner and per data steward (numbers from code; `ReportSummariserAgent`, A1, may write the narrative), T3 daily and monthly
report jobs (in-app notices; email stays a stub), T4 the audit view with a CSV export.

- A pattern is the same rule failing on the same weekday, day of month or provider date across at least N loads (a
  setting), with the dates as evidence; groups referred to the provider in 7.14 are listed once.
- A report is append-only and versioned (migration 0035); its numbers are counted by code from the tables and hashed by the
  database; the narrative is a separate field, logged like every model call, and can never change a number (a number the
  narrative states that the report does not hold refuses the narrative, not the report).
- Out of scope: sending email, and the erase of profiling history (7.16).
- **Done when:** `pytest -q -n 6`, both UI suites and all Playwright stories pass; `docs/demo.md` covers 7.15.
  Push `phase-7.15`; do not merge.

## Phase 7.16 — Demo data for the reports; recorded erase of profiling history

Plan: `docs/plans/phase-7.16.md`. Tasks: plan, T0a "Preview today so far" on the Reports screen (numbers only, never
stored), T0b `python -m core.seed --demo-history` (21 days through the real daily pipeline with an injected clock, one
weekday pattern), T1 `amaze_erase_profiles(feed, before, reason, by)` (migration 0036: the only delete path of a profile;
keeps the latest profile and any profile a registration, glossary run, arrival, code version or report points to; writes
append-only `profile_erasures`), T2 the History panel (select, reason, confirm; "erased by X") and the audit view's rows.

- Exception to rules 1/7, like 7.9's: a profile is a measurement of a file, not configuration; only the function deletes it,
  and the record outlives it.
- Out of scope: erasing anything but profiles, erasing the latest profile, email.
- **Done when:** `pytest -q -n 6`, both UI suites and all Playwright stories pass; `docs/demo.md` covers 7.16.
  Push `phase-7.16`; do not merge.

## Phase 7.17 — Code pipeline: commit, checks, test load, result; go-live needs a pass

Plan: `docs/plans/phase-7.17.md`. Tasks: plan, T1 port `Pipeline` (`ports/pipeline`, `PIPELINE_PROVIDER=local|azure_devops`;
local = a separate git repository under `PIPELINE_REPO_ROOT`, one folder per feed, one commit per code version; Azure DevOps
a stub), T2 `pipeline_runs` (migration 0037, append-only) with the stages commit, SQL parse, sandbox rule check, test load
and a typed result, and a go-live that needs a passed run of the code version it pins, T3 the Code view (runs, stages, logs,
commit hash, "Run pipeline") and the endpoints.

- Code is generated from approved configuration (7.12); the pipeline only checks and records it. No model call anywhere
  (rule 2's spirit: deterministic code); nothing is written inside this repository.
- Outcomes: `passed`, `failed_at_commit`, `failed_at_sql_parse`, `failed_at_rule_check`, `failed_at_test_load`; a go-live
  without a passed run is the named refusal `PipelineNotPassed` (409).
- Out of scope: Azure DevOps implementation, remotes and pushes, scheduled runs, a run of anything but the generated code.
- **Done when:** `pytest -q -n 6`, both UI suites and all Playwright stories pass; `docs/demo.md` covers 7.17.
  Push `phase-7.17`; do not merge. Run `alembic upgrade head` on dev and on Replit (migration 0037).

## Phase 7.18 — A seam for in-house data agents

Plan: `docs/plans/phase-7.18.md`. Tasks: plan, T1 port `ExternalAgent` (`ports/external_agents`,
`EXTERNAL_AGENT_PROVIDER=none|hexaware`; `hexaware` is a stub that raises `NotImplementedError`, `none` is never called),
T2 `Agent.run()` may route one named agent through it (`EXTERNAL_AGENT_FOR=<agent name>`, empty by default), logged by
`ProposalLog` with provider `hexaware`, a failure is the typed outcome `external_agent_unavailable` and writes nothing, T3 a
read-only Settings page in backend-ui (each agent, its provider, whether `EXTERNAL_AGENT_FOR` is set; no secret value).

- Out of scope: any network call, any real Hexaware client, changes to which agents exist. Tests use a fake adapter only.
- **Done when:** `pytest -q -n 6`, both UI suites and all Playwright stories pass; `docs/demo.md` covers 7.18.
  Push `phase-7.18`; do not merge. No migration expected (plan, assumption 9).

## Phase 7.19 — Glossary upload and Add element; a column added after the first release joins the product

Plan: `docs/plans/phase-7.19.md`. Part A (the glossary is the base for auto-mapping): T1 migration 0038 (proposal kind
`glossary_import`, no feed, approver `glossary_owner`; new versions of the glossary guards for that kind only; append-only
`glossary_uploads`), T2 upload of a CSV or XLSX (`key, name, domain, data_type, definition, aliases`), each row labelled `new |
update | unchanged | invalid(reason) | alias_conflict(element) | duplicate_in_file`, proposals only for new and update rows,
T3 Glossary page (upload, template, labelled rows with a filter, Approve/Reject selected, "Add element" = a one-row upload),
T4 the 7.15 audit view and CSV list uploads and decisions. Part B: T5 step 20 adds elements the feed's mappings gained to the
product (nothing deleted), an element no longer mapped is left out of the page, dictionary, preview and downloads, consumers get
an in-app notice.

- Rule 1: every row is a proposal a glossary owner approves. Rule 7: an approved row is a new element or a new version, never an
  edit in place. No model call (nothing is added to `proposal_log`).
- Out of scope: deleting an element, a new domain through a file, a model reading the file.
- **Done when:** `pytest -q -n 6`, both UI suites and all three Playwright stories pass; `docs/demo.md` covers 7.19.
  Push `phase-7.19`; do not merge. Run `alembic upgrade head` on dev and on Replit (migration 0038).

## Phase 7.20 — Click a rule, see its rows (green passes, red fails)

Plan: `docs/plans/phase-7.20.md`. T1 `GET /api/amaze/rules/{proposal_or_rule_id}/rows?failing_only=&page=&size=`: a rule proposal
runs its SQL in the sandbox on the feed's latest file with the draft configuration (no connection held); an approved rule reads
the newest load's `standard_records` flags (waived is its own state). Typed outcomes `rows_shown`, `rule_not_found`, `no_file`,
`sql_refused`. T2 shared `RuleRowsPanel` opened from the rule card in the Rules step, the chain review and the feed page:
affected headers marked, cells green / red / amber as fills with words, "N of M rows fail", a failing-only toggle, paging, Esc.
T3 docs.

- Rules: read only, no model call, no write, no migration.
- Out of scope: editing a rule here, a rule's history across loads, downloads.
- **Done when:** `pytest -q -n 6`, both UI suites and all three Playwright stories pass; `docs/demo.md` covers 7.20.
  Push `phase-7.20`; do not merge.

## Phase 7.21 — Recurring or one-time, and a Deliveries dashboard

Plan: `docs/plans/phase-7.21.md`. T1 append-only `feed_schedules` (migration 0039; kind one_time|recurring, frequency, weekday or
day of month, expected time, grace, timezone, notify owner; a change is a new version; checks in the database). T2 "Recurring
feed or one-time load?" on "Create the feed" (required, written with the feed), "Change schedule" on the feed page, the audit
view lists changes; a feed without one shows "Schedule not set". T3 deterministic expected vs actual (`on_time`, `late`,
`missing`, `resent_quarantined`, `held`, plus `unexpected`; typed results) and a once-per-delivery missing alert to the owner.
T4 Deliveries tab (figures, calendar, arrival vs window, rows over time), operations ranking, product page "Updated daily".
T5 demo history with one missing day and two late arrivals, `docs/demo.md`, the demo story.

- Rules: no model call; a schedule is a person's setting (versioned, who and when kept); no guard is loosened.
- Out of scope: the source's own delivery time, holidays, a schedule proposal.
- **Done when:** `pytest -q -n 6`, both UI suites and all three Playwright stories pass; `docs/demo.md` covers 7.21.
  Push `phase-7.21`; do not merge. Run `alembic upgrade head` on dev and on Replit (migration 0039).

## Phase 7.22 — Mapping before glossary; "Run all agents" removed

Plan: `docs/plans/phase-7.22.md`. Owner decisions after the demo: the 7.13 chain mode is removed; the onboarding order is
Profile, Mapping, Glossary requests, Transformations, Rules, Go-live, First load, Product ready. A column is mapped to the existing
glossary first; a column no element fits is "Cannot map: no glossary element fits" and the person chooses **Ignore** (the existing
excluded mapping) or **Request glossary element** (a builder run for that column only; the glossary owner approves; the approval
creates the column's mapping proposal). An ignored or requested column never blocks go-live and is listed by name on the
completion report, the feed page and the product page. T1 remove chain mode (per-step "Approve all confident" stays, the Back /
system-rejection fix stays, 0032's tables stay unused). T2 mapping first, cannot-map items, Ignore. T3 Request (migration 0040: two
CHECK constraints), the mapping proposal on approval, the lists. T4 element names are links with a definition card. T5 a rejected
transformation is not proposed again. T6 demo story, docs, finish.

- Rules: no model call in the run engine or entitlement; every decision is a person's; no guard is loosened or worked around.
- Out of scope: taking back a request, a MappingAgent run after an approval, the legacy workbench screens, steps 12 to 15.
- **Done when:** `pytest -q -n 6`, both UI suites and all three Playwright stories pass; `docs/demo.md` covers 7.22.
  Push `phase-7.22`; do not merge. Run `alembic upgrade head` on dev and on Replit (migration 0040).

## Phase 8 — Azure move (on the office laptop)

- Implement `AzureOpenAIProvider`; run the evaluation script against the gold mappings and compare with the mock.
- Implement Azure Blob, Functions timer, Entra ID, Key Vault and email or Teams adapters.
- Point `DATABASE_URL` at the office laptop's PostgreSQL first, run the migrations and the full test suite, then at Azure Database for PostgreSQL.
- Fill in the Bicep templates; deploy to the company subscription. Add containers here only if the Azure hosting choice needs them.
- Switch adapters by environment variables only. No business logic changes.
- **Done when:** the same Playwright test passes against the Azure deployment.

## Out of scope for the PoC

Composition engine (stub only), full mastering survivorship, value drift agent (step 16), exception investigation agent (step 14), streaming delivery, multiple tenants.
