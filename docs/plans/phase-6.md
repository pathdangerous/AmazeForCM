# Phase 6 plan — Rules, go-live approval and the run engine (steps 09, 11, 13)

**Decisions (2026-11-17):** every assumption approved as written; 15, 16 and 30 accepted by name; risk 4 accepted for phase 6 (no start-up sweep; phase 7 note 3); the test-time ceiling is **195 s per full run**, not 213 s (assumption 35, done-when 8, section 13); task 3 gains a row-count proof.
**Status:** approved 2026-11-17 with the decisions above.
**Date:** 2026-11-17. **Branch:** `phase-6`, created from `main` at `ea233a1`. **Scope:** phase 6 of `BUILD_PLAN.md`, phase 5 plan section 16 (notes 1 to 5 and 12), phase 4 plan section 16 (notes 3 and 4), and four items of the planning request (the `run_soon` early fire, the stub-scheduler status, load history fixtures, test time). Local only; nothing is pushed.

**Lean mode.** Two checkpoints: (1) after the migration task (task 5), (2) at the end. Guard proofs for new guards only. Race tests only where two writers really meet (assumption 33). Every task names its behaviour, test, files, added seconds and what is out of scope (section 12).

## 1. Goal and "done when"

**Phase 6 is done when all eight are true:**

1. **An approved feed loads end to end from configuration alone** (`BUILD_PLAN.md`): `test_api_amaze_go_live.py::test_done_when_an_approved_feed_loads_end_to_end_from_configuration_alone`. Through the API: mappings and rules approved, go-live approved by the feed's owner; then `DailyFetch.run()` (no person, no request) fetches the file and the engine loads it: four `stage_completed` stages, one standard record per file row, the unmapped column absent, a broken rule's rows flagged.
2. Step 09: the RuleRecommenderAgent proposes SQL rules; each is back-tested on the synthetic history; the steward approves, then the checker; only then a `rules` row exists. Outcomes `rules_proposed`, `flags_most_history_returned`.
3. Step 11: the ReviewSummariserAgent drafts the go-live summary after a test load; the feed's named owner approves; the feed is loaded by the daily job from then on; hypercare is set. Outcomes `approved_scheduled`, `rejected_back_to_step`, `test_failed_held`.
4. Step 13: raw → curated → validated → standard model, each stage a row; a failed file leaves no standard record (`failed_rolled_back`). No model call: `test_import_boundaries.py` covers `core/engine/`.
5. Agent-proposed SQL runs only in a locked-down DuckDB (no file or network access beyond the one file): guard proof in section 6.
6. Each of the seven outcome names has a named test (section 5.3); each committing endpoint and job has one commit-visibility test; the three race pairs of assumption 33 have tests.
7. `0018`'s guards each have a raw-SQL test shown failing without the guard.
8. `pytest` passes with nothing skipped and no warnings, in three back-to-back runs, each at or below 195 s (assumption 35).

## 2. State at the start (checked on 2026-11-17)

