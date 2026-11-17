# Phase 7 plan — Every load and release (steps 12, 15, 20) and the consumer loop closing

**Decisions (2026-11-17):** every assumption approved as written except: 4 (the `-n <N>` after-median must be below the serial before-median), 12 (README says it), 19 (a refusal in step 15 or 20 fails the load, naming the step), 25 (an outstanding ticked term is `data_not_in_domains`), 33 and 34 (no "Step N of 4"; the rest of the wording approved), and risk 6 (`DAILY_JOB_ENABLED`, assumption 38).
**Status:** approved 2026-11-17 with the decisions above.
**Date:** 2026-11-17. **Branch:** `phase-7`, created from `main` at `d4ace51`. **Scope:** phase 7 of `BUILD_PLAN.md`; phase 6 plan section 16 (notes 1 to 3); phase 5 plan section 16 (notes 6, 7, 13); phase 4 plan section 16 (notes 5, 6); four items of the planning request (parallel test runs first, requester states, phase 6 UI polish lines, phase 6 lessons). Local only; nothing is pushed.

**Lean mode.** Two checkpoints: (1) after the migration task (task 3), (2) at the end. Guard proofs for new guards only. Race tests only where two writers really meet (assumption 41). Every task names its behaviour, its RED run, its files, added seconds and what is out of scope (section 12). Every browser or demo row names its database, and the task order makes that database ready before the row runs (section 8).

## 1. Goal and "done when"

**Phase 7 is done when all eight are true:**

1. **The Playwright test runs the full PoC story, including one held delivery** (`BUILD_PLAN.md`): `e2e/tests/poc_story.spec.ts` passes three runs in a row on `amaze_test`, schema `e2e_story` (section 8, rows E1 to E9): a request no product covers, source picking, onboarding, one glossary approval and one mapping approval (phase 5 note 7), rules, go-live, a released load, "Ready", then a delivery with a missing column held (`breaking_held`) and a person notified.
2. Step 12: the five outcomes of `CLAUDE.md`, each typed and named in a test; `additive_parked` and `breaking_held` both notify; an additive column goes to the glossary builder and the mapping agent on the load's own profile (phase 5 note 6).
3. Step 15: a trust score from `standard_records.flags` and the `validated` stage's counts (phase 6 note 2); `released` or `held_consumers_warned`.
4. Step 20 on release: one product per feed, with the registration's owner and visibility (phase 4 note 5); each waiting requester gets "Ready" or "Access restricted" (`created`), or is told what is missing (`data_not_in_domains`).
5. The requester sees a distinct state at each point up to "Ready" (assumption 33); a load left `received` by a dead process is cleared (phase 6 note 3); the back-test reads the feed's own loaded scheduled loads (phase 6 note 1).
6. Each committing endpoint and job has one commit-visibility test; the two race pairs of assumption 41 have tests.
7. `0019`'s guards each have a raw-SQL test shown failing without the guard.
8. `pytest -q -n <N>` passes with nothing skipped and no warnings, three back-to-back clean runs, within the ceiling task 1 sets; no test reads another worker's schema (assumption 3).

## 2. State at the start (checked on 2026-11-17)
| Item | Found |
|---|---|
| Git, Alembic, tests | `main` = `d4ace51`; untracked `.claude/`, `.env.main`, `docs/phase6-screenshots/`. `0018` in `amaze`, `amaze_eval`, `amaze_test`. `2001 tests collected`; phase 6's clean runs 236.77 / 238.89 / 186.75 s (median 236.77 s) |
| `amaze` rows | `syn_exchange_volumes`: 3 rules, go-live v1 (hypercare to 2026-11-27), a test and a scheduled load, `loaded`, 10 records each, 0 flagged. `syn_bond_terms`: summary rejected, a `loaded` test load. 10 `onboarding` requirements (6 on `syn_exchange_volumes`) |
| In place | Statuses `ready`, `access_restricted` with no transition to them; `DomainCoverage` counts only feeds with a `released` load; G8 allows `loaded` → `held`/`released` with no proof; `products` has no feed link; no endpoint writes grants; no notification endpoint or inbox; `local_file` reads under `SOURCE_CATALOGUE`'s folder |
| Tools | No `pytest-xdist` or Playwright installed; `pytest-xdist 3.8.0`, `execnet 2.1.2` are `py3-none-any`; `@playwright/test` 1.64.1 (2026-10-14) is the newest release over two weeks old. 20 logical CPUs |

## 3. What you fixed, and where this plan answers it
| Carried item | Answered in |
|---|---|
| Request: test time first: xdist, schema per worker, ceiling, isolation proof, seconds per task | assumptions 1 to 4; task 1; section 12 |
| Phase 6 note 1: back-test history gains the feed's own loaded scheduled loads | assumption 30; task 8 |
| Phase 6 note 2: certification reads `standard_records.flags` and the `validated` counts | assumption 21; task 6; guard G14 |
| Phase 6 note 3 (risk 4): a load left `received` by a dead process | assumption 18; task 5; race R4, R5 |
| Phase 5 note 6: an additive column is input to steps 05 and 06, on the load's profile | assumptions 13, 14; task 4 |
| Phase 5 note 7: one glossary and one mapping approval in the Playwright story | section 8, rows E4, E5 |
| Phase 5 note 13: a bond request must reach source picking | assumption 36; task 9 |
| Phase 4 note 5: the product takes the registration's visibility; grants and "Access restricted" | assumptions 24 to 27 |
| Phase 4 note 6: the daily fetch becomes a load, its structure checked against the signed-off profile | assumptions 9 to 11 (the template is the latest profile: assumption 10) |
| Request: distinct requester states up to "Ready" | assumptions 33 to 35 |
| Request: `docs/ui-polish.md` phase 6 lines | assumption 46 |
| Phase 6 lessons: rows name their database; section 11 complete; RED named per task | section 8; section 11; section 12 |

## 4. Assumptions — veto any by number

**Test time (task 1)**

