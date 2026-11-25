# Amaze for CM — Proof of Concept

This file tells Claude Code what this repository is and the rules it must follow. Read it fully before every task.

## What this is

A proof of concept (PoC) of the redesigned Amaze for Capital Markets: an agentic data onboarding and consumption system. It proves one end-to-end path. A bigger team builds the production system after approval, so favour clear structure and replaceable parts over completeness.

The system is three platforms, in the order data moves:

1. **Custom data product (ingestion only).** Activates only when an end user asks for data that Amaze has not produced and no known source has. It finds external sources, lists them to the requester with the approvals needed, and once the requester picks one, a daily scheduler fetches new data into Amaze.
2. **Amaze.** The backend team's platform. Onboards each feed once (steps 01–11), then runs every load (steps 12–16).
3. **Data product consumption.** The only platform end users see. Search first, reuse, compose or create; build new only when needed (steps 17–22).

## Non-negotiable rules

1. **AI proposes; people decide; rules execute.** An agent never changes configuration or data directly. It writes a *proposal*. A named person approves or rejects it. Only approved proposals become versioned configuration.
2. **No model call in the run engine (step 13) or in entitlement decisions.** These are deterministic code only.
3. **Every negative outcome is explicit.** Each step returns a typed result: success or one of its named negative outcomes (see the step table). Never return a bare error for an expected case.
4. **Every AI proposal is logged** with the agent name, provider, model and version, prompt version, input reference, output, and who approved or rejected it.
5. **Everything Azure sits behind an interface** (see Placeholders). Local implementations run on a Windows laptop with no containers; Azure implementations are switched on by configuration. Business logic never imports an Azure SDK directly.
6. **Voided on 2026-11-22.** The rule was "synthetic data only". The code now lives only on company-approved servers (the company GitHub repository and Replit), so client files, client names and real vendor data may be used. Secrets still never go in the repository: `.env` locally, Replit Secrets on Replit. Three tests still check that today's fixtures are synthetic (`tests/test_source_catalogue.py`, `tests/test_glossary_seed.py`, `tests/test_demo_seed.py`); change them in the phase that brings in real data.
7. **Configuration is versioned, never edited in place.** A change creates a new version.

