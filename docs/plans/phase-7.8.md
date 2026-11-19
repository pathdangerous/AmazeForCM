# Phase 7.8 plan — One operator, a real model, any file

**Decisions:** D1–D8 of the request (2026-11-19), copied in section 3; not asked again.
**Status:** built by the builder of record, stage by stage; each stage's tasks are written here before it is built.
**Date:** 2026-11-19. **Branch:** `phase-7.8`, created from `main` at `9714268`. **Scope:** the request of 2026-11-19 (stages S1–S10). Local only; nothing is merged, pushed or bundled.

**Method.** Each stage: its tasks written in section 12 → tests first, RED pasted in `docs/plans/phase-7.8-progress.md` → code (a Sonnet subagent per task, its diff reviewed here) → GREEN pasted → one commit per task. The progress file is the resume point.

## 1. Goal and "done when"

**The product:** one operator onboards any file (CSV, XLSX, table PDF) through a guided "save and next" workspace. At each step an agent does the work; the operator reviews, edits if needed, approves. A consumer finds the product, previews it and downloads it. The model is Azure OpenAI through Foundry.

**Phase 7.8 is done when all are true:**
1. `e2e/tests/operator_story.spec.ts` passes three runs in a row on recorded responses (`LLM_PROVIDER=mock`), with the security master, one edited proposal, a preview and a download.
2. `e2e/tests/poc_story.spec.ts` passes three runs in a row with `REQUIRE_FOUR_EYES=true` (D7).
3. `pytest -q -n 6` passes, 0 skipped, `-W error`, live tests excluded; median of three clean runs ≤ 200 s.
4. Every guard changed for D2 has a proof under both settings; no guard removed.
5. The profile of the security master shows every D8 issue (S3 test).
6. S8's live report per agent (cases, passes, mean latency, calls) — or, if the model is unreachable, the reason and the recorded-response build (STOP rule).
7. Both builds exit 0; `npm test` passes in both UIs; bundle sizes reported.

## 2. State at the start (checked on 2026-11-19)
| Item | Found |
|---|---|
| Git | `main` = `9714268`; untracked `.claude/`, `docs/phase6-screenshots/`, `docs/phase7-screenshots/`, `fixtures/synthetic/` (the D8 file) |
| Model config | `.env` read through `ports/config.read_env` (names and set/empty only): `AZURE_OPENAI_ENDPOINT`, `_API_KEY`, `_API_VERSION`, `_DEPLOYMENT`, `_DEPLOYMENT_SMALL` **empty**; `AZURE_OPENAI_AUTH` = `entra`; `LLM_PROVIDER` = `mock`. **The live model is unreachable as configured** (STOP rule: build on recorded responses, report) |
| `.env.example` | denied to the file tools by `.claude/settings.json` (`Read(./.env.*)`); it lacks `AZURE_OPENAI_DEPLOYMENT_SMALL`, `REQUIRE_FOUR_EYES`, `SEND_SAMPLE_VALUES` (checked by a script printing booleans) |
| Packages | no `openai`, `openpyxl`, `pdfplumber` in the venv |
| Guards | approvals: `uq_approvals_proposal_id_decided_by` (0002, same person twice) and `SameApproverTwice` (`core/repositories/proposals.py`); go-live owner-only (`s11_go_live.py`, 0018); `amaze_require_approved_proposal()` (0005) |
| Tests | baseline run recorded in the progress file before S1 |

## 3. What you asked, and where this plan answers it
D1 real model: S1 (tasks 1.x), S8. D2 four-eyes setting: S5 task 5.1 (assumption 9). D3 sample values: S3 (assumption 12). D4 PDF: S2. D5 drop folder: S4/S5 (assumption 15). D6 mastering out: section 14. D7 old story with four-eyes on: S9. D8 security master: S2 commits it, S3 surfaces it.

## 4. Assumptions — veto any by number