1. **`pytest-xdist==3.8.0`, `execnet==2.1.2`** in `requirements-dev.txt`. Not in `addopts`: the full run is `pytest -q -n <N>`; one file still runs serially as today.
2. **One schema per worker.** `conftest.py` names it `w_<worker_id>` (`w_gw0` ..., `w_master` without `-n`). `test_database_url` returns the `TEST_DATABASE_URL` with `options=-csearch_path=<schema>` (that schema only, no `public`); `migrated_engine` drops and creates that schema through a base engine, then runs `alembic upgrade head` into it, and drops it at session end. `fresh_schema` and `scratch_schema` keep their own uuid schemas. `test_migrations.py`'s two `'public'` queries use `current_schema()`. `amaze_test.public` is no longer used and is left as it is.
2a. **Advisory lock keys include the schema (decision of 2026-11-17).** `pg_advisory_xact_lock(hashtext(current_schema() || ':' || <key>))` in `lock_source()` (`core/steps/known_source.py`) and `GlossaryRepository.lock()`. Advisory locks are database-wide, so two workers' tests (same fixture source codes, locks held to the test's end) deadlocked at `-n 4` (`DeadlockDetected`, reproduced with two connections). One schema in production, so locking is unchanged there; every production path takes one source lock per transaction (`s3_pick.py:99`, `s03_signoff.py:90`, `known_source.py:119`), so the deadlock was test-only. Test (`test_worker_isolation.py`): in two schemas one source code gives two keys (two connections take it at once, neither waits); in one schema the second connection waits.
3. **Isolation proof.** `tests/test_worker_isolation.py`: (a) `current_schema()` and `current_setting('search_path')` of `migrated_engine` and `db_session` are the worker's schema only; (b) no test file names a schema literal (`'public'`, `w_`, `nspname =`, `search_path=`) outside `conftest.py` and the two schema helpers. Removal proof: every worker given one shared schema name, the failures pasted.
4. **Ceiling.** Three clean serial runs before the change and three clean `-n <N>` runs after, pasted ("clean": no dev server, browser or other pytest running; ports 8000, 5173, 5174 checked free and pasted). `<N>` is the fastest of one run each at 4, 6 and 8 with no failure. **Ceiling: the median of three at or below 1.2 × the after-median; no single run above 1.5 × it** (both rounded up to 5 s and written in section 17 at task 1). **The `-n <N>` after-median must be below the serial before-median; if not, the builder stops and reports** (decision of 2026-11-17). A breach stops the builder (report with `--durations=20`). Expected after-median: 50 to 80 s.

**Step 12: arrival and structure (deterministic, no agent)**