**Exception to rules 1/7 (phase 7.9):** a feed may be hard-deleted only through `amaze_delete_feed()`; its deletion is recorded in `feed_deletions`. Nothing else deletes from an append-only table (the guards allow a DELETE only while `amaze.deleting_feed` names the row's own feed). Glossary elements and their proposals are kept. **Phase 7.16 adds one more:** a `feed_profiles` row may be deleted only through `amaze_erase_profiles()`, which keeps the latest profile and every profile a registration, sign-off, glossary run, arrival, code version or report points to, and records the erase in `profile_erasures`.

## Tech stack

- Backend: Python 3.14 (3.14.5 on this laptop and the office laptop; `requires-python = ">=3.14,<3.15"`), FastAPI, Pydantic v2, SQLAlchemy 2, Alembic. Create the venv with `py -3.14`.
- Data: PostgreSQL 18 (any 18.x minor) on both laptops (stores and repositories). Use no PostgreSQL feature newer than 14 until the Azure Database for PostgreSQL version is confirmed. DuckDB (`duckdb==1.5.6`, in memory; profiling, and later SQL rules, on files without staging).
- Frontend: React + TypeScript (Vite). Two apps: `backend-ui` (Amaze, backend team) and `consumer-ui` (consumption, end users). One look (phase 7.5): Mantine 9.7.4 and `src/theme.ts`, byte-identical in both apps (the Hexaware palette, derived dark values, every text/background pair in `CONTRAST_PAIRS` at 4.5:1; `tests/test_ui_shared_look.py` checks it). Warning, info and the current-state marker are fills, never text. Selects stay native (`NativeSelect`), checkboxes and radios native inputs, and a disabled button carries `disabled`: the Playwright story relies on all three.
- Local runtime: no containers. PostgreSQL installed directly on Windows. PowerShell scripts are plain ASCII so Windows PowerShell 5.1 reads them correctly. Files go to a local folder (`STORAGE_PROVIDER=local`). The API runs with uvicorn in a Python virtual environment and the two UIs with `npm run dev`. `scripts/dev.ps1` starts all three; `scripts/setup_db.ps1` creates the database and its user.
- Infrastructure as code: Bicep under `infra/`.
- Tests: pytest, and the full run is `pytest -q -n 6` (pytest-xdist: each worker has its own schema `w_<worker>` of `amaze_test`, so no test reads another worker's schema; one file still runs serially). Playwright for one end-to-end test of the PoC path, in `e2e/`: it starts the API and both UIs itself on `amaze_test`, schema `e2e_story`, never on `amaze` (`cd e2e; npx playwright test`). Component checks in each UI (`npm --prefix apps\<ui> test`: vitest, jsdom, a stubbed `fetch` from `src/test/fixtures.ts`; run from a path with an upper-case drive letter). The screens run (`e2e/playwright.screens.config.ts`, `e2e/screens/screens.spec.ts`) repeats the story's path and writes `docs/phase75-screenshots/<SHOTS_SET>/`; a change to the story needs the same change there.

## Repository layout

```
apps/
  api/                    FastAPI app, routers per platform; jobs.py: the one daily source fetch, registered
                          at every start-up, reading the feeds table when it runs; and GlossaryRuns, the
                          step 05 worker: kicked (Scheduler.run_soon) after the commit that queues a run,
                          and once at start-up for queued runs and expired leases
  backend-ui/             Amaze workbenches (data owner, steward, glossary owner, operations)
  consumer-ui/            Marketplace portal for end users
core/
  domain/                 Pydantic models: Feed, Requirement, Proposal, Mapping, Rule, Load, Product, ...
  repositories/           Tables (tables.py), named errors (errors.py) and one repository per model.
                          Repositories flush but never commit; the caller owns the transaction.
  proposal_log.py         ProposalLog.record(): one row per model call (rule 4)
  seed.py                 python -m core.seed: loads the synthetic glossary (fixtures/glossary/);
                          --demo loads the demo products (fixtures/demo/, codes start with demo_)
  steps/                  One module per step (s1_scope.py ... s22_monitor.py); each returns a typed StepResult
                          (result.py: a Pydantic union discriminated on `outcome`). Also matching.py (request
                          text to glossary elements), routing.py (routing checks), request_data.py (one
                          "Request this data", end to end) and errors.py (refused step input). Phase 3:
                          known_source.py (Known source?), s1_scope.py, s2_sources.py, s3_pick.py, s4_fetch.py
                          (S1-S4), release.py (release a selected source) and requirement_page.py (what the
                          requester sees, and every requester message). Phase 4: profiling.py (DuckDB in
                          memory over one CSV, and coverage against each requirement), s02_profile.py,
                          s03_signoff.py, s04_register.py (steps 02-04) and feed_page.py (what the backend
                          team sees about one feed). Phase 5: candidates.py (at most 10 candidate elements
                          per column or term, scored on names, aliases and type), s05_glossary.py (queue,
                          claim and record a glossary builder run; the owner's decision) and s06_map.py
                          (propose mappings; the steward's decision). Phase 6: rule_candidates.py (at most 5
                          rule templates per mapped element), s09_rules.py (propose and back-test rules; the
                          steward's, then the checker's decision) and s11_go_live.py (prepare: checks, a test
                          load, the summary; the owner's decision). Phase 7: s12_arrival.py (arrival and
                          structure of a scheduled delivery; also the stale clear and late_alert),
                          s15_certify.py (the trust score) and s20_create_product.py (the feed's product and
                          the requester's state; also finish_scheduled_load(), the one finishing transaction).
                          A step that needs an agent receives it
                          as an argument, so core/steps never imports agents
  engine/                 Deterministic run engine (step 13). No AI imports allowed here. run.py (plan_load,
                          execute_load, record_load), curate.py (the cast), passthrough.py (mastering and
                          transformations are a marked pass-through) and sandbox.py (the locked-down DuckDB
                          every agent-proposed SQL rule runs in; nothing else runs rule SQL)
  entitlement/            Rules-based entitlement decisions (decide.py). No AI imports allowed here.
agents/
  base.py                 Agent base class. Agent.run() is the only path to the model: it calls the
                          LLMProvider, stores the input, logs the call, then creates the proposals. A
                          subclass that defines run() is refused when the class is created
  registry.py             Agent registry, current prompt versions and a hash of every prompt file
  prompt_loader.py        Loads and renders prompts/<prompt_id>.<version>.yaml
  <agent_name>.py         One file per agent
  evaluation.py           Scores a provider's mappings against fixtures/gold/mappings.json;
                          evaluate_mappings.py runs it (logs to EVAL_DATABASE_URL, never DATABASE_URL)
  prompts/                Versioned prompt templates (YAML). Never edit one: save a new version
ports/                    Ports (interfaces) and adapters — see Placeholders. Named ports/, not
                          platform/, because a package named platform hides Python's own platform module.
                          ports/__init__.py stays empty; each port's factory lives in its own package.
  db/                     Database engine, session and table base class (selected by DATABASE_URL)
  llm/                    LLMProvider: mock (default), azure_openai (stub)
  storage/                BlobStore: local, azure_blob (stub)
  scheduler/              Scheduler: local (APScheduler), azure_functions (stub)
  identity/               Identity: dev users, entra_id (stub)
  secrets/                Secrets: env file, key_vault (stub)
  notify/                 Notifier: in-app, email/Teams (stub)
  external_agents/        ExternalAgent (phase 7.18): none (default, never called) | hexaware (a stub until their API is known).
                          Only agents/base.py calls it: EXTERNAL_AGENT_FOR=<agent name> routes that one agent through it,
                          logged with the adapter's provider; a failure is ExternalAgentUnavailable (503
                          external_agent_unavailable) and writes nothing. core/ never imports it (a test enforces this)
  pipeline/               Pipeline (phase 7.17): local = a git repository of its own under PIPELINE_REPO_ROOT (one folder
                          per feed, one commit per code version, never this repository), azure_devops (stub)
  connectors/             Source connectors: local file, HTTP (for the custom data product), and the source
                          catalogue (catalogue.py; each entry's columns name the file column of every
                          declared field). HTTP reaches only SOURCE_HTTP_ALLOWED_HOSTS (this machine until
                          the Azure move) and never follows a redirect
migrations/               Alembic migrations (alembic.ini at the repository root)
scripts/                  setup_db.ps1, dev.ps1 (PowerShell, Windows)
infra/                    Bicep templates (placeholders until the Azure move)
fixtures/                 Synthetic feeds, sample files, glossary seed, gold mappings for evaluation;
                          sources/: the synthetic source catalogue and its files (codes start with syn_)
                          e2e/: the file the Playwright story swaps in for its held delivery
e2e/                      The Playwright story (phase 7): prepare.py rebuilds schema e2e_story of amaze_test,
                          serve_api.py serves it (DAILY_JOB_ENABLED=false, mock provider, own drop folder,
                          REQUIRE_FOUR_EYES from the environment, false by default), tests/poc_story.spec.ts is the
                          test (run with REQUIRE_FOUR_EYES=true). Phase 7.8: tests/operator_story.spec.ts (one
                          operator, upload to download) and screens/workspace.spec.ts (its shots, config
                          playwright.workspace.config.ts) share one path, operator_flow.ts
tests/
```

## Placeholders (fill these after the move to the office laptop)

Every adapter below is selected by an environment variable. The default is the local one. Azure adapters exist as stubs: correct class, correct method signatures, configuration keys read from the environment, and `NotImplementedError("Azure adapter: implement after move")` in the body. Tests run against the local adapters only.

| Port | Local (now) | Azure (after the move) | Selector |
|---|---|---|---|
| `LLMProvider` | `MockProvider`: deterministic, rule- and fixture-based proposals | `AzureOpenAIProvider` | `LLM_PROVIDER=mock\|azure_openai` |
| `BlobStore` | Local folder | Azure Blob / Data Lake Storage Gen2 | `STORAGE_PROVIDER=local\|azure_blob` |
| `Scheduler` | APScheduler inside the API | Azure Functions timer trigger | `SCHEDULER_PROVIDER=local\|azure_functions` |
| `Identity` | Seeded dev users with roles | Microsoft Entra ID | `IDENTITY_PROVIDER=dev\|entra_id` |
| `Secrets` | `.env` file | Azure Key Vault | `SECRETS_PROVIDER=env\|key_vault` |
| `Notifier` | In-app notifications table | Email or Teams | `NOTIFY_PROVIDER=inapp\|email` |
| `Pipeline` | Git repository of its own (`PIPELINE_REPO_ROOT`) | Azure DevOps repository | `PIPELINE_PROVIDER=local\|azure_devops` |
| `ExternalAgent` (phase 7.18) | none (nothing is called) | Hexaware in-house data agents (stub until their API is known) | `EXTERNAL_AGENT_PROVIDER=none\|hexaware`, `EXTERNAL_AGENT_FOR=<agent name>` |
| Database | Local PostgreSQL (Windows install) | Azure Database for PostgreSQL | `DATABASE_URL` |

### LLMProvider contract

```python
class LLMProvider(Protocol):
    name: str            # "mock" | "azure_openai"
    def complete(self, *, prompt_id: str, prompt_version: str, messages: list[Message],
                 response_schema: type[BaseModel], temperature: float = 0.0) -> LLMResult: ...

class LLMResult(BaseModel):
    output: BaseModel          # validated against response_schema
    provider: str
    model: str                 # deployment name for Azure OpenAI
    model_version: str
    prompt_id: str
    prompt_version: str
    latency_ms: int
    raw_ref: str | None        # pointer to the stored raw response
```

- `MockProvider` must return outputs that validate against the same `response_schema`, built from fixtures and simple heuristics (for example, name similarity for glossary mapping). It must be deterministic so tests and demos are repeatable.
- `AzureOpenAIProvider` reads `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_DEPLOYMENT`, `AZURE_OPENAI_API_VERSION`, and either `AZURE_OPENAI_API_KEY` or Entra ID through `DefaultAzureCredential` when `AZURE_OPENAI_AUTH=entra`. It uses structured outputs against `response_schema`. Stub only until the move.
- Agents call only `LLMProvider`. No agent imports an SDK.
- Every call goes through `ProposalLog.record(...)` before the proposal is shown to a person.

## Agents in the PoC

Each agent extends `agents/base.Agent`, declares `autonomy` (A1 assist or A2 recommend; A3 is not used in the PoC) and `approver_roles` (an ordered tuple, each role once; `()` for a read-only agent), and returns a `Proposal`.

| Step | Agent | Autonomy | Approver |
|---|---|---|---|
| S1 | IntentAgent: scope (public or private, who provides, where to search) | A2 | Requester |
| S2 | SourceDiscoveryAgent: candidate sources with coverage, licence, approvals needed | A2 | Requester picks (S3) |
| 02 | ProfilerAgent: summarises the DuckDB profile against the requirement | A2 | Backend team (03) |
| 05 | GlossaryBuilderAgent: new columns only (a new element, or an alias on an existing one), runs in the background | A2 | Glossary owner |
| 06 | MappingAgent: source fields to the standard model | A2 | Data steward |
| 09 | RuleRecommenderAgent: SQL rules, back-tested on past loads | A2 | Steward, then checker |
| 11 | ReviewSummariserAgent: summary for go-live | A1 | Data owner |
| 17 | DiscoveryAssistant: search by element, domain or purpose | A2 | — (read only) |

Step 12's structure check is deterministic (no agent). Step 13 and entitlement have no agent.

## Steps and negative outcomes in the PoC

Implement these as typed results in `core/steps/`. Names in `snake_case`.

- **17 Discover**: `found`, `not_entitled_visible` (shown, with the route to access)
- **Routing checks**: `reuse`, `compose` (stub for PoC), `create_product`, `raise_requirement`
- **01 Define requirement**: `defined`, `element_not_in_glossary`
- **Known source?**: `known_source` → 02, `no_known_source` → S1
- **S1–S4**: `scoped`, `scope_not_confirmed`; `sources_listed`, `no_fit`; `source_picked`, `approval_missing`; `fetched`, `source_down`
- **00 Intake (phase 7.8, an uploaded file)**: `inspected`, `uploaded`, `table_extracted`; `too_large`, `unsupported_format`, `not_a_table`, `needs_image_extraction`
- **02 Profile**: `profiled`, `spec_only_low_confidence`
- **03 Sign off**: `meets`, `meets_with_gaps`, `rejected` (reasons kept, requester told)
- **04 Register**: `registered`, `duplicate_linked`, `no_owner`
- **05 Glossary**: `proposed`, `duplicate_alias`, `conflict_to_owner`
- **06 Map**: `mapped`, `tie_to_steward`, `no_element_back_to_05`
- **09 Validation**: `rules_proposed`, `flags_most_history_returned`
- **10 Transform (phase 7.8)**: `transforms_proposed`, `nothing_to_transform` (the steward's decision; the engine applies approved transformations, no model call)
- **11 Approve**: `approved_scheduled`, `rejected_back_to_step`, `test_failed_held`
- **12 Arrival and structure**: `on_time_complete`, `late_alert`, `resent_quarantined`, `additive_parked`, `breaking_held`
- **13 Run**: `stage_completed` per stage, `failed_rolled_back`
- **15 Certify**: `released`, `held_consumers_warned`
- **20 Create product**: `created`, `data_not_in_domains`
- Requester messages: "Your data is getting onboarded; we will notify you once it is ready.", then "Ready" or "Access restricted".

## Plugins used in this project, and which one wins

Installed in Claude Code: Superpowers, Karpathy guidelines, Claude Octopus, i-have-adhd. **In this setup the plugins do not load.** The Superpowers and Karpathy method below is followed by hand, task by task: before code, list any assumption the plan does not state and stop if it changes scope; touch only the files the plan names (any other is a recorded deviation); tests first, the failing summary line pasted (RED), then the least code that passes and its summary line (GREEN); an unexpected failure is reproduced and its root cause written down before it is fixed; every claim in a report is backed by pasted output. Octopus reviews are done by hand too.

Installed as skills (files under `%USERPROFILE%\.agents\skills\`, not plugins): addyosmani/agent-skills and Ponytail. Graphify is a local tool (in `.venv`; `graphify-out/` and `.claude/` are not committed, and Replit has no graphify). OmniRoute is not used: other providers need sponsor approval.

Order of precedence when they disagree:

1. **This file's non-negotiable rules** override every plugin.
2. **Superpowers** is the working method: brainstorm, write the plan, test first, execute, review. Use it instead of ad-hoc planning. Plans go in `docs/plans/`, one per phase of `BUILD_PLAN.md`.
3. **Karpathy guidelines** apply to every change: surface assumptions first, keep it simple, change only what the task needs, and define how the result will be checked.
4. **agent-skills** (addyosmani) apply where Superpowers is silent.
5. **Ponytail** (level full): the least code that solves the task, never at the cost of a typed negative outcome, a test or a guard.
6. **i-have-adhd** shapes replies to the developer: answer first, numbered steps, no filler. It changes how replies read, never what gets checked or reported. Problems, risks and failed tests are always stated.
7. **Claude Octopus** is off by default. Turn it on for a review at the end of each phase, then off again. Use the Claude provider only. Do not configure other AI providers (Codex, Gemini or others) in this repository unless the project sponsor has approved sending code to them.

## How to work in this repo

- Follow the Superpowers flow for each phase: brainstorm → plan in `docs/plans/` → tests for the step's negative outcomes first → implement → review.
- Keep `core/engine/`, `core/entitlement/`, `core/domain/`, `core/repositories/` and `core/steps/` free of `agents` and `ports.llm` imports; a test enforces this. An agent-backed step receives its agent as an argument; the API builds the agent (phase 3 plan, assumption 22). No file outside `agents/base.py` calls `.complete(`; a test enforces this too.
- Every step that changes a requirement (or any row others may change at the same time) locks that row first (`RequirementRepository.lock()`), and the API maps each named conflict to 409, never 500 (`apps/api/transactions.py`).
- One lock order for every step that takes more than one lock (phase 4 plan, assumption 46): the advisory lock on the source code (`lock_source()`), then the feed row (`FeedRepository.lock()`, `FOR NO KEY UPDATE`), then the requirement rows in id order. The next version of a feed's profile, sign-off or registration is read only after the feed row is locked. Phase 5 extends it (phase 5 plan, assumption 49): source lock, feed row, proposal row, requirements, the `glossary_runs` row, then the glossary advisory lock (`GlossaryRepository.lock()`); the next mapping or element version is read only after the feed row or the glossary lock is held. Phase 6 extends it again (phase 6 plan, assumption 36): source lock, feed row, proposal row, requirements, the `glossary_runs` row, the glossary lock, then the load row (`LoadRepository.lock()`); the next rule or go-live version is read only after the feed row is locked.
- Agent-proposed SQL runs only in `core/engine/sandbox.py`: the one file is read into memory, then `enable_external_access` is switched off and the configuration locked, and only then does a rule run, as exactly one plain SELECT with a `_row` column, under `RULE_TIMEOUT_SECONDS`. No other code runs rule SQL. The engine reads only `MappingRepository.current_for_feed()` and `RuleRepository.current_for_feed()`; an unmapped column is not loaded; a value that does not cast holds the whole file (`failed_rolled_back`); a scheduled load runs only the configuration the latest go-live approved. A load, its stages and its records are written by `record_load()` in one savepoint: a failed file leaves no standard record.
- From `0018` (phase 6 plan, section 6): a `rule` proposal has a feed and names its rule key and SQL, one pending rule per feed and key and one pending summary per feed; a `rules` row matches its approved proposal; `feed_go_lives`, `load_stages` and `standard_records` are append-only; a go-live needs an approved review summary of the feed, decided by the owner named at registration, pinning the current mapping ids, rule ids and a loaded test load; a load moves only received to loaded or failed_rolled_back (a loaded one on to held or released) with its proof, one received load per feed, stages in order, records only for a received load of the same feed. Never work around these triggers.
- From `0019` (phase 7 plan, section 6): a `go_live_test` load has no arrival, and a scheduled load's arrival (SHA-256, outcome, detail) is set once, while it is received; a scheduled load is `loaded` or `failed_rolled_back` only after step 12 passed it (`on_time_complete` or `additive_parked`), `quarantined` only as `resent_quarantined`, `held` on arrival only as `breaking_held`, `abandoned` (a load a dead process left received) with a failure and no stage or record, and a loaded scheduled load becomes `released` or `held` only with its `load_certifications` row (append-only; the database recounts rows and flagged rows and checks the score and the outcome); a product with a feed is of a registered feed that has a released load, has the latest registration's owner and visibility, and is the feed's only product; a glossary run's reason may be `structure_change`. Never work around these triggers.
- Step 12 runs on every scheduled load between the fetch and the engine, with no model call and no database connection held while the file is read: a file byte for byte the same as an earlier loaded, released or held load is `resent_quarantined`; a template column (the feed's latest profile) missing, or no CSV table, is `breaking_held`; new columns are `additive_parked` (a new profile and a `structure_change` glossary run); `plan_load()` refuses a scheduled load step 12 has not passed. Steps 13, 15 and 20 then run in one transaction (`finish_scheduled_load()`: the feed row, the requirements selecting the feed in id order, the load row; steps 15 and 20 inside the engine's savepoint), so a refusal fails the load naming the step. The trust score is 100 x unflagged rows / rows; at or above `TRUST_RELEASE_THRESHOLD` the load is released and step 20 creates the feed's product and readies each waiting requirement, below it the load is held and its consumers are warned.
- From `0037` (phase 7.17, `docs/plans/phase-7.17.md`): every new go-live needs a `passed` row of `pipeline_runs` for its feed and the SHA-256 of the code version it pins; `decide_go_live()` asks first (`PipelineNotPassed`, 409). A run is append-only evidence (stages commit, sql_parse, rule_check, test_load; the database recomputes the outcome), made by `core/steps/pipeline.py` with no model call, no database connection held and a dry test load that writes nothing. Tests commit to a temporary pipeline repository (`tests/conftest.py`); no test or story writes `data/pipeline-repo`. Never work around the trigger.
- From `0038` (phase 7.19, `docs/plans/phase-7.19.md`): a `glossary_import` proposal is one element row of an uploaded glossary file
  (`core/steps/glossary_upload.py`, `POST /api/amaze/glossary/uploads`): made by a person (`origin = 'manual'`, the uploader as
  author, no logged call: no model is called), no feed, decided by the `glossary_owner` alone. Approving it writes version 1 of a
  new key or the key's next version (`GlossaryRepository.add_import()`), never an edit in place; an alias another element holds
  cannot be approved (the database checks it too). Only these guards have a version that knows the kind: `ck_proposals_kind`,
  `ck_proposals_manual_kinds`, `amaze_require_approved_proposal()` and `amaze_require_glossary_option()`; every glossary or
  mapping proposal of another kind still needs a feed. `glossary_uploads` is append-only. A released load adds the elements the
  feed's current mappings gained to the feed's product (`core/steps/product_columns.py`; `product_elements` only grows), and
  `product_page._columns()` shows a feed's product only the elements its current mappings name. Never work around these checks.
- Phase 7.20 (`docs/plans/phase-7.20.md`): `GET /api/amaze/rules/{proposal_or_rule_id}/rows` (`core/steps/rule_rows.py`) is read only,
  with no model call and no migration. A rule proposal that is not approved runs in `core/engine/sandbox.py` on the feed's latest
  fetched file through `draft_config()`, in `draft_rows()`, which takes no session (read, end the transaction, then run); an
  approved rule or a `rules` id reads the newest load's `standard_records` flags, with no SQL run. The answer is a typed result:
  `rows_shown`, `rule_not_found`, `no_file`, `sql_refused`. Page size is cut to `RULE_ROWS_MAX_SIZE`.
- From `0039` (phase 7.21, `docs/plans/phase-7.21.md`): a feed's schedule (`feed_schedules`: one-time or recurring; daily, business
  days, weekly or monthly; expected time, grace, time zone, notify owner) is a person's setting, not a proposal: no model call.
  It is append-only and versioned (the guard needs the previous version plus 1; a change is a new version, never an edit); the
  question "Recurring feed or one-time load?" is required by `POST /api/amaze/uploads/{id}/confirm` and written with the feed.
  `core/steps/deliveries.py` (no model, no clock of its own) matches each expected delivery to its scheduled load, by the schedule
  version in force when it was due, with typed results `deliveries_shown`, `one_time_shown`, `schedule_not_set`; a missing delivery
  tells the owner once (`delivery_alerts`, one row per feed and due time) through the daily job. Deliveries are judged on the load's
  `received_at` (when Amaze took the file in), not on when the source sent it. `amaze_delete_feed()` also deletes the schedules and
  alerts. Never work around these checks.
- From `0028` (phase 7.12, T7, `docs/plans/phase-7.12.md`): a proposal is made by an agent (`origin = 'agent'`, a `proposal_log_id`, no `author`) or by a person (`origin = 'manual'`, an `author`, no `proposal_log_id`). Never write a made-up `proposal_log` row for a manual item: no model was called (rule 4 logs model calls). A person adds only glossary, mapping, transformation and rule items (`add_item()` in `core/steps/onboarding.py`, `POST /api/amaze/onboardings/{id}/items`); a go-live note is an edit of the review summary's `notes`. A manual mapping may name any existing glossary element, and a column may be left out (`excluded`: an approved proposal and no mappings row, so it is not loaded). A manual or edited rule is back-tested by `check_manual_rule()` (the agent's own back-test code) before it is written, with no connection held; a failure is `ManualRuleRefused`, 422. Never work around these checks.
- A glossary builder run is a `glossary_runs` row (one active run per feed), queued in the same transaction as the registration or the steward's request, and run by the worker in `apps/api/jobs.py` with a lease (`GLOSSARY_RUN_LEASE_SECONDS`); an answer that arrives after its lease was taken again is discarded (logged, not used). No agent input holds the whole glossary: each column or term gets at most 10 candidate elements (`core/steps/candidates.py`); a test enforces this.
- Step 02 reads the file only through `BlobStore.get()` and runs DuckDB with no database connection held, like a model call: read, end the transaction, profile, lock, check again, write.
- No database connection is held during a model call. Every agent-backed step follows one pattern: (1) a read-only `*_input()` builds the agent's input and checks the status; (2) the API ends that read transaction (`Agent.run()` refuses a session still in one with `SessionInTransaction`; one-session chains for step tests live in `tests/step_chains.py`, never in `core/`); (3) `Agent.run(..., before_write=...)` calls the model; (4) in `before_write` the step locks the row and checks its status again; (5) only then are the log row, the proposals and the status change written, in one transaction. `Agent.run()` commits the call's `proposal_log` row in its own short transaction right after the call, before the request writes anything; a change during the call gives 409 and a second, append-only row in `proposal_log_discards` pointing at it, with no proposal. Every answered model call has exactly one call row. `tests/test_concurrency.py` checks `pool.checkedout() == 0` during every call. The source fetch (S4) follows the same read / end transaction / call / lock / write pattern.
- Rules 1 and 7 are also enforced by the database: append-only tables refuse UPDATE, DELETE and TRUNCATE, and `mappings`, `rules` and non-seed `glossary_elements` rows need an approved proposal of the matching kind. From `0014`, a sign-off needs its decided `profile_summary` proposal (approved when the person's outcome is the recommended one, rejected when it differs), a registration needs the feed's latest accepting sign-off, and `feeds.stage` changes only candidate → registered (with a registration) or candidate → rejected (with a rejected sign-off); tests make such feeds with `tests/onboarded.py`. From `0016`, a mapping must match its approved proposal (the column, one of its candidates, a registered feed) and a non-seed glossary element must be one of its proposal's options; a feed column has at most one pending glossary or mapping proposal; tests write configuration with `tests/configured.py`. From `0017`, a glossary or mapping proposal has a feed and names its column (a non-blank string), an alias row adds an alias the element does not have yet, and a new glossary run names a profile of its own, registered feed. Never work around these triggers; add a new version instead.
- At the end of each phase, append any UI polish items found to `docs/ui-polish.md` (one line each: screen, issue, proposed fix). Do not fix them unless asked; phase 7.5 does.
- Edit files only with the file tools (Read, Edit, Write). Never rewrite or patch files through PowerShell, sed or other shell commands.
- When an Azure detail is needed, add it to the stub and to `.env.example`, never to business logic.
- Do not add features beyond the current phase in `BUILD_PLAN.md`.

## graphify

If graphify-out/ exists, query it (`graphify query "<question>"`) before broad searches.