**Model (S1)**
1. **SDK `openai==3.45.0`** (2026-11-04; 3.46.0 is 14 days old today, so the older one). Its transitive packages are pinned in `requirements.txt` at versions at least two weeks old (section 17 lists them with dates).
2. **Entra auth is a lazy import** of `azure.identity` (not installed, not pinned): `AZURE_OPENAI_AUTH=entra` without the package is a clear `ConfigError` naming it. Key auth is the built path (D1).
3. **Structured outputs:** `chat.completions` with `response_format` `json_schema` built from the agent's `response_schema` (strict when the schema allows it, else non-strict); the answer is validated again with Pydantic. SDK retries are off (`max_retries=0`): this adapter owns retries.
4. **Errors:** timeout → `LLMUnavailable("timeout")`; HTTP 429 → wait `Retry-After` when ≤ `LLM_RETRY_AFTER_CAP_SECONDS` (default 20) and retry, at most 2 times, else `rate_limit`; other failures → `provider_error`. Content filter (`finish_reason == "content_filter"` or a 400 with code `content_filter`) and a model refusal (`message.refusal`) → new `LLMRefused(reason)`, API 422 with `reason` `content_filter` or `refusal`; nothing written. Malformed JSON or a schema mismatch → one retry; a second → `LLMUnavailable("malformed_output")` (a new reason), nothing written, both raw answers kept in the BlobStore.
5. **Raw responses:** every answer's JSON is stored at `llm-raw/<prompt_id>/<uuid>.json` through the `BlobStore`; `raw_ref` is that key; `model_version` is the response's `model` field; `model` is the deployment name. Nothing of the response is logged.
6. **Small deployment:** prompts `intent` and `discovery_assistant` use `AZURE_OPENAI_DEPLOYMENT_SMALL` when set, else `AZURE_OPENAI_DEPLOYMENT`.
7. **Recorded responses:** hand-made synthetic chat-completion JSON in `fixtures/llm_recorded/azure/`, served by an `httpx2.MockTransport` (no network). Live tests carry `@pytest.mark.live`; `pyproject.toml` adds `-m "not live"` to `addopts`, so `pytest -m live` selects them.
8. **Health:** the adapter is available when endpoint, deployment, API version and (key, or entra) are set; health makes no model call. `LLM_TIMEOUT_SECONDS` (default 60), `MAX_MODEL_CALLS_PER_ONBOARDING` (default 60; enforced from S5, when sessions exist).

**Four-eyes (D2), workspace (S5)** — written in full before S5; headline choices:
9. **A one-row-per-change settings table**, `amaze_settings` (append-only, versioned: rule 7), latest row wins, **no row = four-eyes on** (fail safe). The API writes a row at start-up when `REQUIRE_FOUR_EYES` differs from the latest. Chosen over a session GUC because a GUC can be set by any connection silently and leaves no history; a row is audited and is seen by every connection, the worker and the daily job alike.

**Files, profile, transforms (S2–S4)** — written in full before each stage:
12. `SEND_SAMPLE_VALUES=false`: no value from the file reaches the model; true: at most 5 distinct non-empty values per column (D3).
15. A drop folder per uploaded feed, `data/dropzone/<feed_code>/`; the newest file goes through step 12 as a scheduled delivery (D5).

## 5. Steps and typed results
Written per stage.

## 6. Data model
Written per stage (migrations from `0020`).

## 7. Domain models and repositories
Written per stage.

## 8. Synthetic data and the demo paths
The demo file is `fixtures/synthetic/Synthetic_Security_Master.xlsx` (sheet `SYN_Security_Master`, vendor "Synthetic Market Data Co", `SOURCE` = `SYN`, invented tickers `<6 digits> SY Equity` and names; 70 rows, 30 columns, every D8 issue; the duplicated ticker is `100001 SY Equity`). It replaced the first D8 file, `<a real vendor name>_Style_Security_Master.xlsx`, on 2026-11-19 (section 17, D4).

## 9. API
Written per stage.

## 10. UI
Written before S7.

## 11. Files
Per task, in section 12.

## 12. Tasks, in order
Method for every task: tests first (RED pasted), the least code that passes (GREEN pasted), only the task's files (any other is a deviation in section 17), one commit per task, the progress file updated after it.