| Item | Found |
|---|---|
| Git | `main` = `ea233a1`; branches `phase-0` to `phase-5` exist; `phase-6` created from `main`. Untracked: `.claude/`, `.env.main` (not mine, not touched) |
| Alembic | `amaze` `0017`, **`amaze_eval` `0017`** (phase 5 section 17 said it stayed at `0016`; it has been upgraded since), `amaze_test` `0017` (read from `alembic_version` in each) |
| Tests | Two full runs today: **`1730 passed in 172.86s`** and **`1730 passed in 162.01s`**. The request's `1 failed, 1729 passed in 213 s` did not come back in either run |
| `run_soon` early fire | **Reproduced, intermittent.** The delay test alone, 10 runs: `1 failed, 9 passed`; a later loop failed on run 10 with `assert (True and (417568.8913623 - 417568.4928218) >= 0.4)`: fired at **0.3985 s** for 0.4 s. 40 direct `threading.Timer(0.4)` measurements in a scratch script: 0 early. The early fire depends on the Windows timer resolution at that moment |
| Slowest tests (`--durations=40`) | No test above 3.4 s. Schema-per-test cases: 56 (`test_concurrency.py`, `test_commit_visibility.py`); their setup costs 0.6 to 1.3 s each. Two fixed waits: `test_health_reports_unreachable_database_with_503` 2.0 s (libpq's 2 s minimum `connect_timeout`), `test_http_client_is_closed_after_a_failed_fetch[None]` 2.17 s (`timeout_s=2`) |
| Schema build | Scratch measurement on `amaze_test`: `alembic upgrade head` into a new schema **0.40 s**; replaying the offline script (`alembic upgrade head --sql`, 1685 lines) **0.15 s**; both give 26 tables, 68 triggers, 341 constraints, 58 indexes |
| Steps 02–04 by hand, empty schema | **Proved through the real API** (uvicorn on a fresh schema `phase6_api_proof` of `amaze_test`, migrated to `0017`, glossary seeded, scratch blob root; schema dropped after). Riley: `POST /api/consumer/requests` 201, `find-source` 200 (known source `syn_exchange_volumes`); Blake: `profile` 200 `profiled`, `profile/summary` 200, `signoff` 200 `meets`, `register` 200 `registered`; the glossary run `done`, results `{}`. API log: 11 × 200/201, no 4xx or 5xx |
| Rows that proof left | `consumer_requests` 1, `requirements` 1 (`onboarding`), `requirement_elements` 1, `source_choices` 1, `source_fetches` 1 (`fetched`, 294 bytes), `feeds` 1 (`registered`, owner `dev-data-owner`), `feed_profiles` 1 (v1, `profiled`, 10 rows), `proposal_log` 1 (ProfilerAgent), `proposals` 1 (`profile_summary`, approved), `approvals` 1 (`backend_team`), `feed_signoffs` 1 (`meets`), `feed_registrations` 1, `glossary_runs` 1 (`registered`, `done`), `notifications` 3 (new requirement, file ready, feed registered), `glossary_elements` 33 (seed). Every other table 0 |
| Application database | `loads` 0, `rules` 0, `mappings` 10; feeds: 4 registered, 2 rejected, 2 candidates; no `rule` or `review_summary` proposal |
| Already in place | `rules` (0004) with `0005`'s approved-proposal guard; `RuleRepository` (`add_version`, `current_for_feed`, `history`); `loads` (received, held, failed_rolled_back, released), not append-only; proposal kinds `rule`, `review_summary`; roles `checker`, `operations`; dev users Casey Checker, Opal Operations; `core/engine/` empty; DuckDB 1.5.6 used in `core/steps/profiling.py` only |
| DuckDB lockdown | Scratch probe on 1.5.6: after loading the file into a memory table, `SET enable_external_access = false` then `SET lock_configuration = true` refuse reading any file (the same one included), `COPY`, `ATTACH`, `INSTALL`, `LOAD`, `http://`, `glob`, and setting the option back; `getenv` does not exist. `PRAGMA` and `CREATE TABLE` in memory still run, so code also checks the statement type (`duckdb.extract_statements`: `SELECT`) |

## 3. What you fixed, and where this plan answers it

| Carried item | Answered in |
|---|---|
| Phase 5 note 1: the engine reads only `MappingRepository.current_for_feed()`; an unmapped column is not loaded | assumption 15; task 7 |
| Note 2: a column whose type does not fit: one behaviour, tested | assumption 16 (the file is held: `failed_rolled_back`); task 7 |
| Note 3: the rule agent gets candidates and a small input | assumption 7; task 8 |
| Note 4: the go-live summary lists unmapped columns and rejected glossary proposals | assumption 23; task 9 |
| Note 5: `rules` rows match their approved proposal | assumption 32, guard G5; task 5 |
| Note 12: test time, per task, held at or below 195 s | assumption 35; task 3 and section 12 |
| Phase 4 note 3: DuckDB locked down before agent SQL runs | assumption 6, guard proof S1; task 6 |
| Phase 4 note 4: the owner's first decision; the owner no longer a data owner | assumption 24 |
| Stub-scheduler status pinned | assumption 30; task 2 |
| `run_soon` early fire | assumption 29; task 1 |
| Load history fixtures | assumption 8; section 8 |

## 4. Assumptions — veto any by number

**Shape and roles**

1. **One part, two checkpoints.** Checkpoint 1 after task 5 (migration and repositories); checkpoint 2 at the end.
2. **No new feed stage.** Go-live is a versioned, append-only `feed_go_lives` row; the feed stays `registered`. "Job scheduled" means the one daily job (`apps/api/jobs.py`) now loads the feed after each fetch: nothing per feed is kept in the scheduler (phase 3 pattern). Alternative: a `live` stage, which changes every phase 5 "registered" check and `trg_feeds_a_stage`.
3. **Who acts.** `data_steward` asks for rule proposals and decides step 1; `checker` decides step 2 (`approver_roles = ("data_steward", "checker")`); `backend_team` asks for the go-live summary; the feed's registered owner decides go-live; `operations` may "Load now". Everyone else reads; 403 on actions.
4. **Agents are passed in.** `core/steps/s09_rules.py` and `s11_go_live.py` receive their agent as an argument; `core/engine/` imports neither `agents` nor `ports.llm` (existing boundary test).

**Step 09: rules**

5. **A rule is one SQL `SELECT` over the table `curated`** (one column per mapped element key, plus `_row`, the file's data row number from 1) **that returns the `_row` of every row that breaks it.** Code checks: exactly one statement, of type `SELECT` (`duckdb.extract_statements`); the result has a `_row` column. Anything else is refused by code with its reason (not proposed).
6. **DuckDB sandbox (phase 4 note 3)**, `core/engine/sandbox.py`: a new in-memory connection with `autoinstall_known_extensions` and `autoload_known_extensions` off; the one file is read into memory first (`read_csv(..., all_varchar = true)`), then `enable_external_access = false` and `lock_configuration = true`; only then does any rule run. Each rule has a time limit, `RULE_TIMEOUT_SECONDS` (default 5), enforced by `connection.interrupt()` from a timer. Used by the back-test and the engine.
7. **The agent's input is small (note 3).** Per mapped element: key, data type, null rate and distinct count from the latest profile, and **at most 5 rule templates** that fit its type (`core/steps/rule_candidates.py`: `present`, `not_negative`, `in_whole_lots`, `not_in_future`, `one_row_per_key`). Also the keys (not the SQL) of the feed's current and pending rules, at most 20. No glossary, no other feed's rules, no values. Test: `test_the_rule_input_holds_candidates_not_the_glossary_or_every_rule`.
8. **History for the back-test.** A catalogue entry gains an optional `history` list of file references (synthetic past deliveries, section 8), read through the feed's own `SourceConnector` after the read transaction ends. Each delivery is curated by the engine's own `curate()`; one that fails curation is excluded and listed. A feed with no history: rules are proposed as "not back-tested" (shown so in the UI and the summary). The feed's own past loads join the history in phase 7 (section 16).
9. **The back-test runs with no connection held.** `Agent.run()` gains one optional argument, `after_answer(output) -> output`: called after the call row is committed and before `before_write`, with the session not in a transaction (checked), it returns a copy of the output with code-filled fields (the back-test). Its failure is a discard, like `before_write`'s. This is the only change to `agents/base.py`.
10. **Outcome per call.** Per rule, code keeps it, refuses it (assumption 5, an unknown column, an error or a timeout in the back-test), or returns it: **flagged rows / curated history rows > 0.5 is returned** (no proposal). Result: **`rules_proposed`** when at least one proposal is made (the returned and refused rules listed with it); **`flags_most_history_returned`** when none is made and at least one was returned. No rule left at all: `rules_proposed` with no proposal and the refused list.
11. **One `rule` proposal per kept rule**, payload: rule key, SQL, description, element keys, template, back-test (per delivery: label, rows, flagged; total share). A key that is current, pending or was rejected for the feed is not proposed again. One pending proposal per feed and rule key (index, G2).
12. **The decision.** Steward approves (proposal still pending, the checker is told) or rejects with a note; checker approves (the `rules` row, version 1, in the same transaction) or rejects with a note. Same person twice: refused (existing `SameApproverTwice`). No editing. A rule version 2 is not in phase 6.
13. **Mock rule `rule_recommender`** (pure): one rule per template offered, SQL from the template. On the exchange volumes history it gives `instrument_id_present`, `price_date_present`, `volume_not_negative`, `one_row_per_instrument_id_and_price_date` (proposed) and `volume_in_whole_lots` (returned: most history volumes are not multiples of 100).

**Step 13: the run engine (no AI)**

14. **Stages.** `raw`: the stored file read through `BlobStore.get()`, row count. `curated`: mapped columns only, renamed to element keys, cast to the element's type (string VARCHAR, integer BIGINT, decimal DECIMAL(38,10), date DATE, datetime TIMESTAMP, boolean BOOLEAN). `validated`: each current rule run in the sandbox; a rule's `_row`s are flagged, the stage completes with the counts per rule. `standard`: one `standard_records` row per curated row. Each stage is a `load_stages` row.
15. **Note 1.** The engine reads `MappingRepository.current_for_feed()` and `RuleRepository.current_for_feed()` and nothing else for its configuration; an unmapped column is dropped at `curated` and named in the stage's detail. **If those ids differ from the ids the latest go-live approved, the load fails at `raw`** (`configuration_changed_since_go_live`) and the owner and backend team are told: a configuration change reaches loads only through a new go-live approval (rule 1). Alternative: the engine runs the pinned versions, which is not "current_for_feed only".
16. **Note 2: a value that does not cast holds the file.** Any non-empty value of a mapped column that does not cast (`TRY_CAST` is NULL) fails `curated`: `failed_rolled_back`, with the count and the first 5 (row, column, value). Not chosen: rejecting the row (silent data loss) or nulling it (silent change). An empty cell is NULL, as in step 02.
17. **Rules flag, they do not fail.** A rule that flags rows does not fail the load; phase 7's certification decides release. A rule whose SQL errors or times out at load time fails `validated`.
18. **Pass-through, clearly marked.** No mastering, survivorship, deduplication or transformation beyond the cast. `core/engine/passthrough.py` says so in its docstring, and every `standard` stage's detail holds `{"mastering": "pass_through", "transformations": "none"}`. Values are stored as JSON (decimals as exact strings, dates ISO).
19. **One pattern, no connection during DuckDB:** `plan_load()` reads (load, feed, go-live, configuration, element types); the caller ends the transaction; `execute_load()` uses `BlobStore.get()` and the sandbox only; `record_load()` locks the feed row, then the load row, checks the load is still `received`, and writes stages, records and status in one transaction. If that write fails it is rolled back and a new transaction writes the completed stages, a failed `standard` stage and `failed_rolled_back`. No standard record of a failed load survives.
20. **Load rows.** `loads` gains `purpose` (`go_live_test` or `scheduled`), `fetch_id`, `go_live_id` (a scheduled load's), `finished_at`, `failed_stage`, `failure`, and the status `loaded`. **One `received` load per feed** (index); a second is 409 `LoadInProgress`. A load left `received` by a process that died stays so; nothing retries it (risk 4).

**Step 11: go-live**

21. **Prepare** (`POST .../go-live/summary`, backend team). Refused with 409, nothing written: the feed is not registered; no current mapping (`NothingToGoLive`); a `review_summary` is pending (`GoLiveAlreadyProposed`); no fetched file; the owner no longer holds `data_owner` (assumption 24). Then a **test load** of the feed's latest fetched file runs through the engine (its own commits).
22. **`test_failed_held`:** the test load ends `failed_rolled_back`. No model call, no proposal; the backend team and the owner are told the stage and reason. The response is 200 with that outcome.
23. **The summary input (note 4)** after a `loaded` test load: feed, source, registration (owner, licence, visibility); per mapped column its element and version; **the unmapped profile columns**; **the rejected glossary proposals** of the feed (column, note); the current rules with back-test figures; the test load's stages and flag counts; the counts of pending glossary, mapping and rule proposals. `ReviewSummariserAgent` (A1, `approver_roles = ("data_owner",)`) writes the summary, highlights and risks. `before_write`: feed row locked; the configuration ids unchanged and no pending summary, otherwise 409 `ConfigurationChangedMeanwhile` and a discard row. The proposal pins the mapping ids, rule ids and test load id.
24. **The owner's decision (phase 4 note 4).** Only the owner named in the feed's registration decides; another data owner gets 403 `NotTheFeedsOwner`. If the named owner no longer holds `data_owner`: prepare is 409 `OwnerNotADataOwner` (backend team told), and a pending summary's decision is 403 `NotAnApprover`. **Changing a feed's owner is not in phase 6** (a registration is written once, `0015`); such a feed waits.
25. **Approve** (`approved_scheduled`): under the feed lock, the pinned ids must still be current (else 409 `ConfigurationChangedSinceSummary`; the owner can reject instead). Writes the approval and the `feed_go_lives` row (next version, `hypercare_until` = today + `HYPERCARE_DAYS`, default 10). The backend team and operations are told; the result names the daily run time (06:00 UTC).
26. **Reject** (`rejected_back_to_step`): a note and `back_to_step` (`05`, `06` or `09`) are required (422). The step's role is told (05 glossary owner, 06 and 09 data stewards). Nothing else changes.
27. **Hypercare.** While today ≤ `hypercare_until`, each scheduled load's result goes to the owner and operations; afterwards only failures, to operations.
28. **Loading.** After the daily job's `fetched` outcome for a feed with a go-live, it creates a `scheduled` load and runs it (each feed in its own sessions, as today). `source_down`: no load. `POST .../loads` (operations) does the same for one feed now. The requester sees nothing new (phase 7 says "Ready").

**Carried fixes, data, tests**

29. **`run_soon` early fire.** The delayed job records `due = time.monotonic() + delay_seconds`; on firing it sleeps until `time.monotonic() >= due` (a loop), then runs. The strict test is not changed.
30. **Stub scheduler: 200.** The post-commit kick is wrapped: any exception (the stub's `NotImplementedError` included) is logged and swallowed; the response is 200 with the committed result; the row is taken by the next kick or start-up. `test_api_amaze_onboarding.py`'s `in (200, 503)` becomes `== 200`, and the test then drains the worker on the test engine and sees the run `done`. Alternative: 503 always, which tells a client a committed write failed.
31. **One migration, `0018_rules_go_live_and_loads.py`**, developed on the test database; applied to `amaze` and `amaze_eval` at checkpoint 2. Any later fix is `0019`. The application database has no rows the guards would refuse (`loads` 0, `rules` 0, no `rule` or `review_summary` proposal); the builder counts again before applying.
32. **Guards** (section 6): G1 to G11, each with a raw-SQL test and a removal proof.
33. **Race pairs, three** (each on its own schema, the second started by an event inside the first): R1 two rule proposals of one feed at once (two stewards, or a double click): 200 and 409. R2 the owner's go-live approval and the checker's rule approval of one feed at once: both lock the feed row; never a go-live that pins a rule set other than the current one at its commit. R3 the daily job's load and operations' Load now of one feed: one load; the other 409 `LoadInProgress` (the job logs it). Not raced: two decisions of one proposal (phase 5's `record_decision` lock, same code).
34. **Commit visibility, six, happy path, through the API or job:** rules propose; the checker's decision; go-live summary; the owner's decision; Load now; the daily job's load. Plus the two new calls in `test_no_connection_is_held_during_any_model_call`, and one new `test_no_connection_is_held_during_a_load_run` (`pool.checkedout() == 0` in `BlobStore.get()` and in the sandbox).
35. **Test time (note 12).** Today 162 to 173 s (section 2). Added: 10 schema-per-test cases at about 2.5 s (25 s) and about 170 ordinary tests (about 10 s): +35 s. Cut first (task 3): `own_schema` replays the offline DDL generated once per session (0.40 → 0.15 s per schema, measured: about 17 s over 66 schemas); the HTTP failed-fetch test at `timeout_s=0.5` (about 1.5 s); the live-lease restart test with a 1 s lease and 0.2 s grace (about 1.8 s). **Expected: 175 to 195 s; ceiling 195 s per full run.** A run above 195 s stops the builder, who reports it with `pytest --durations=20`. Back to 160 s needs parallel runs (a schema per pytest-xdist worker), not in phase 6 (section 14): your decision.
36. **Lock order, extended at its end:** source lock, feed row, proposal row, requirements by id, `glossary_runs` row, glossary lock, **load row**. The next rule or go-live version is read after the feed row is locked.
37. **Synthetic data only**; every new file and code starts with `syn_` or sits under `history/`.
38. **Documents** (task 14): `CLAUDE.md` (layout of `core/engine/`, the three steps, lock order, the `0018` guards, the sandbox rule), `BUILD_PLAN.md` (phase 6 as built), `README.md` (`0018 (head)`, the demo rows), `.env.example` (`RULE_TIMEOUT_SECONDS`, `HYPERCARE_DAYS`).
39. **UI** checked by both builds and a scripted browser run (rows U to Y). The committed Playwright test stays in phase 7.

## 5. Steps and typed results

### 5.1 The shapes

| Module | Result (`outcome`) | Fields |
|---|---|---|
| `core/steps/s09_rules.py` | `RulesProposed` (`rules_proposed`) | proposal_ids, returned, refused (`ReturnedRule`: key, SQL, share, per delivery) |
| | `FlagsMostHistoryReturned` (`flags_most_history_returned`) | returned, refused |
| `core/steps/s11_go_live.py` | `ApprovedScheduled` (`approved_scheduled`) / `RejectedBackToStep` (`rejected_back_to_step`) / `TestFailedHeld` (`test_failed_held`) | go_live, hypercare_until, daily_run_utc / back_to_step (`05`, `06`, `09`), note / load_id, stage, reason |
| `core/engine/run.py` | `StageCompleted` (`stage_completed`) / `FailedRolledBack` (`failed_rolled_back`) | stage, rows_in, rows_out, detail / stage, reason |

Not outcomes: `GoLiveSummaryProposed`, `RuleAwaitsChecker`, `RuleStored`, a rule rejection. `test_step_results.py` reads "09 Validation", "11 Approve", "13 Run" from `CLAUDE.md` and compares them with the unions in order.

### 5.2 Rule 1: who decides what

| Step | Decides | Approvals | Allows, and the database guard |
|---|---|---|---|
| 09 | `data_steward`, then `checker` | two rows, in that order | a `rules` row: `0005`, and G5 (matches its proposal) |
| 11 | the feed's registered owner (`data_owner`) | one row | a `feed_go_lives` row: G6 (an approved summary of the feed, decided by the owner, pinning the current configuration and a loaded test load) |
| 13 | nobody: rules execute | none | G8 to G11 (statuses, stages, records) |

### 5.3 Every outcome name and its test

| Outcome | Named test |
|---|---|
| `rules_proposed` | `test_s09_rules.py::test_rules_proposed_are_back_tested_and_wait_for_the_steward` |
| `flags_most_history_returned` | `test_s09_rules.py::test_a_rule_that_flags_most_history_is_returned_not_proposed` |
| `approved_scheduled` | `test_s11_go_live.py::test_the_owners_approval_writes_the_go_live_and_sets_hypercare` |
| `rejected_back_to_step` | `test_s11_go_live.py::test_a_rejection_names_the_step_and_tells_its_role` |
| `test_failed_held` | `test_s11_go_live.py::test_a_failed_test_load_holds_the_feed_and_calls_no_model` |
| `stage_completed` | `test_engine_run.py::test_each_stage_completes_and_is_tracked` |
| `failed_rolled_back` | `test_engine_run.py::test_a_value_that_does_not_cast_fails_the_file_and_leaves_no_record` (note 2), `test_a_failed_write_is_rolled_back` |

Other named tests: note 1 `test_engine_run.py::test_only_current_mappings_are_loaded_and_an_unmapped_column_is_dropped`, `test_a_configuration_changed_since_go_live_fails_at_raw`; note 4 `test_s11_go_live.py::test_the_summary_lists_unmapped_columns_and_rejected_glossary_proposals`; phase 4 note 4 `test_only_the_named_owner_decides`, `test_an_owner_without_data_owner_is_409_at_prepare`; hypercare `test_jobs.py::test_under_hypercare_every_load_is_reported`.

## 6. Data model

**Migration `0018_rules_go_live_and_loads.py`**

| Table | Change |
|---|---|
| `feed_go_lives` (new) | feed_id, version, proposal_id (unique), approved_by, mapping_ids uuid[], rule_ids uuid[], test_load_id, hypercare_until date. Append-only |
| `load_stages` (new) | load_id, stage (`raw`, `curated`, `validated`, `standard`), outcome (`stage_completed`, `failed_rolled_back`), rows_in, rows_out, detail jsonb, started_at, finished_at; unique (load_id, stage). Append-only |
| `standard_records` (new) | load_id, feed_id, row_number, values jsonb (element key → value), flags text[] (rule keys). Append-only; index on load_id |
| `loads` | `purpose`, `fetch_id`, `go_live_id`, `finished_at`, `failed_stage`, `failure`; status `loaded` added |
| `proposals`, `rules` | the guards below |

| # | Guard | Refuses |
|---|---|---|
| G1 | `ck_proposals_names_a_rule` | a `rule` proposal with no feed, or whose payload lacks a non-blank string `rule_key` or `sql` |
| G2 | `uq_proposals_one_pending_rule_per_feed_key`, `uq_proposals_one_pending_review_summary_per_feed` | a second pending rule of one key for one feed; a second pending summary for one feed |
| G3 | `uq_rules_proposal_id` | two rules from one proposal |
| G4 | append-only and no-TRUNCATE triggers (existing function) on the three new tables | UPDATE, DELETE, TRUNCATE |
| G5 | `trg_rules_b_matches_proposal` (note 5) | a rule whose feed, key, SQL or description is not its proposal's; version not the key's next; feed not registered |
| G6 | `trg_feed_go_lives_b_guard` | not an approved `review_summary` of this feed; `approved_by` not the latest registration's `owner_id` or not the decider; ids not the proposal's; test load not this feed's `loaded` `go_live_test`; version not the next |
| G7 | `ck_loads_purpose_fits` | a `scheduled` load with no `go_live_id`, a `go_live_test` with one |
| G8 | `trg_loads_a_status` | any move but received → loaded or failed_rolled_back; `loaded` without four `stage_completed` rows; `failed_rolled_back` without one failed stage, or with a standard record |
| G9 | `uq_loads_one_received_per_feed` | a second `received` load of a feed |
| G10 | `trg_load_stages_b_order` | a stage row of a load that is not `received`, or out of order |
| G11 | `trg_standard_records_b_load` | a record of a load that is not `received`, or of another feed |

Every check on a nullable column spells out NULL, has a NULL case and joins `REVIEWED_NULLABLE_CHECKS`. Downgrade stops if any row exists in the new tables, in `loads`, in `rules` or among `rule`/`review_summary` proposals. Nothing from PostgreSQL 15 or later.

**S1, the sandbox proof (not a database guard):** `test_engine_sandbox.py` runs, as rule SQL, a read of another file, a read of the same file, `COPY ... TO`, `ATTACH`, `INSTALL`, `LOAD`, an `http://` read, `glob('*')`, `SET enable_external_access = true`, two statements, a `DELETE`, a `PRAGMA`: each refused. Removal proofs: without `enable_external_access = false` the file tests fail; without the statement check the `PRAGMA` and two-statement tests fail.

## 7. Domain models and repositories

| Item | Change |
|---|---|
| `core/domain/rules.py` (new) | `RuleTemplate`, `RuleCandidateElement`, `RuleRecommenderInput`, `RuleRecommenderOutput` (back-test fields filled by code), `Backtest`, `RuleProposalPayload` |
| `core/domain/go_live.py` (new) | `ReviewSummaryInput`, `ReviewSummaryOutput`, `ReviewSummaryPayload`, `GoLive` |
| `core/domain/load.py` | `Stage`, `LoadPurpose`, `LoadStage`, `StandardRecord`; `LoadStatus` gains `loaded` |
| `RuleRepository` | `add_version()` checks what G5 checks; `rejected_keys(feed_id)` |
| `GoLiveRepository` (new) | `add_version(...)`, `latest(feed_id)` |
| `LoadRepository` | `create(purpose, fetch_id, go_live_id)`, `lock(id)`, `record_stages(...)`, `finish(...)`, `stages(id)`; `StandardRecordRepository.add_many()`, `count(load_id)` |
| `ports/connectors/catalogue.py` | `SourceEntry.history: list[Text] = []` |
| `ports/config.py` | `rule_timeout_seconds` (default 5), `hypercare_days` (default 10), each at least 1 |
| `ports/llm/mock_rules.py` | `rule_recommender`, `review_summariser` |
| `agents/base.py` | `run(..., after_answer=None)` (assumption 9) |

New named errors: `RulesAlreadyProposed`, `NothingToGoLive`, `GoLiveAlreadyProposed`, `ConfigurationChangedMeanwhile`, `ConfigurationChangedSinceSummary`, `OwnerNotADataOwner`, `LoadInProgress`, `NotLive` (409); `NotTheFeedsOwner` (403); `BackToStepNeeded` (422, a `StepInputError`).

## 8. Synthetic data and the demo paths

- `fixtures/sources/files/history/exchange_volumes/2026-10-26.csv` to `2026-10-30.csv`: five deliveries of 10 rows, the columns of `exchange_volumes.csv`. **10-28 has one negative `traded_qty`** (breaks `volume_not_negative`); **10-29 has one blank `instr_id`** (breaks `instrument_id_present`); 8 of each 10 volumes are not multiples of 100 (so `volume_in_whole_lots` flags most history and is returned). `syn_exchange_volumes` gains `history` listing them. No other source has history.

Sam Steward, Casey Checker, Blake Backend, Devon Owner, Opal Operations in backend-ui, on the application database.

| # | What | What happens |
|---|---|---|
| U | `syn_exchange_volumes` (registered, not mapped). Sam: Propose mappings, approve the three; Propose rules | 4 rules proposed with back-tests (`volume_not_negative`: 1 row in 1 of 5 deliveries); `volume_in_whole_lots` returned (share about 0.8) |
| V | Sam approves the four; Casey approves three, rejects one with a note | three `rules` rows, version 1 |
| W | Blake: Prepare go-live | the test load `loaded` (4 stages, 10 records); the summary lists no unmapped column and the three rules; Devon approves: `approved_scheduled`, hypercare until today plus 10 days |
| X | Opal: Load now | a `scheduled` load, 4 stages, 10 records; Devon and Opal get the hypercare notice |
| Y | `syn_bond_terms` (mapped in phase 5): Blake prepares; Devon rejects, back to 06 | `rejected_back_to_step`; Sam is told; the summary listed `maturity` unmapped and its rejected glossary proposal |

## 9. API

| Endpoint | Who | Does |
|---|---|---|
| `POST /api/amaze/feeds/{id}/rules/propose` | `data_steward` | step 09. Commits |
| `POST /api/amaze/proposals/{id}/decision` | the step's role | gains `rule` and `review_summary` (`back_to_step` in the body for a rejection). Commits |
| `GET /api/amaze/proposals?kind=rule\|review_summary` | any backend role | gains the two kinds |
| `POST /api/amaze/feeds/{id}/go-live/summary` | `backend_team` | step 11 prepare: the test load, then the summary or `test_failed_held`. Commits |
| `POST /api/amaze/feeds/{id}/loads` | `operations` | fetch now and load (409 `NotLive` without a go-live). Commits |
| `GET /api/amaze/feeds/{id}` | any backend role | gains rules (current, pending, returned), go-live (latest, hypercare), loads (latest 10, their stages) |
| `GET /api/amaze/loads/{id}` | any backend role | one load, its stages and its first 50 records |

401, 403, 404, 409, 422 and 503 as in phase 5. The two agents are FastAPI dependencies.

## 10. UI

backend-ui, Feed view of a registered feed, three sections after Mapping: **Rules** (per rule: SQL, back-test per delivery, status, steward and checker actions for their roles; returned and refused rules listed with why; Propose rules for the steward); **Go-live** (Prepare for the backend team; the summary marked as the agent's draft, the unmapped columns and rejected glossary proposals, the test load; Approve, or Reject with a note and a step, for the owner only; hypercare until); **Loads** (latest 10, each with its four stages and counts; Load now for operations). No new npm package; consumer-ui unchanged.

## 11. Files

- **Documents (edit):** `docs/plans/phase-6.md`, `docs/ui-polish.md`, `BUILD_PLAN.md`, `CLAUDE.md`, `README.md`, `.env.example`.
- **New:** `migrations/versions/0018_rules_go_live_and_loads.py`; `fixtures/sources/files/history/exchange_volumes/2026-10-26.csv` to `2026-10-30.csv`; `agents/rule_recommender.py`, `agents/review_summariser.py`, `agents/prompts/rule_recommender.v1.yaml`, `agents/prompts/review_summariser.v1.yaml`; `core/engine/sandbox.py`, `curate.py`, `passthrough.py`, `run.py`; `core/domain/rules.py`, `go_live.py`; `core/repositories/go_lives.py`, `standard_records.py`; `core/steps/rule_candidates.py`, `s09_rules.py`, `s11_go_live.py`; `apps/backend-ui/src/views/RulesSection.tsx`, `GoLiveSection.tsx`, `LoadsSection.tsx`.
- **Edit:** `fixtures/sources/catalogue.json`; `agents/base.py`, `agents/registry.py`; `ports/scheduler/local.py`, `ports/llm/mock_rules.py`, `ports/config.py`, `ports/connectors/catalogue.py`; `core/domain/load.py`; `core/repositories/tables.py`, `errors.py`, `configuration.py`, `loads.py`; `core/steps/feed_page.py`; `apps/api/deps.py`, `jobs.py`, `transactions.py`, `routers/amaze.py`; `apps/backend-ui/src/api.ts`, `views/FeedView.tsx`.
- **Tests, new:** `test_engine_sandbox.py`, `test_engine_run.py`, `test_rule_candidates.py`, `test_s09_rules.py`, `test_s11_go_live.py`, `test_agent_rule_recommender.py`, `test_agent_review_summariser.py`, `test_db_rule_guards.py`, `test_db_go_live_guards.py`, `test_db_load_guards.py`, `test_api_amaze_go_live.py`.
- **Tests, edit:** `conftest.py`, `configured.py` (`approved_rule()`, `go_live()`), `stand_in_agents.py`, `test_port_scheduler.py`, `test_api_amaze_onboarding.py`, `test_migrations.py`, `test_append_only.py`, `test_agent_base.py`, `test_step_results.py`, `test_mock_rules.py`, `test_prompts.py`, `test_settings.py`, `test_source_catalogue.py`, `test_repo_configuration.py`, `test_repo_loads.py`, `test_jobs.py`, `test_port_connectors.py`, `test_commit_visibility.py`, `test_concurrency.py`.

## 12. Tasks, in order

Method for every task (followed by hand, `CLAUDE.md`): assumptions the plan does not state are listed first; only section 11's files; RED pasted, then GREEN pasted; an unexpected failure reproduced and its root cause written before the fix; no weakened test; files edited with the file tools only; one commit per task. "Seconds" is the expected added full-suite time.

| # | Behaviour wanted | Test that proves it (written first) | Files | Seconds | Out of scope |
|---|---|---|---|---|---|
| 0 | This plan updated with your decisions | none | this file | 0 | |
| 1 | A delayed `run_soon` never runs before its delay (assumption 29) | `test_port_scheduler.py`: the strict test unchanged; new `test_a_timer_that_fires_early_waits_out_the_rest` (a `Timer` patched to fire 50 ms early) | `ports/scheduler/local.py` | +0.5 | the Azure stub |
| 2 | A failed post-commit kick leaves 200 and the committed row, taken later (assumption 30) | `test_api_amaze_onboarding.py`: `== 200`, then `drain()` makes the run `done` | `apps/api/deps.py` | +0.3 | start-up kick changes |
| 3 | Test time cut before tests are added (assumption 35) | three full runs before and after, pasted; the 56 schema tests unchanged in body; **proof:** one schema built from the offline DDL and one by `alembic upgrade head`, a row count of every table in both, pasted side by side; any difference stops task 3 | `tests/test_commit_visibility.py` (`own_schema`), `tests/conftest.py`, `test_port_connectors.py`, `test_concurrency.py` (lease values only) | −20 | xdist |
| 4 | **Migration tests first:** every G1–G11 refusal and the downgrade, written and run against `0017` | `test_db_rule_guards.py`, `test_db_go_live_guards.py`, `test_db_load_guards.py`, `test_migrations.py`, `test_append_only.py`: RED pasted (fail or error, none pass) | tests only | +4 | code |
| 5 | `0018`, `tables.py`, domain models, repositories, `tests/configured.py` helpers | task 4's tests GREEN; `test_repo_configuration.py`, `test_repo_loads.py`; each guard removed in turn, its tests shown failing. **CHECKPOINT 1:** pytest summary, the 11 removal proofs, the downgrade test | section 11's migration, `core/domain/`, `core/repositories/`, `tests/configured.py` | +2 | applying `0018` to `amaze` |
| 6 | The sandbox (assumption 6, S1) | `test_engine_sandbox.py`: the 12 refusals; a rule over the time limit stopped (limit 0.2 s); the two removal proofs | `core/engine/sandbox.py`, `ports/config.py` | +2 | |
| 7 | The engine: plan, execute, record; four stages; notes 1 and 2; rollback; config changed since go-live | `test_engine_run.py` (section 5.3 names) | `core/engine/curate.py`, `passthrough.py`, `run.py` | +3 | step 12, certification |
| 8 | Step 09: candidates, history, back-test via `after_answer`, propose, the two-step decision; mock rule; prompt | `test_rule_candidates.py`, `test_s09_rules.py`, `test_agent_rule_recommender.py`, `test_agent_base.py` (`after_answer` with no transaction; its failure is a discard), `test_mock_rules.py`, `test_prompts.py`, `test_source_catalogue.py` | agents, `core/steps/`, fixtures, catalogue | +4 | rule version 2 |
| 9 | Step 11: prepare, test load, summary, decision, owner checks, hypercare | `test_s11_go_live.py`, `test_agent_review_summariser.py`, `test_step_results.py` | agents, `core/steps/s11_go_live.py` | +3 | changing the owner |
| 10 | The daily job loads live feeds; Load now; hypercare notices | `test_jobs.py` | `apps/api/jobs.py` | +1 | a load retry |
| 11 | API and feed page; the done-when test | `test_api_amaze_go_live.py` (done-when, roles 403, 409 cases, 422) | `apps/api/`, `feed_page.py` | +4 | |
| 12 | Commit visibility (6), races R1–R3, no-connection tests | `test_commit_visibility.py`, `test_concurrency.py`; each commit removed and each lock or check removed, shown failing | tests only | +25 | other races |
| 13 | backend-ui: Rules, Go-live, Loads | none; both builds; browser rows U to Y with screenshots `phase6-U.png` ... | `apps/backend-ui/src/` | 0 | consumer-ui |
| 14 | Documents; UI polish lines appended (not fixed) | `test_step_results.py` reads `CLAUDE.md` | section 11's documents | 0 | |
| 15 | Review against the seven rules, PostgreSQL 14, this plan; `0018` applied to `amaze` and `amaze_eval` after the row count; three timed runs. **CHECKPOINT 2** | section 13 | section 17 | 0 | merge, push |

Net expected: +35 s added, −20 s cut: **about 175 to 195 s.**

## 13. Commands that prove it is done

Run from `F:\amaze-cm-poc` in Windows PowerShell, each as `.\.venv\Scripts\python -m ...` where it is Python:

| Command | Expected |
|---|---|
| `pytest -q`, three times back to back | all pass, 0 skipped, 0 warnings; each at or below 195 s (above it: stop, report with `--durations=20`) |
| `alembic upgrade head`, then `alembic current` (for `amaze`, and with `DATABASE_URL` set to `amaze_eval`) | `0018 (head)` on both |
| `agents.evaluate_mappings` | unchanged: `25 of 31 (80.6%)` |
| `npm --prefix apps\backend-ui run build`, the same for consumer-ui, `.\scripts\dev.ps1` | exit 0; three `OK` lines and health `ok` |

## 14. Not in phase 6

- Step 12 (arrival, structure check), step 15 (certification, trust score, release), step 20, "Ready" and "Access restricted" (phase 7).
- Mastering beyond pass-through; transformations beyond the cast; deduplication.
- A rule version 2, editing a proposal, rules on a feed with no mapping.
- Changing a feed's owner (assumption 24); ending hypercare by hand.
- Retrying a load left `received`; loads on the feed's own history; load history in the back-test (phase 7).
- Parallel test runs (pytest-xdist with a schema per worker): the way back to 160 s; your decision.
- `AzureOpenAIProvider`, the Azure Functions timer (phase 8).

## 15. Risks

1. **The 0.5 share is a guess.** A strict rule on a dirty source is returned; a loose one passes. The share is in the payload so the steward sees it.
2. **The mock's rules come from templates**; a real model writes free SQL, which the sandbox and statement check must hold (S1). Phase 8 evaluates it.
3. **Configuration changed since go-live fails loads** (assumption 15) until the owner approves again; a steward's re-map stops the daily load. The notice says why.
4. **A load left `received` by a dead process blocks the feed** (G9) until a person acts; nothing in phase 6 clears it. Accepted for phase 6 (2026-11-17): no start-up sweep; it stays phase 7 note 3.
5. **`after_answer` changes the sealed base class.** It is one argument with its own tests; every existing agent test must pass unchanged.
6. **History lives in the catalogue**, not in loads, until phase 7.
7. **Test time** varies 162 to 213 s on this laptop for unchanged code; three runs are reported. A run at the slow end would pass the 195 s ceiling with no code cause, and still stops the builder.
8. **`run_soon` evidence is intermittent** (1 in 10 runs today); the patched-timer test makes the fix provable without luck.

## 16. Carried forward

**Phase 7:** 1. the back-test history gains the feed's own `loaded` scheduled loads (assumption 8); 2. certification reads `standard_records.flags` and the `validated` stage's counts; 3. step 12 decides how a load left `received` is cleared (risk 4).
**Phase 8:** 4. both new agents against Azure OpenAI, the sandbox holding free SQL; 5. engine runs move to the queue with the other background work.

## 17. Results and deviations (recorded during implementation)

### Checkpoint 1 (tasks 1 to 5, head `5c43b43`; decisions of 2026-11-17)

1. G8 widening (loaded to held or released) accepted. 2. Test files edited outside section 11 in `5c43b43` accepted
(`test_domain_models.py`, `test_db_approval_guard.py`, `test_coverage.py`, `test_api_consumer.py`,
`test_request_data.py`, `test_routing.py`). 3. The lease, grace and HTTP timeout changes of task 3 accepted. 4. Guard
proofs by count accepted for checkpoint 1. 5. The extra constraints and uniques accepted. 6. `ck_loads_status`
shadowed by G8 accepted. 7. The permanent offline-DDL equality test accepted.
Changed for the rest of the phase: the ceiling is the median of three back-to-back full runs at or below 195 s, no
single run above 233 s (a breach stops the builder; report with `--durations=20`); guard and sandbox removal proofs
paste the test name and its failure line; task 6 also makes the timed `run_soon` job re-check its timer; task 14 also
fixes the `apps/api/main.py` comment about 503.

### Checkpoint 2 (tasks 6 to 15; head `2078b12` plus this note)

- **Ceiling breached, builder stopped (task 15).** The first three runs (`197.83s`, `235.75s`, `247.12s`) ran with
  the `dev.ps1` servers and the browser still open (my stop command had not killed them), so they are not the
  figures. The figures are the three back-to-back runs made after every dev server, shell and browser of this
  project was closed and checked gone (only the user's own Firefox and VS Code were left running): `2001 passed in
  236.77s`, `2001 passed in 238.89s`, `2001 passed in 186.75s` (0 skipped, 0 warnings). Median 236.77 s (limit
  195 s); two of the three are over the 233 s single-run limit. The earlier set had a median of 235.75 s, so the
  open servers were not the cause. `--durations=20`: the same tests lead every run (the concurrency tests at 1.7 to
  2.1 s, `test_the_offline_ddl_schema_equals_the_alembic_schema` about 2 s, the 2 s libpq wait), and their top-20
  call time is 19 to 25 s in all three, so the 50 s between the fastest and the slowest run is not one test. Before
  any phase 6 code, unchanged code ran 168 s to 233 s on this laptop (task 3 notes), and the suite grew from 1859
  to 2001 tests. No change was made to bring the time down; it is the user's decision (assumption 35, section 14:
  parallel runs).
- `0018` was applied to `amaze` and `amaze_eval` after the counts (loads 0, rules 0, rule and review_summary
  proposals 0 in each): both read `0018 (head)`. `evaluate_mappings`: `25 of 31 (80.6%)`, unchanged (run twice: 8
  calls logged to `amaze_eval`). Both npm builds and `dev.ps1` (three OK lines, health ok) pass.
- **Deviations of tasks 6 to 15** (the commit is the task's): 8. `check_rule_sql` also compares the statement's text
  (task 6, `1a37446`). 9. Task 8's history files, catalogue field and candidates module were written before their
  tests, so those tests had no separate RED run (`e28c0d5`). 10. Task 9 had no separate RED run: its tests were
  written first but first run after the code (`5d1c175`). 11. `record_load` writes in a savepoint
  (`begin_nested`) instead of "a new transaction", which keeps the feed and load locks (`ebd4b97`). 12. The engine
  fails an empty file at raw (`no_data_rows`) and an unreadable one (`not_a_table`); the plan is silent
  (`ebd4b97`). 13. `OwnerNotADataOwner` commits the backend team's note before the 409, so the note survives
  (`08672f6`). 14. The feed page lists current, pending and rejected rule keys; "returned" rules are not stored, so
  only the answer to Propose rules shows them (`08672f6`). 15. Task 12 changed `apps/api/jobs.py`
  (`fetch_and_load` raises `LoadInProgress`, as assumption 33 says) beyond "tests only" (`3d6b1b5`). 16. Files
  outside section 11: `core/steps/errors.py` (`BackToStepNeeded`, `5d1c175`) and `tests/step_chains.py` (the
  chains, `ebd4b97`, `e28c0d5`, `5d1c175`). 17. `FeedView` reads the roles from `/api/me` instead of `App.tsx`
  passing them (`1810134`). 18. **Rows U to Y first ran on the wrong database.** In task 13 the dev API ran with `DATABASE_URL` set by
  `export` in the shell command that started it (not `.env`, not an argument): `amaze_test` with
  `options=-csearch_path=ui_p6`, a scratch schema built by `alembic upgrade head` and seeded with the test helpers
  (`tests/onboarded.py`: its feeds have a one-row profile and `raw/test/<id>/f.csv` files; the bond feed there was
  `a6b36d94…`, created 15:39:58). It was chosen because `0018` could not touch `amaze` before task 15. It was dropped
  afterwards: `amaze` holds `syn_bond_terms` as `553d98bd…` (created 02:20, phase 5 testing) and 0 `raw/test/%`
  fetches, `amaze_test` holds neither. The five screenshots of that run were therefore not of `amaze`. After `0018`
  was applied (task 15) rows U to Y were rerun on `amaze` through the dev servers started by `dev.ps1` (no
  `DATABASE_URL` override in the shell; the API lists `amaze`'s feed ids), the five screenshots in
  `docs/phase6-screenshots/` were replaced, and `amaze` now holds what they left: three rules (version 1) and one
  go-live of `syn_exchange_volumes` with its test load and one scheduled load (4 stages, 10 records each), and the
  rejected go-live of `syn_bond_terms` with a test load. The old ones stay untracked, not committed. 19. The mock rule templates: `present` for string, date and datetime,
  `not_in_future` for datetime only, `in_whole_lots` for `volume` only, to give the plan's four proposed rules and
  one returned (`e28c0d5`). 20. A rule whose every delivery was left out is kept as "not back-tested"
  (`e28c0d5`). 21. The "no fetched file" refusal of step 11 cannot happen to a registered feed (a profile needs a
  fetched file), so its test removes the fetch by monkeypatch (`5d1c175`).

### Task 6 (sandbox, scheduler re-check, settings)

- Deviation 8: `check_rule_sql` also compares the statement's own text with the text given. DuckDB types
  `PRAGMA database_size` as SELECT (it rewrites it to `SELECT * FROM pragma_database_size();`), so the type check
  alone cannot refuse a PRAGMA, which row 12 of S1 requires. A tightening, not a widening.
- Removal proofs (S1). Without `SET enable_external_access = false`: `read another file` and `read the same file`
  fail with `Regex pattern did not match. Expected 'Permission Error'; Actual 'Binder Error: Referenced column "_row" not
  found in FROM clause!'` (the file was read), `http read` with `Actual 'File http://127.0.0.1:9/x.csv requires the
  extension httpfs to be loaded'`, `glob` with `ValueError: invalid literal for int() with base 10: '.\\.env'` (it
  listed the working directory). Without the statement check: `two statements` fails with `Failed: DID NOT RAISE
  RuleRefused`, `PRAGMA` with `Expected regex: 'plain SELECT'; Actual message: 'a rule returns the _row of every row
  that breaks it; this has no _row column'` (it ran), and `COPY TO`, `ATTACH`, `INSTALL`, `LOAD`, `SET`, `DELETE` fail
  with `RuleFailed: Permission Error ...` or `Invalid Input Error: Cannot change configuration option
  "enable_external_access" - the configuration has been locked` (DuckDB's lock refused them, with the wrong class).
- Removal proof, scheduler re-check: with `time.sleep(remaining)` and no `finished` check, `AssertionError: assert 'old'
  not in ['old']` (shutdown) and `assert 'old' not in ['old', 'new']` (replaced).