5. **Step 12 runs on every `scheduled` load** (daily job and Load now), between the fetch and the engine. A `go_live_test` load skips it.
6. **One pattern, no connection during the file work:** `arrival_plan()` reads (load, feed, template profile, earlier scheduled hashes); the caller ends the transaction; `check_arrival()` reads the file through `BlobStore.get()`, takes its SHA-256 and profiles it with `core/steps/profiling.profile_csv()` (DuckDB in memory); `record_arrival()` locks the feed row, then the load row, checks the load is still `received`, writes, notifies.
7. **Outcomes, first match wins:** `resent_quarantined` (the SHA-256 equals an earlier scheduled load's of this feed: status `quarantined`, no stage); `breaking_held` (a template column is missing, or the file is not a CSV table: status `held`, no stage); `additive_parked` (columns the template lacks: the load goes on, the new columns are parked); `on_time_complete` (the load goes on). `late_alert`: the daily job's fetch of a feed with a go-live is `source_down`; no load row (task 7).
8. **"On time" in the PoC** is "delivered by its daily run or by Load now"; there is no delivery deadline.
9. **The template** is the column list of a profile (phase 4 note 6). Column order does not matter (the engine matches names).
10. **The template is the feed's latest profile:** the signed-off one until step 12 writes a newer one for an additive change. Departure from note 6's "signed-off profile": otherwise the same extra column is parked again every day. Alternative: always the signed-off one.
11. **Types are not compared in step 12.** A mapped value that no longer casts already holds the file at step 13 (`values_do_not_cast`, phase 6 assumption 16); DuckDB's sniffed types change with the values (an all-empty column reads as VARCHAR), which would hold good files.
12. **Hashes compared:** only scheduled loads that carry one (written from `0019` on). `amaze`'s phase 6 scheduled load has none, so the first phase 7 load of the same file passes (row Z1); the next is `resent_quarantined` (row Z2). **`README.md`'s demo section says that an unchanged fixture file is quarantined on every later daily run** (decision of 2026-11-17; task 13).
13. **`additive_parked` writes a new `feed_profiles` version** (the load's `fetch_id`, the template's `spec`, `coverage` `[]`, `profiled_by` `step_12`) and queues a glossary run, reason `structure_change` (new), `columns` = the added columns, through `queue_run()` (an active run is reused and the columns wait: noted in the arrival detail). `builder_input()` looks only at a `structure_change` run's own columns and treats none as sent back. The mapping agent sees the column because its input is the latest profile.
14. **Who is told.** `additive_parked`: glossary owners, data stewards, backend team. `breaking_held`: backend team, operations, the feed's owner, and the consumers (assumption 23). `resent_quarantined` and `late_alert`: operations and the owner. The parked column is not loaded until it is mapped and a new go-live is approved (phase 6 assumption 15).
15. **A file with no data rows** passes step 12 and fails at step 13 (`no_data_rows`), as today.
16. **`plan_load()` refuses a scheduled load whose arrival outcome is not `on_time_complete` or `additive_parked`** (`ArrivalNotChecked`, 409); G13 refuses it too.
17. **Lock order unchanged** (`CLAUDE.md`): step 12 takes the feed row, then (queue) the `glossary_runs` row, then the load row; step 15 and 20 take the feed row, the requirements, then the load row; the stale clear takes the feed row, then the load row.
18. **A load left `received` (phase 6 note 3).** Before creating a load, `clear_stale_load()` (in `s12_arrival.py`) locks the feed row: a `received` load older than `LOAD_STALE_MINUTES` (default 30) is locked, checked still `received`, and moved to `abandoned` (new status: no stage, no record, `failure` and `finished_at` set); operations are told; the new load follows. Younger: `load_in_progress`, as today. Called by the daily job, Load now and step 11's Prepare. No start-up sweep.

**Step 15: certification (deterministic, no agent)**

19. **Runs on every scheduled load that ends `loaded`, in the same transaction as `record_load()`** (`finish_scheduled_load()`: feed row, the requirements selecting the feed in id order, `record_load()`, step 15, step 20, the reports; the caller commits). A process that dies before the commit leaves the load `received`, which assumption 18 clears. **One transaction (decision of 2026-11-17): steps 15 and 20 run inside `record_load()`'s savepoint (a `then` callback, so `core/engine/` imports no step); a refusal there (a guard, for example G15) rolls the savepoint back and the existing fallback writes the completed stages, a failed `standard` stage, no record and `failed_rolled_back`, with `failure` = `certify: <guard text>` or `create_product: <guard text>`.** Test: `test_s20_create_product.py::test_a_refused_product_fails_the_load_naming_create_product` (G15 made to refuse).
20. **Trust score = 100 × (rows with no flag) / rows, rounded to 0.1.** `released` when it is at or above `TRUST_RELEASE_THRESHOLD` (default 90, 1 to 100); else `held_consumers_warned` (status `held`). A feed with no rules scores 100.0.
21. **One `load_certifications` row per certified load:** rows and flagged rows counted from `standard_records` of the load, the `validated` stage's per-rule counts, score, threshold, outcome, the consumers warned. G14 recounts.
22. **A held load stays held.** No person releases it and certification is not re-run; the next delivery is certified on its own.
23. **Consumers** are the requesters of every requirement whose current choice selects the feed (`onboarding`, `ready` or `access_restricted`), and the product's owner. `held_consumers_warned` tells them, operations and the backend team; the data released earlier stays available.

**Step 20: the product and the requester**

24. **One product per feed, created at its first release** (`products.feed_id`, unique): code = the feed's code; name = the feed's name; description "Released from the {feed name} feed."; domain = the most frequent glossary domain of its elements (ties alphabetical); elements = the element keys of the current mappings (equal to the go-live's pins, or the load would have failed); `owner_id` and `visibility` = the latest registration's (phase 4 note 5; G15); `requirement_id` = the oldest waiting requirement, or none. Later releases reuse it; its elements do not change.
25. **Each `onboarding` requirement selecting the feed is checked at every release:** every element key of the requirement is in the product → `created`; the requirement moves to `ready` if `core/entitlement/decide.py` entitles the requester, else `access_restricted`. Otherwise → `data_not_in_domains` (the missing keys): the requirement stays `onboarding`, the backend team is told. **A ticked term (`requirement_terms`) the product's elements do not cover is outstanding (decision of 2026-11-17): the requirement is `data_not_in_domains`, stays `onboarding`, and its outstanding terms are listed to the backend team and on the requester's page (`part_missing`, assumption 33).** A term counts as covered when the glossary phrase matching (`core/steps/matching.py`) finds it among the product's elements' names and aliases.
26. **No grant is written.** A restricted product gives its requester "Access restricted" with the route `route_to_access()` already builds ("Ask Devon Owner for access to …"). Granting access is not in phase 7.
27. `REQUIREMENT_TRANSITIONS` gains (`onboarding`, `ready`) and (`onboarding`, `access_restricted`).
28. **Reports.** The requester: "Ready: {title}" or "Access restricted: {title}". Phase 6's hypercare reports stay as they are.
29. A requirement linked later to an already released feed (`duplicate_linked`, known source) becomes Ready at that feed's next release, not at once.

**Carried, data, API, tests**

30. **Back-test history (phase 6 note 1):** `RuleRecommenderInput` gains `load_history` (label, `file_ref`) of the feed's newest 10 scheduled loads that completed all four stages; `after_answer` reads each through `BlobStore.get()` with no connection held, after the catalogue history. Labels `load <received date>`.
31. **One migration, `0019_arrival_certification_products.py`**, developed on the test database; applied to `amaze` and `amaze_eval` in task 14 before rows Z1 to Z3, after counting what the guards would refuse. Any later fix is `0020`.
32. **Guards** (section 6): G12 to G16, each with a raw-SQL test and a removal proof (test name and failure line pasted).
33. **Requester states (`requirement_page.py`).** While `onboarding`, the message stays `CLAUDE.md`'s word for word, and a new `progress` line shows one of: `checking_file` "The backend team is checking the first file from {source}." (feed candidate); `setting_up` "{source} is registered; the backend team is setting up how its data is read and checked." (registered, no go-live); `approved` "The data owner approved {source} for loading; the first delivery is being checked." (go-live, nothing released); `delivery_held` "The latest delivery from {source} was held ({reason}); the next delivery is checked again."; `part_missing` "{source} is released, but it does not hold {elements} yet; the backend team has been told." ({elements}: the missing element names and outstanding terms, assumption 25). **Approved 2026-11-17, without "Step N of 4".**
34. **Ready and Access restricted:** `ready` "Ready. Your data is in {product name}."; `access_restricted` "Access restricted. Your data is in {product name}. {route}." When the feed's latest scheduled load is held after a release: "The delivery of {date} was held ({reason}); you still see the data released on {date}." **Approved 2026-11-17.**
35. The page gains `progress`, `product` (code, name, entitled, route) and `held_note`. Statuses other than these keep today's messages.
36. **Phase 5 note 13:** `fixtures/demo/products.json`'s `demo_bond_reference` loses `coupon_rate`, so a `coupon rate` request raises a requirement and reaches source picking (Bond terms). Fresh seeds only: `amaze`'s demo product keeps it (`product_elements` is append-only). Alternative: a new synthetic source declaring an element no demo product holds.
37. **Notifications inbox:** `GET /api/notifications` (the signed-in dev user's, newest 50); a Notifications tab in both UIs (subject, body, time; no read state). Needed so the story shows "a person is notified".
38. **Settings:** `TRUST_RELEASE_THRESHOLD` (default 90, 1 to 100), `LOAD_STALE_MINUTES` (default 30, at least 1), and **`DAILY_JOB_ENABLED` (default true; risk 6, decision of 2026-11-17): when false, `apps/api/main.py` registers no daily job** (the glossary runs worker still runs), in `ports/config.py` and `.env.example`. Test: `test_jobs.py::test_with_the_daily_job_disabled_no_daily_job_is_registered` (the app started with it false; the scheduler holds no `daily-source-fetch` job).
39. **Playwright:** `@playwright/test` `1.64.1` (exact) in `e2e/package.json`, Chromium via `npx playwright install chromium`. Playwright starts the API and both UIs itself on 8000, 5173 and 5174 with `reuseExistingServer: false`, so a running `dev.ps1` (on `amaze`) stops the run before any test. `e2e/serve_api.py` sets `DATABASE_URL` to `amaze_test` with `search_path=e2e_story`, `SOURCE_CATALOGUE=data/e2e/sources/catalogue.json`, `LOCAL_BLOB_ROOT=data/e2e/blob`, `DAILY_JOB_ENABLED=false`, prints them, and refuses when the database is `DATABASE_URL`'s in `.env`. `e2e/prepare.py` (global setup, the same refusal) rebuilds schema `e2e_story` (`alembic upgrade head`, `core.seed`, `core.seed --demo`), copies `fixtures/sources/` to `data/e2e/sources/` and empties `data/e2e/blob/`. Not part of the pytest time; budget 300 s per run.
40. **The held delivery:** before row E9 the spec copies `fixtures/e2e/syn_bond_terms_breaking.csv` (10 synthetic rows; `cpn` renamed `coupon_pct`) over `data/e2e/sources/files/bond_terms.csv`, then Opal presses Load now.
41. **Race pairs, two** (own schema each; the second writer started by an event inside the first): R4 the stale clear and the slow original writer's `record_load()` of one load: exactly one of `loaded` or `abandoned`, never records under `abandoned`. R5 two stale clears of one load (daily job and Load now): one `abandoned` and one new load; the other `load_in_progress` (409 through the API). Not raced: two loads of one feed (phase 6 R3, G9, unchanged).
42. **Commit visibility:** no new committing endpoint (`GET /api/notifications` reads). The two existing load tests (the daily job's load, Load now) also assert the arrival, the certification, the product and the requirement status are committed. `test_no_connection_is_held_during_a_load_run` gains step 12's `BlobStore.get()` and profile; the rules propose call gains its `load_history` read.
43. **Synthetic data only**; new codes start with `syn_`; new fixture files sit under `fixtures/e2e/`.
44. **Documents** (task 13): `CLAUDE.md` (layout: `e2e/`, the three step modules; `0019` guards; step 12 and 15 rules; the `-n` full-run command), `BUILD_PLAN.md` (phase 7 as built), `README.md` (`0019 (head)`, the test and e2e commands, rows Z, the quarantine of unchanged fixture files, assumption 12), `.env.example` (assumption 38).
45. **UI** checked by both builds, the Playwright test (rows E) and a scripted browser run on `amaze` (rows Z) with screenshots in `docs/phase7-screenshots/` (left untracked, as phase 6's).
46. **`docs/ui-polish.md`, the six phase 6 lines:** "Feed, Loads: stages with no legend; purpose wording" → **phase 7** (task 12 edits that section anyway). "Roles appear late" (Playwright waits for the button), "returned and refused rules not stored", "result line at the top of the page" (Rules), "Prepare disabled with no word", "mock plurals" → **phase 7.5**.

## 5. Steps and typed results

### 5.1 The shapes

| Module | Result (`outcome`) | Fields |
|---|---|---|
| `core/steps/s12_arrival.py` | `OnTimeComplete` / `LateAlert` / `ResentQuarantined` / `AdditiveParked` / `BreakingHeld` | load_id / feed_id, fetch detail / load_id, same_as (load id) / load_id, added, profile_id, run_id / load_id, missing, reason |
| `core/steps/s15_certify.py` | `Released` / `HeldConsumersWarned` | load_id, trust_score, threshold, rows, flagged_rows / the same and warned (user ids) |
| `core/steps/s20_create_product.py` | `Created` / `DataNotInDomains` | requirement_id, product_code, product_new, status (`ready`, `access_restricted`) / requirement_id, missing (element keys), terms (outstanding) |

Not outcomes: a stale load abandoned, a held load's later delivery. `test_step_results.py` reads "12 Arrival and structure", "15 Certify", "20 Create product" from `CLAUDE.md` and compares them with the unions in order.

### 5.2 Rule 1: who decides what

| Step | Decides | Allows, and the database guard |
|---|---|---|
| 12 | nobody: rules execute | arrival fields and `quarantined`/`held` (G12, G13) |
| 15 | nobody: rules execute (threshold from configuration) | a `load_certifications` row; `loaded` → `released`/`held` (G13, G14) |
| 20 | nobody: the registration (decided in step 04) sets owner and visibility | a product with a feed (G15) |

### 5.3 Every outcome name and its test

| Outcome | Named test |
|---|---|
| `on_time_complete` | `test_s12_arrival.py::test_a_delivery_matching_its_template_goes_on_to_the_engine` |
| `late_alert` | `test_jobs.py::test_a_live_feed_whose_daily_fetch_is_down_gives_a_late_alert` |
| `resent_quarantined` | `test_s12_arrival.py::test_a_byte_identical_delivery_is_quarantined_and_never_loaded` |
| `additive_parked` | `test_s12_arrival.py::test_a_new_column_is_parked_profiled_and_sent_to_the_glossary_builder` |
| `breaking_held` | `test_s12_arrival.py::test_a_missing_template_column_holds_the_delivery_and_warns_consumers` |
| `released` | `test_s15_certify.py::test_a_load_at_or_above_the_threshold_is_released` |
| `held_consumers_warned` | `test_s15_certify.py::test_a_load_below_the_threshold_is_held_and_its_consumers_warned` |
| `created` | `test_s20_create_product.py::test_the_first_release_creates_the_product_and_makes_the_requester_ready` |
| `data_not_in_domains` | `test_s20_create_product.py::test_a_requirement_the_product_does_not_cover_stays_onboarding` |

Other named tests: `test_s20_create_product.py::test_a_restricted_registration_gives_access_restricted_with_the_route`, `::test_an_outstanding_ticked_term_is_data_not_in_domains`, `::test_a_refused_product_fails_the_load_naming_create_product`; `test_jobs.py::test_with_the_daily_job_disabled_no_daily_job_is_registered`; `test_s12_arrival.py::test_the_mapping_agent_sees_the_parked_column_on_the_load_profile`; `test_jobs.py::test_a_received_load_older_than_the_stale_limit_is_abandoned_and_the_feed_loads`; `test_s09_rules.py::test_the_back_test_includes_the_feeds_loaded_scheduled_loads`; `test_api_consumer_sourcing.py::test_the_requester_sees_each_onboarding_state_in_turn`; `test_request_data.py::test_a_coupon_rate_request_with_the_demo_products_reaches_source_picking`.

## 6. Data model

**Migration `0019_arrival_certification_products.py`**

| Table | Change |
|---|---|
| `loads` | `content_sha256` (64 hex), `arrival_outcome` (`on_time_complete`, `resent_quarantined`, `additive_parked`, `breaking_held`), `arrival_detail` jsonb, `arrival_profile_id` (FK `feed_profiles`); statuses gain `quarantined`, `abandoned` |
| `load_certifications` (new) | load_id (unique), feed_id, rows, flagged_rows, rule_counts jsonb, trust_score numeric(4,1), threshold numeric(4,1), outcome (`released`, `held_consumers_warned`), warned text[]. Append-only |
| `products` | `feed_id` (FK `feeds`, nullable; unique where not null) |
| `glossary_runs` | `ck_glossary_runs_reason` gains `structure_change` |

| # | Guard | Refuses |
|---|---|---|
| G12 | `trg_loads_b_arrival` | arrival fields on a `go_live_test` load or a load not `received`; changing them once set; `arrival_profile_id` without `additive_parked` or not a profile of the load's feed |
| G13 | `trg_loads_a_status` (replaces `0018`'s) | any move but: received → loaded / failed_rolled_back (as `0018`; a scheduled one only after `on_time_complete` or `additive_parked`); received → quarantined (`resent_quarantined`, no stage); received → held (`breaking_held`, no stage); received → abandoned (no stage, no record, failure and finished_at set); loaded → released / held (scheduled, with a certification of the matching outcome) |
| G14 | append-only and no-TRUNCATE on `load_certifications`; `trg_load_certifications_b_guard` | a certification of a load that is not this feed's `loaded` scheduled load; rows or flagged rows that differ from its `standard_records`; a score that is not the formula; an outcome that does not fit score and threshold |
| G15 | `uq_products_feed_id`, `trg_products_b_feed` | a second product of a feed; a product with a feed that is not registered with a released load, or whose owner or visibility is not the latest registration's |
| G16 | `ck_glossary_runs_reason` | a reason outside the four |

Every check on a nullable column spells out NULL, has a NULL case and joins `REVIEWED_NULLABLE_CHECKS`. Downgrade stops if any `load_certifications` row, product with a feed, load with an arrival field or status `quarantined`/`abandoned`, or `structure_change` run exists. Nothing from PostgreSQL 15 or later. Existing rows pass (the `amaze` loads have no arrival fields).

## 7. Domain models and repositories
| Item | Change |
|---|---|
| `core/domain/load.py` | `LoadStatus` gains `quarantined`, `abandoned`; `ArrivalOutcome`; `Load` gains the four arrival fields; `LoadCertification`, `LoadCertificationDraft` |
| `core/domain/product.py` | `feed_id` on `ProductDraft` and `Product` |
| `core/domain/requirement.py`, `mapping.py`, `rules.py` | assumption 27; reason `structure_change`; `load_history` |
| `LoadRepository` | `record_arrival(...)`, `abandon(id, failure)`, `scheduled_hashes(feed_id)`, `fully_loaded_scheduled(feed_id, limit)` |
| `LoadCertificationRepository` (new, `certifications.py`) | `add(...)`, `for_load(load_id)` |
| `ProductRepository` | `create(feed_id=...)`, `for_feed(feed_id)` |
| `ports/config.py` | assumption 38 |

New named errors: `ArrivalNotChecked` (409), `LoadNotLoaded` (409). Existing `LoadInProgress`, `LoadNotReceived` reused.

## 8. Synthetic data and the demo paths

- `fixtures/e2e/syn_bond_terms_breaking.csv`: the 10 rows of `bond_terms.csv` with the `cpn` header renamed `coupon_pct`.
- `fixtures/demo/products.json`: assumption 36.

**Rows E: database `amaze_test`, schema `e2e_story`, rebuilt by `e2e/prepare.py` at the start of every Playwright run** (so it is ready before E1). Riley Requester, Blake Backend, Glenn Glossary, Sam Steward, Casey Checker, Devon Owner, Opal Operations.

| # | Who: what | What the spec checks |
|---|---|---|
| E1 | Riley (consumer-ui): search "coupon rate", Request this data; Find a source; confirm the scope; list sources; pick Bond terms, accept its conditions | requirement raised (not `reuse`); message is `CLAUDE.md`'s onboarding line; progress `checking_file` |
| E2 | Blake (backend-ui): profile, summarise, sign off `meets`, register (owner Devon, open) | feed `registered`; Riley's progress `setting_up` |
| E3 | the glossary run finishes in the background | banner shows the run done (wait at most 60 s) |
| E4 | Glenn: approves one proposal | one element version written |
| E5 | Sam: Propose mappings; approves the mappings of `instr_id`, `matures_on`, `cpn` | three current mappings |
| E6 | Sam: Propose rules, approves; Casey approves | at least one `rules` row |
| E7 | Blake: Prepare go-live; Devon approves | `approved_scheduled`; Riley's progress `approved` |
| E8 | Opal: Load now | arrival `on_time_complete`, four stages, trust 100.0, `released`; product Bond terms; Riley's page "Ready. Your data is in Bond terms."; Riley's inbox "Ready: …"; a new "coupon rate" search answers `reuse` of Bond terms |
| E9 | the spec swaps the file (assumption 40); Opal: Load now | load `held`, arrival `breaking_held`, missing `cpn`, no stage; Opal's and Blake's inbox "Delivery held: syn_bond_terms"; Riley's page still Ready, with the held note |

**Rows Z: database `amaze`, run in task 14 only after `0019` is applied to it**, through `dev.ps1` with no `DATABASE_URL` override (proof: the API lists feed `c93ab4be…` for `syn_exchange_volumes`).

| # | What | What happens |
|---|---|---|
| Z1 | Opal: Load now on `syn_exchange_volumes` | `on_time_complete` (no stored hash yet), 4 stages, trust 100.0, `released`; product `syn_exchange_volumes` (open, Devon); the 6 waiting requirements `ready` or `data_not_in_domains`, each listed; hypercare report to Devon and Opal |
| Z2 | Opal: Load now again | `resent_quarantined`, no stage; Opal and Devon told |
| Z3 | Riley: open one ready requirement; Notifications | "Ready. Your data is in Exchange daily volumes."; the Ready notice |

## 9. API
| Endpoint | Who | Does |
|---|---|---|
| `POST /api/amaze/feeds/{id}/loads` | `operations` | as phase 6, plus the stale clear, step 12, 15 and 20; the answer adds arrival, certification and products. Commits |
| `POST /api/amaze/feeds/{id}/go-live/summary` | `backend_team` | gains the stale clear before its test load. Commits |
| `GET /api/amaze/feeds/{id}` | any backend role | loads gain arrival outcome and detail, certification; the feed gains its product and parked columns |
| `GET /api/consumer/requirements/{id}` | the requester | gains `progress`, `product`, `held_note` (assumptions 33 to 35) |
| `GET /api/notifications` | any dev user | the user's own, newest 50 |

409 `ArrivalNotChecked`, `LoadInProgress`, `LoadNotReceived`, as in `apps/api/transactions.py`.

## 10. UI

backend-ui: Feed, **Loads** (each load: arrival outcome and why, parked or missing columns, certification score/threshold/outcome, the statuses `quarantined`, `held`, `abandoned`; a legend "rows in → rows out"; purposes "test load for go-live" and "scheduled load"); a **Product** line on the feed (code, visibility, created); a **Notifications** tab. consumer-ui: Requirement shows the progress line, Ready or Access restricted with the product name (and the route), the held note; a **Notifications** tab. No new npm package in either UI.

## 11. Files

- **Documents (edit):** `docs/plans/phase-7.md`, `docs/ui-polish.md`, `BUILD_PLAN.md`, `CLAUDE.md`, `README.md`, `.env.example`, `.gitignore` (`e2e/test-results/`, `e2e/playwright-report/`).
- **Dependencies (edit):** `requirements-dev.txt`.
- **New:** `migrations/versions/0019_arrival_certification_products.py`; `core/steps/s12_arrival.py`, `s15_certify.py`, `s20_create_product.py`; `core/repositories/certifications.py`; `apps/api/routers/notifications.py`; `apps/backend-ui/src/views/NotificationsView.tsx`; `apps/consumer-ui/src/views/NotificationsView.tsx`; `e2e/__init__.py`, `e2e/prepare.py`, `e2e/serve_api.py`, `e2e/package.json`, `e2e/package-lock.json`, `e2e/playwright.config.ts`, `e2e/tests/poc_story.spec.ts`; `fixtures/e2e/syn_bond_terms_breaking.csv`.
- **Edit:** `fixtures/demo/products.json`; `core/domain/load.py`, `product.py`, `requirement.py`, `mapping.py`, `rules.py`; `core/repositories/tables.py`, `errors.py`, `loads.py`, `products.py`, `glossary.py` (assumption 2a); `core/engine/run.py`; `core/steps/known_source.py` (assumption 2a), `s05_glossary.py`, `s09_rules.py`, `s11_go_live.py`, `requirement_page.py`, `feed_page.py`; `ports/config.py`; `apps/api/main.py`, `deps.py`, `jobs.py`, `transactions.py`, `routers/amaze.py`, `routers/consumer.py`; `apps/backend-ui/src/App.tsx`, `api.ts`, `views/FeedView.tsx`, `views/LoadsSection.tsx`; `apps/consumer-ui/src/App.tsx`, `api.ts`, `views/RequirementView.tsx`, `views/StatusLine.tsx`.
- **Tests, new:** `test_worker_isolation.py`, `test_db_arrival_guards.py` (G12, G13), `test_db_certification_guards.py` (G14 to G16), `test_s12_arrival.py`, `test_s15_certify.py`, `test_s20_create_product.py`, `test_api_notifications.py`.
- **Tests, edit:** helpers `conftest.py`, `configured.py` (`live_feed()`, `released_load()`), `onboarded.py`, `step_chains.py`, `stand_in_agents.py`; `test_migrations.py`, `test_repo_glossary.py` (assumption 2a), `test_append_only.py`, `test_domain_models.py`, `test_step_results.py`, `test_settings.py`, `test_repo_loads.py`, `test_repo_products.py`, `test_demo_seed.py`, `test_request_data.py`, `test_routing.py`, `test_engine_run.py`, `test_s05_glossary.py`, `test_s09_rules.py`, `test_s11_go_live.py`, `test_jobs.py`, `test_api_amaze_go_live.py`, `test_api_consumer_sourcing.py`, `test_commit_visibility.py`, `test_concurrency.py`.

## 12. Tasks, in order

Method for every task (followed by hand, `CLAUDE.md`): assumptions the plan does not state are listed first; only section 11's files; the RED run pasted (its summary line), then GREEN pasted; an unexpected failure reproduced and its root cause written before the fix; no weakened test; files edited with the file tools only; one commit per task. "Seconds" is the expected added **serial** test time; the wall time at `-n <N>` is about that divided by `<N>`.

| # | Behaviour wanted | RED run, then the test that proves it | Files | Seconds | Out of scope |
|---|---|---|---|---|---|
| 0 | This plan updated with your decisions | none | this file | 0 | |
| 1 | Parallel runs, a schema per worker, isolation, a new ceiling (assumptions 1 to 4) | RED: `test_worker_isolation.py` run serially before `conftest.py` changes (`public` is the schema). Then three clean serial runs, one each at `-n 4/6/8`, three clean `-n <N>` runs, pasted; the shared-schema removal proof; **stop and report if the after-median is not below the before-median** | `requirements-dev.txt`, `tests/conftest.py`, `tests/test_migrations.py`, `tests/test_worker_isolation.py`, `tests/test_repo_glossary.py`, `core/steps/known_source.py`, `core/repositories/glossary.py`, section 17. Plus assumption 2a: its lock-key test, and a removal proof (key without the schema: the test fails) | +0.5 | any other test body change; e2e |
| 2 | **Migration tests first:** G12 to G16, the downgrade, append-only `load_certifications` | RED against `0018`: `test_db_arrival_guards.py`, `test_db_certification_guards.py`, `test_migrations.py`, `test_append_only.py` (fail or error, none pass) | tests only | +3 | code |
| 3 | `0019`, `tables.py`, domain, repositories, `configured.py` helpers | task 2 GREEN; `test_repo_loads.py`, `test_repo_products.py`, `test_domain_models.py`; each guard removed in turn, its test name and failure line. **CHECKPOINT 1:** pytest summary, the 5 removal proofs, the downgrade test | section 11's migration, `core/domain/`, `core/repositories/`, `tests/configured.py` | +2 | applying `0019` to `amaze` |
| 4 | Step 12 (assumptions 5 to 17) | RED: `test_s12_arrival.py` (section 5.3 names, precedence, both notify, the mapping agent sees the parked column), `test_engine_run.py` (`ArrivalNotChecked`), `test_s05_glossary.py` (`structure_change` asks only its columns) | `core/steps/s12_arrival.py`, `s05_glossary.py`, `core/engine/run.py`, `tests/step_chains.py` | +4 | type checks; late_alert (task 7) |
| 5 | The stale clear (assumption 18) | RED: `test_jobs.py` stale case, a younger one is `load_in_progress`; `test_s11_go_live.py` Prepare clears too | `s12_arrival.py`, `s11_go_live.py`, `apps/api/jobs.py`, `ports/config.py` | +1 | a start-up sweep |
| 6 | Step 15 and step 20 in the finishing transaction (assumptions 19 to 29) | RED: `test_s15_certify.py`, `test_s20_create_product.py` (section 5.3 names, the restricted case, an outstanding term, the lock order, the threshold edge 90.0, G15 refusing → `failure` names `create_product`) | `core/steps/s15_certify.py`, `s20_create_product.py`, `core/engine/run.py` (`then`), `core/domain/requirement.py` | +4 | releasing a held load; grants |
| 7 | The daily job and Load now run 12 → 13 → 15 → 20; `late_alert`; a kick after `additive_parked` | RED: `test_jobs.py` (late alert, quarantine, held, released, kick called; `DAILY_JOB_ENABLED=false` registers no daily job) | `apps/api/jobs.py`, `main.py`, `deps.py`, `ports/config.py` | +2 | |
| 8 | Back-test history from loads (assumption 30) | RED: `test_s09_rules.py::test_the_back_test_includes_the_feeds_loaded_scheduled_loads` | `core/domain/rules.py`, `s09_rules.py`, `routers/amaze.py`, `deps.py` | +1 | |
| 9 | Requester states and messages, the API, the inbox endpoint, note 13 | RED: `test_api_consumer_sourcing.py` (each state in turn; outstanding terms on the page), `test_api_notifications.py`, `test_request_data.py` (note 13), `test_api_amaze_go_live.py` (feed page, Load now answer), `test_step_results.py`, `test_demo_seed.py`, `test_routing.py` | `requirement_page.py`, `feed_page.py`, `routers/`, `main.py`, `transactions.py`, `fixtures/demo/products.json` | +4 | |
| 10 | Commit visibility (2 updated), races R4, R5, no-connection additions | RED: each new assertion with its commit, lock or check removed, shown failing | `test_commit_visibility.py`, `test_concurrency.py` | +7 | other races |
| 11 | The Playwright harness and the story spec (rows E1 to E9) | RED: the spec's first run, pasted (it fails at the first step that needs task 12's UI) | `e2e/`, `fixtures/e2e/`, `.gitignore` | 0 | the Azure run (phase 8) |
| 12 | backend-ui and consumer-ui (section 10; polish line 31) | GREEN: the spec passes three runs in a row, times pasted; both builds | section 11's UI files | 0 | the other polish lines |
| 13 | Documents; UI polish lines appended (not fixed) | `test_step_results.py` reads `CLAUDE.md` | section 11's documents | 0 | |
| 14 | Counts, then `0019` applied to `amaze` and `amaze_eval`; rows Z1 to Z3 on `amaze`; review against the seven rules, PostgreSQL 14 and this plan; three timed runs. **CHECKPOINT 2** | section 13 | section 17 | 0 | merge, push |

Net expected: about +28 s serial, about +4 to +7 s of wall time at `-n 4` to `-n 8`.

## 13. Commands that prove it is done
Run from `F:\amaze-cm-poc` in Windows PowerShell, each as `.\.venv\Scripts\python -m ...` where it is Python:

| Command | Expected |
|---|---|
| `pytest -q -n <N>`, three times back to back, clean | all pass, 0 skipped, 0 warnings; within task 1's ceiling and cap (a breach: stop, `--durations=20`) |
| `npx playwright test` in `e2e\`, three times | `1 passed` each, at or below 300 s; the first lines of `serve_api.py` name `amaze_test` and `e2e_story` |
| `alembic current` for `amaze`, and with `DATABASE_URL` set to `amaze_eval` | `0019 (head)` on both |
| `agents.evaluate_mappings` | unchanged: `25 of 31 (80.6%)` |
| `npm --prefix apps\backend-ui run build`, the same for consumer-ui, `.\scripts\dev.ps1` | exit 0; three `OK` lines and health `ok` |

## 14. Not in phase 7

- A person releasing a held load, re-running certification, per-feed thresholds.
- Granting access (no access-request workflow; the route is text, as in phase 2).
- Step 20 from the routing `create_product` outcome (the backend team is told, as today); a product's elements changing after creation; a consumer view of the records.
- Type changes in step 12 (assumption 11); delivery deadlines (assumption 8); a start-up sweep of stale loads.
- Making a `duplicate_linked` requirement Ready before the feed's next release (assumption 29).
- Value drift (16), exception investigation (14); the Playwright run against Azure and `AzureOpenAIProvider` (phase 8).

## 15. Risks

1. **Parallel runs can expose timing tests** (`run_soon` delay, the 0.2 s sandbox limit, the concurrency events). Three clean runs are required; a flaky failure stops the builder (reproduced, root cause written).
2. **Connections:** N workers each with pools and schema-per-test engines; PostgreSQL's `max_connections` is pasted in task 1; "too many clients" stops the builder.
3. **A newer profile on a registered feed** (additive) may change what the feed page offers (profile or sign-off prompts); task 12 checks it in the browser.
4. **Resend is byte-identical only:** a resent file with one changed byte is a new delivery.
5. **The 90 threshold is a guess**; it is configuration, shown with each certification.
6. **The e2e API runs the real scheduler:** the daily job at 06:00 UTC (11:30 here) could fire during a run. **Closed (2026-11-17):** `e2e/serve_api.py` sets `DAILY_JOB_ENABLED=false` (assumption 38).
7. **Chromium is downloaded** by `npx playwright install` (network, user cache).
8. **The bond story replays only on `e2e_story`:** `amaze`'s Bond terms is registered and its demo product keeps `coupon_rate` (assumption 36).
9. **The stale limit (30 min)** abandons a load still running after 30 minutes; the late writer then writes nothing (R4).

## 16. Carried forward

**Phase 7.5:** 1. the five phase 6 polish lines of assumption 46, and the lines this phase appends.
**Phase 8:** 2. the Playwright test against the Azure deployment (`BUILD_PLAN.md`); 3. step 12 and 15 move to the queue with the other background work; 4. a delivery deadline per feed for `late_alert`.

## 17. Results and deviations (recorded during implementation)
**Deviation 1 (task 1; accepted 2026-11-17):** assumption 3(b)'s `'public'` is also a visibility value on about 30 test lines, so the scan flags `'public'` only beside a schema keyword or cast (`nspname`, `schemaname`, `table_schema`, `schema_name`, `::regnamespace`), plus `w_gw*`, `search_path=` and `SET search_path`.
**Deviation 2 (task 1):** the lock key is `concat(current_schema(), ':', key)`, not `||`: with no schema in the search_path `||` gives NULL and PostgreSQL then takes no lock. **Deviation 3:** to keep the plan at 300 lines the blank line after six headings (2, 3, 7, 9, 13, 17) was removed; no text changed.
**Task 1 results.** Serial before: 198.73 / 250.98 / 234.52 s (median **234.52**). One run each: `-n 4` 122.80, `-n 6` 116.11, `-n 8` 120.23 s, so **N = 6**. `-n 6` after: 142.84 / 169.16 / 163.31 s (median **163.31**, below 234.52); 2008 tests, 0 warnings; `max_connections` 100. **Ceiling: median of three at or below 200 s; no single run above 245 s** (1.2 and 1.5 × 163.31, rounded up to 5). Before assumption 2a one `-n 4` run in two deadlocked. Removal proofs: all workers on `w_shared` → `assert ('w_shared', 'w_shared') == ('w_gw0', 'w_gw0')`; key without the schema → `LockNotAvailable: canceling statement due to lock timeout` in `test_the_same_lock_in_two_schemas_has_two_keys_and_nobody_waits[source_lock]` and `[glossary_lock]`.
**Task 3 results (checkpoint 1).** `0019` on the test schemas only. Task 2's tests GREEN; with `test_repo_loads`, `test_repo_products`, `test_domain_models`, `test_worker_isolation`: 345 passed. Removal proofs (guard taken out of the migration; test; failure): G12 `test_a_go_live_test_load_has_no_arrival[content_sha256]` `DID NOT RAISE DBAPIError` (10 fail); G13 `test_a_resent_delivery_is_quarantined` `a received load moves only to loaded or failed_rolled_back, not quarantined` (36 fail); G14 `test_the_trust_score_is_the_formula` `DID NOT RAISE` (13); G15 `test_a_product_needs_a_released_load[loaded]` `DID NOT RAISE` (7); G16 `test_a_structure_change_is_the_fourth_glossary_run_reason` `violates check constraint "ck_glossary_runs_reason"`.
**Deviation 4 (open, stop):** G13 refuses a scheduled load with no arrival, and step 12 and the job wiring come in tasks 4 and 7, so 11 tests fail until then (`test_jobs` 5, `test_api_amaze_go_live` 2, `test_commit_visibility` 2, `test_engine_run` 1, `test_concurrency` 1). **Deviation 5 (open, stop):** 9 tests in `test_db_load_guards.py` (8) and `test_db_go_live_guards.py` (1) test 0018's G8 (a test load goes held or released; `loaded_load(purpose="scheduled")` has no arrival); neither file is in section 11.