### S1 — Real model adapter
| # | Behaviour wanted | Test (RED first) | Files | Out of scope |
|---|---|---|---|---|
| 1.1 | `live` marker; settings `LLM_TIMEOUT_SECONDS` (60), `LLM_RETRY_AFTER_CAP_SECONDS` (20), `MAX_MODEL_CALLS_PER_ONBOARDING` (60) | `test_settings.py`: defaults, env, refusal; `test_live_marker.py`: `-m live` collects only live tests, the default run none | `pyproject.toml`, `ports/config.py`, tests | enforcement of the call cap (S5) |
| 1.2 | Pins (assumption 1; installed with `pip --uploaded-prior-to 2026-11-05`, so every transitive package is ≥ 2 weeks old); `AzureOpenAIProvider` on recorded responses (assumptions 2–8); `LLMRefused`; 503/422 in `main.py`; health available when configured | `tests/test_port_llm_azure.py`: success (schema-valid, raw stored, model/version, latency), small deployment, timeout, 429 within cap retried, 429 over cap, 500, content filter (both forms), refusal, malformed once then good, malformed twice, entra without the package, not configured; `test_api.py` health; API 422/503 | `ports/llm/azure_openai.py`, `ports/llm/base.py`, `ports/llm/__init__.py`, `apps/api/main.py`, `apps/api/routers/health.py`, `fixtures/llm_recorded/azure/*`, tests | prompts (S8) |
| 1.3 | Live smoke: one call per agent's schema against the real model, marked `live` | `tests/live/test_live_azure.py` (collected only with `-m live`) | that file | gold sets (S8) |

Old → new for S1: `tests/test_azure_stubs.py` drops the LLM pair (the adapter is no longer a stub); its "imports no SDK" check is replaced by "only `ports/llm/azure_openai.py` imports `openai`", and its ".env.example lists the keys" check by "README lists the keys" (`.env.example` is denied to the file tools).

### S2 — Direct file intake
Assumptions: **16.** Every accepted file becomes one **canonical CSV** (UTF-8, comma, one header row, ISO dates) stored as the feed's fetched file, so steps 02–20 read CSV as today; the original is kept at `uploads/<upload id>/<name>` and an **intake manifest** (format, encoding, delimiter, sheet, header row, skipped rows with reasons, cell types per column) beside it. **17.** An upload is a **source of its own**: a new `upload_sources` row (code `upl_<feed code>`, name, provider "Uploaded by <operator>", conditions typed by the operator with defaults "Internal use only"), seen by every step as a `SourceEntry` through one combined catalogue (file catalogue + upload sources); the feed is a candidate with connector `upload` (CHECK widened in `0021`), `source_ref` = `dropzone/<feed code>`. No requirement and no consumer request. **18.** The upload body is raw bytes (`application/octet-stream`, `?filename=`), so no `python-multipart` package. **19.** Two calls: `POST /api/amaze/uploads` (store, inspect: format, sheets, detected header, delimiter, encoding, the first 20 rows; typed outcomes `inspected`, `too_large` (413), `unsupported_format`, `not_a_table`) and `POST /api/amaze/uploads/{id}/confirm` (sheet, header row, feed code and name, conditions → `uploaded` with the feed id; `not_a_table`). `UPLOAD_MAX_MB` default 50. **20.** The operator is a dev user `dev-operator` ("Olive Operator") holding every backend role; uploads need `backend_team`. **21.** XLSX: a row is skipped when empty or when it lies in a merged range spanning ≥ 2 columns (titles, banners); cell types come from openpyxl (number, date, text, bool) and are kept in the manifest; numbers keep their full value. **22.** PDF (D4): text PDFs through `pdfplumber` (tables per page; pages with the same header are joined); a PDF with no text layer goes to a **TableExtractorAgent** (A2, approver `backend_team`) that reads page images (rendered by `pypdfium2`, at most 10 pages) through the model's image input; `Message.content` may then be a list of text and image parts; its `extracted_table` proposal is confirmed by the operator before the canonical CSV is written. Free text and forms: `not_a_table`.

| # | Behaviour wanted | Test (RED first) | Files | Out of scope |
|---|---|---|---|---|
| 2.1a | Readers: CSV (encoding: BOM, UTF-8, cp1252; delimiter `, ; \t |`; header row after title or blank rows), the canonical CSV and the manifest; pure code, no database | `tests/test_intake_csv.py` on synthetic files made in the tests | `core/intake/{__init__,model,csv_reader,canonical}.py` | XLSX, PDF |
| 2.1b | XLSX reader (sheets, header row, skips, cell types) into the same model | `tests/test_intake_xlsx.py` on workbooks made in the tests, and the D8 file: sheet `SYN_Security_Master`, header row 1, 70 rows, 30 columns | `core/intake/xlsx_reader.py` | PDF |
| 2.2 | Text-PDF tables (pdfplumber); scanned detection | `tests/test_intake_pdf.py`: a text PDF written by the test (two pages, one header) → one table; an image-only PDF (Pillow) → `needs_image_extraction`; a text page with no table → `not_a_table` | `core/intake/pdf_reader.py` | the agent |
| 2.3 | TableExtractorAgent, image messages, mock rule, `extracted_table` kind | agent tests as `test_agent_*.py`; mock answers from `fixtures/llm/table_extractor/`; Azure adapter sends image parts (recorded) | `agents/table_extractor.py`, prompt v1, registry, `ports/llm/base.py`, `mock*.py`, `azure_openai.py`, `0022` (kind) | |
| 2.4 | Migration `0021`, `upload_sources`, combined catalogue, intake step (`core/steps/s00_intake.py`), API, size limit, dev operator, D8 file committed, 100k-row CSV | `tests/test_s00_intake.py`, `tests/test_api_amaze_upload.py` (each outcome; the feed profiles through step 02 as today), migration test; 100k rows confirmed and profiled in < 20 s | migration, tables, repository, step, router, `fixtures/dev_users.json`, the D8 file | sessions (S5) |
| 2.5 | Upload screen (backend-ui): pick file, see inspection, pick sheet and header row, name the feed, confirm | vitest component checks (stubbed fetch) | `UploadView.tsx`, `App.tsx`, `api.ts` | the workspace (S7) |

### S3 — Profiling
Assumptions: **23.** Code finds every issue; the agent only proposes key columns and words the summary. Per column, from DuckDB on the canonical CSV: `placeholder_count` (trimmed, case-insensitive values `ALL`, `NA`, `N/A`, `-`, empty, whitespace only), `text_date_format` (a text column whose every non-placeholder value parses with one format of a fixed list — `DD-MM-YYYY`, `MM-DD-YYYY`, `DD/MM/YYYY`, `MM/DD/YYYY`, `YYYY-MM-DD`, `YYYYMMDD`, `DD.MM.YYYY`; day/month order decided by a value above 12, else the day-first form, and the ambiguity stated), `all_empty`, `constant`, and `samples` (at most 5 distinct non-empty values, kept with the profile). **24.** The profile gets an `issues` list, each `{kind, column(s), count, detail}` with kinds `duplicate_key`, `placeholder_only`, `placeholders`, `date_as_text`, `all_empty`, `constant`; the security master gives exactly: duplicate_key `TICKER_NAME` (1 extra row, `100001 SY Equity`), placeholder_only `COUNTRY`, `REGION`, `F_PERIOD`, date_as_text `RECORD_DATE` `DD-MM-YYYY`, all_empty `BEST_EPS_1_WEEK_PERCENT_CHANGE`, constant `SOURCE`. **25.** Key columns: the ProfilerAgent (prompt `profiler.v2`) proposes `key_columns`; code then counts duplicates of that key (`after_answer`, as step 09's back-test) and adds the `duplicate_key` issue; the input lists each column's distinct count so the model can choose. **26.** D3: `SEND_SAMPLE_VALUES` (default false) — false: no file value is in any agent's input (a test renders every agent's messages and finds none of the samples); true: at most 5 per column. Stored profiles keep their JSON shape plus the new optional fields (no migration).

| # | Behaviour wanted | Test (RED first) | Files | Out of scope |
|---|---|---|---|---|
| 3.1 | Column facts and issues (assumptions 23, 24) in `profile_csv` and the stored profile | `test_profiling.py`: each fact on small synthetic CSVs; the security master's six issues exactly | `core/steps/profiling.py`, `core/domain/profiling.py`, `s02_profile.py` | the agent |
| 3.2 | Key columns and duplicates (25); D3 samples setting (26); `profiler.v2`; mock rule | `test_agent_profiler.py`, `test_s02_profile.py`, `test_sample_values.py` | `agents/profiler.py`, prompt, registry, `mock_rules.py`, `ports/config.py`, step 02 | other agents' samples (each in its stage) |

### S4 — Transformations and duplicates (step 13's pass-through replaced for transforms; mastering stays out, D6)
Assumptions: **27.** A **TransformationAgent** (A2, approver `data_steward`, prompt `transformation.v1`) proposes, per mapped column, an ordered list from a **fixed safe set**: `trim`, `placeholder_to_null {values}`, `parse_date {format}` (formats of assumption 23 only), `cast {type}` (a glossary data type), `rename {to}` (the column's label in the product; snake case). Its input is the profile facts of each mapped column and its element's type (samples only per D3). One `transformation` proposal per column (new kind, `0023`); a column needs none when nothing applies. **28.** Approved transforms are versioned configuration, `transformations` (append-only; a row needs its approved proposal and matches it: a `0023` guard as `0016`'s mapping guard). The engine reads `TransformationRepository.current_for_feed()` and applies each step as a **fixed SQL template** in the curated stage (`core/engine/transform.py`; no model, no agent SQL; column names quoted), before the cast; go-live pins the transformation ids as it pins mappings and rules. **29.** **Duplicate-key policy** per feed, `feed_key_policies` (append-only, versioned): key columns (from the approved profile) and `hold` (the load is `failed_rolled_back`, reason `duplicate_keys`), `keep_last` (earlier rows of a key are dropped, counted in the stage detail) or `flag` (default: every row of a duplicated key is flagged `duplicate_key`, so the trust score falls). Set by the operator (role `data_steward`) in the transformations step. **30.** D5: connector `dropzone` for uploaded feeds: the daily job and Load now take the newest file in `data/dropzone/<feed code>/` (`DROPZONE_ROOT`), run it through the feed's intake options (sheet, header row) into a canonical CSV, then step 12 as today; no file there: the existing `late_alert`. Re-upload in the UI writes the next file into the same folder.

| # | Behaviour wanted | Test (RED first) | Files | Out of scope |
|---|---|---|---|---|
| 4.1 | `0023`: kind `transformation`, `transformations`, `feed_key_policies`, `feed_go_lives.transformation_ids` (the go-live guard pins the current transformations), guards; repositories | migration and guard tests (`test_db_transformation_guards.py`), repository tests | migration, tables, `core/repositories/transformations.py`, domain | the engine |
| 4.2 | Engine applies approved transforms and the key policy | `test_engine_transform.py`: each template; security master end to end (dates parsed, placeholders NULL, duplicate flagged / kept last / held) | `core/engine/transform.py`, `curate.py`, `run.py`, `passthrough.py` (doc: transforms no longer pass-through; mastering still is), `s11_go_live.py` (pins) | mastering |
| 4.3 | TransformationAgent, step `s10_transform.py` (propose; the steward's decision; edit in S5), mock rule | agent and step tests; no model call at load time (import-boundary test unchanged) | agent, prompt, registry, mock rule, step | |
| 4.4 | Dropzone connector and re-upload (assumption 30) | `test_port_connectors.py`, `test_jobs.py`: the newest of two files is loaded; none → late_alert | `ports/connectors/dropzone.py`, `jobs.py`, intake | |

### S5 — The onboarding workspace (backend)
Assumptions: **31.** `onboarding_sessions` (one per feed: feed, `current_step`, `step_states` JSON, `status` active/done, created by and at, updated at; the row is workflow state, locked before each change, not configuration) and `onboarding_events` (append-only: session, step, event `entered | agent_queued | agent_started | agent_done | agent_failed | pre_accepted | edited | approved | rejected | back | stale | done`, by, at, proposal id, detail) — the audit timeline reads events, proposals, approvals and `proposal_log`. **32.** Steps, in order: `upload, profile, glossary, mapping, transformations, rules, go_live, first_load, product_ready`; step states `waiting | agent_working | needs_decision | ready | done | stale | failed`. **33.** Entering a step queues an `onboarding_jobs` row (one active per session; lease as `glossary_runs`, `ONBOARDING_JOB_LEASE_SECONDS`), run by a worker in `apps/api/jobs.py`, kicked after the commit and at start-up (resume after a restart). The job calls the existing step functions (02 profile + summary; 05 through the existing glossary run; 06 propose; S4 propose; 09 propose; 11 prepare; Load now; step 20's product) — no step logic is copied. **34.** "Approve & next" calls the existing decisions: profile = sign-off with the recommended outcome + registration (owner = the operator, visibility chosen, licence from the upload source); glossary, mapping, transformations, rules = every pending proposal of the step approved (each its own approval rows; four-eyes off: `approve_my_steps`); go-live = the owner's decision; first load = Load now from the drop folder; product ready = the product exists. It is refused (`decision_needed`) while any item needs a decision. **35.** Confidence: each proposal's `confidence` (from the agent's output, or the candidate score for mappings) ≥ `AUTO_ACCEPT_CONFIDENCE` (0.85) is pre-accepted (an event, shown, still approved only by the operator's click); below it, or a tie, needs a decision (accept, edit or reject). **36.** **Edit before approval** (the phase 5 no-edit rule changes, with proofs): an edit writes a new proposal row (same `proposal_log_id`, `edited_from`, `edited_by`, the edited payload) and moves the old one to a new status `superseded`, in one transaction; the "one pending proposal per column" guard counts `pending` only, so it still holds; every configuration guard (mapping matches its proposal, element is one of its options) applies to the edited row as to any; a superseded proposal can never be approved (guard). The log row keeps what the model said. **37.** Back: marks every later step `stale`; entering a stale step reruns its agent (new proposals; earlier configuration stays versioned). Back is refused after go-live is approved. **38.** `MAX_MODEL_CALLS_PER_ONBOARDING`: a job that would pass the cap is not run (`call_cap_reached`, an event and a notice). **39.** Migration `0024`.

| # | Behaviour wanted | Test (RED first) | Files | Out of scope |
|---|---|---|---|---|
| 5.1 | D2: `amaze_settings`, `amaze_four_eyes()`, the approvals guard honours it, `approve_my_steps`, start-up sync, dev operator (assumption 9) | `test_four_eyes.py`: both settings at database and repository level; owner-only go-live under both; the decision endpoint | `0020`, `settings.py`, `proposals.py`, `main.py`, `amaze.py`, `dev_users.json` | UI |
| 5.2 | `0024`: sessions, events, jobs; `superseded`, `edited_from`, `edited_by`; guards and proofs (assumption 36) | `test_db_onboarding_session_guards.py`, `test_proposal_edit.py` | migration, tables, repositories | |
| 5.3 | `core/steps/onboarding.py`: enter, queue, run a step's job, approve & next, back and stale, pre-accept, the call cap | `test_onboarding.py`: the security master through every step on the mock, with one edit and one back | step module | API |
| 5.4 | API (`/api/amaze/onboardings` list, create from an upload, get, enter, approve, back, edit a proposal, timeline) and the worker; resume after a restart | `test_api_onboarding.py`; a commit-visibility test per committing endpoint; restart test (a queued job taken at start-up) | `routers/onboarding.py`, `jobs.py`, `main.py` | UI (S7) |

### S6 — Glossary for the security master
Assumptions: **40.** The seed glossary has 33 elements and no `unit`; `0025` adds `unit text NULL` to glossary elements and to the builder's element option (the guard "an element is one of its proposal's options" compares the unit too). **41.** The GlossaryBuilderAgent (prompt `glossary_builder.v2`) proposes, per new column, a new element (key, name, domain, data type, definition, unit) or an alias of one of its ≤ 10 candidates; the mock rule makes the D8 elements deterministically from the column names and profile facts (identifiers → alias of `ticker` for TICKER_NAME, `instrument_name` for SHORTNAME; SECTOR, COUNTRY, REGION, F_PERIOD, RECORD_DATE, SOURCE and the 22 `BEST_*` estimates as new elements in an `estimates` domain). **42.** Seeded by hand, only what the builder cannot know: `fixtures/glossary/units.json`, the unit of each ratio or percentage estimate (`BEST_PE_RATIO`, `BEST_PX_BPS_RATIO`, `BEST_EV_TO_BEST_EBITDA` → `times`; `BEST_DIV_YLD`, `BEST_ROE`, `BEST_GROSS_MARGIN`, the four `*_PERCENT_CHANGE` → `percent`; amounts such as `BEST_SALES` or `BEST_OPP` get no seeded unit: their currency is not in the file, and the builder says so); code fills an option's empty unit from it and marks `unit_source: seed`; section 17 lists exactly what was seeded.

| # | Behaviour wanted | Test (RED first) | Files | Out of scope |
|---|---|---|---|---|
| 6.1 | `0025` unit; builder v2 with units; mock rule for the D8 columns; the unit seed | `test_agent_glossary_builder.py`: the D8 run proposes every column (aliases and new elements with names, definitions, types, units); the seeded units applied and marked; guards with units | migration, domain, agent, prompt, registry, mock rule, `units.json`, step 05 | UI |

### S7 — UX redesign (both apps; Mantine, the shared `theme.ts`)
Assumptions: **43.** Read-only consumer API for products: `GET /api/consumer/catalogue` (products by domain, the ones the user may see or is shown with the route to access, as step 17), `GET /api/consumer/products/{code}` (description, owner's name, freshness = the latest released load's time, the data dictionary = each element's name, definition, type, unit and label), `GET .../preview` (the first 100 standard records of the latest released load), `GET .../download.csv` and `.../download.xlsx` (all records of that load; XLSX written with openpyxl, typed cells). Entitlement is `core/entitlement/decide.py` (rules, no model): not entitled → 403 with the route to access; preview and downloads write nothing. **44.** backend-ui: home "Onboardings" (`#/onboardings`, the default view) lists sessions with step and status and a primary "Onboard a file" button (`#/onboard`, the Upload step); the workspace `#/onboarding/<id>`: left stepper (nine steps, a state badge each, arrow keys move focus, Enter opens a done or current step), main panel per step, right panel "What the agent did and why" (reasoning summary, confidence, inputs it saw: columns, issues, candidate counts — never sample values unless `SEND_SAMPLE_VALUES`), sticky footer Back / Approve & next (disabled with the reason while items need a decision), a summary page before go-live, every step with empty, loading (agent working, progress text, polled every 2 s while a job is active), error and done states. Old pages stay under "Feeds" (the registry and feed page) and are what the four-eyes flow uses. **45.** consumer-ui: Search → product page → preview table and the two downloads; "Catalogue" by domain; "My requests" as a tracker (steps of the requester's journey with the current one marked, as phase 7.5's progress steps). **46.** Accessibility: keyboard through the stepper (vitest), a label on every control (vitest queries by label), the existing 4.5:1 contrast test; `theme.ts` stays byte-identical in both apps. Bundle budget: report gz sizes; flag if either app grows more than 60 KB gz over 7.5's final.

| # | Behaviour wanted | Test (RED first) | Files | Out of scope |
|---|---|---|---|---|
| 7.1 | Consumer product API (43) | `test_api_consumer_products.py`: catalogue, page, preview 100, CSV and XLSX contents, 403 with route, a product of a held load | `routers/consumer.py`, `core/steps/product_page.py` (new) | UI |
| 7.2 | backend-ui home, workspace shell, stepper, right panel, footer, polling, upload step (44) | vitest: list, button, stepper keyboard, footer disabled reason, panel content, states | `OnboardingsView.tsx`, `WorkspaceView.tsx`, `Stepper.tsx`, `AgentPanel.tsx`, `App.tsx`, `api.ts` | step panels |
| 7.3 | Step panels: profile (issues, key, summary), glossary, mapping, transformations (+ key policy), rules, go-live summary page, first load, product ready; decide and edit an item | vitest per panel: items, decide, edit form, needs-decision marking | `views/steps/*.tsx` | |
| 7.4 | consumer-ui: catalogue, product page, preview, downloads, My requests tracker (45) | vitest per view | consumer `views/*`, `App.tsx`, `api.ts` | |

## 13. Commands that prove it is done
| Command (from `F:\amaze-cm-poc`) | Expected |
|---|---|
| `.\.venv\Scripts\python -m pytest -q -n 6`, three clean runs | all pass, 0 skipped; median ≤ 200 s |
| `.\.venv\Scripts\python -m pytest -m live` | live report (needs the model configured) |
| `cd e2e; npx playwright test tests/operator_story.spec.ts` ×3; `poc_story.spec.ts` ×3 with `REQUIRE_FOUR_EYES=true` | `1 passed` each |
| both UIs: `npm run build`, `npm test` | exit 0 |

## 14. Not in phase 7.8
Mastering and the golden record (D6). Free-text and form PDFs (D4). Merging, pushing, bundling.

## 15. Risks
1. The live model is unconfigured at the start: S8 may have no live numbers. 2. S5 and S7 are the largest stages; their plans are split into small tasks.

## 16. Carried forward
Filled at the end.

## 17. Results and deviations (recorded during implementation)
**S6, what was seeded by hand (assumption 42):** `fixtures/glossary/units.json` only, by element key: `times` for `best_pe_ratio`, `best_px_bps_ratio`, `best_ev_to_best_ebitda`; `percent` for `best_div_yld`, `best_roe`, `best_gross_margin`, `best_eps_4_week_percent_change`, `best_eps_6_month_percent_change`, `best_eps_3_month_percent_change`, `best_eps_1_week_percent_change`. Currency amounts get no unit (their currency is not in the file). Everything else is proposed by the GlossaryBuilderAgent (v2) and decided by the operator; the mock's rule makes the D8 columns' elements by name (SHORTNAME is a new element `short_name`: the candidate scoring offers no candidate for it; SOURCE a new `data_source`, not an alias of `price_source`).
**Deviations:** D4 (developer's instruction B1, 2026-11-19) the demo file replaced: `<a real vendor name>_Style_Security_Master.xlsx` (a real vendor's name in the file name and `SOURCE` = `BBG`; tickers and names like real listed companies; a person's name as the workbook author) → `Synthetic_Security_Master.xlsx`, derived from it by a one-off script: `SOURCE` → `SYN`, every ticker and company name invented, sheet `SYN_Security_Master`, author "Synthetic Market Data Co"; every number, empty cell, text date and placeholder kept, so every D8 issue is the same (checked: the seven profile issues and the duplicate `100001 SY Equity`). Tests and this plan now name the new file and values; the progress file's earlier rows keep the old values as history. D3 order: tasks with no shared files ran in parallel (separate schemas `w_b2`, `w_b3`; the builder ran the full suite after each pair): 3.2 ∥ 4.1, 4.2 ∥ 4.4, 4.3 ∥ 5.2, 5.3 ∥ 7.1 (7.1, the read-only consumer product API, before S6). D2 S2 task order: 2.1 split into 2.1a (CSV, before the packages arrived) and 2.1b (XLSX); 2.3 (TableExtractorAgent) is built after 2.4, because it confirms its table through 2.4's intake step. D1 task 5.1 (D2, the four-eyes setting) was built before task 1.2: PyPI timed out for over 30 minutes, which blocked S1's SDK install, and 5.1 needs no package and touches no S1/S2 file. Migration numbers moved: `0020` four-eyes, `0021` intake (S2), `0023` transformations (S4).
