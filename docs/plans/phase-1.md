# Phase 1 plan — Domain, repositories and glossary

**Status:** approved on 2026-11-13 with three changes (identifier names cusip, sedol and figi allowed, so the seed has 33 elements; glossary keys enforced in the database; future statuses noted in section 15). Those changes are applied below. You will upgrade PostgreSQL to 18.7 in place before task 4.
**Date:** 2026-11-13. **Branch:** `phase-1`, created from `main`. **Scope:** phase 1 of `BUILD_PLAN.md`, plus four items carried over from phase 0. Local only; nothing is pushed.

## 1. Goal and "done when"

Phase 1 is done when all five are true:

1. A person can browse the seeded glossary in backend-ui: by domain, by search, and one element in detail.
2. A person can open the feed registry in backend-ui and sees an explicit "no feeds yet" state.
3. The ten models exist with tables and repositories: Requirement, Feed, GlossaryElement, Mapping, Rule, Load, Product, Proposal, Approval, and Notification (on the phase 0 table).
4. `pytest` passes with nothing skipped and no warnings.
5. Phase 0 items a, b and c are fixed and item d is recorded for phase 3 (section 5).

## 2. State at the start (checked on 2026-11-13)

| Item | Found |
|---|---|
| Git | `main` was still the empty baseline. I fast-forwarded it to `phase-0` (your decision) and created `phase-1` from it. No remote exists |
| Python / PostgreSQL | 3.14.5 in `.venv`; server 18.6 accepting connections |
| Tests | 183 passing at the end of phase 0 |
| Dev ports 8000, 5173, 5174 | Free (the phase 0 windows are closed) |
| Plugins | Superpowers, Karpathy, i-have-adhd, Octopus still not loaded. I follow the same flow by hand |

## 3. Decisions you made

1. **Git:** fast-forward `main` to `phase-0`, then branch `phase-1` from `main`.
2. **Table detail: minimal now.** Identity, links, status and timestamps, plus only the fields `CLAUDE.md` and `BUILD_PLAN.md` name. Later phases add columns by migration.
3. **Decisions live in the approvals table only.** `proposal_log` stays the record of each model call. Its decision columns and `record_decision()` are removed by a new migration. A database view shows rule 4's fields per proposal.
4. **Mapping (level 1)** is one source field to one glossary element, with no transformation.

## 4. Assumptions — veto any by number

**Structure**

1. **One new folder: `core/repositories/`.** It holds `tables.py` (every table class) and one repository module per model. Pydantic models stay in `core/domain/`.
2. **The `proposal_log` table class moves into `core/repositories/tables.py`.** Reason: other tables now point at it, and the run engine must be able to load the tables without importing `ports.llm`. `ProposalLog.record()` stays in `core/proposal_log.py`.
3. **The import-boundary test also covers `core/domain` and `core/repositories`**, because the run engine will import both.
4. **Three migrations**, one per group of tables: `0002` proposals and approvals, `0003` glossary, `0004` requirements, feeds, mappings, rules, loads and products.

**Data rules**

5. **Versioned configuration is append-only rows.** `glossary_elements`, `mappings` and `rules` each carry a `version` number; the current one is the highest. A change inserts a new row.
6. **The database itself refuses UPDATE and DELETE** on those three tables, and on `approvals` and `proposal_log`, through a trigger. This makes rule 7 ("never edited in place") and "a decision is never overwritten" true even for hand-typed SQL.
7. **A mapping or rule row can only be created from an approved proposal of the matching kind.** The repository checks it. This is rule 1 ("only approved proposals become versioned configuration"). Glossary elements follow the same rule, except the seed.
8. **Approval rules:** roles decide in the order the proposal lists them; one decision per step; the same person cannot decide two steps of one proposal (the checker must not be the steward); the first rejection ends it; nothing can be decided after that.
9. **Proposal kinds** are the seven agents that have an approver: scope, source list, profile summary, glossary element, mapping, rule, review summary. The read-only DiscoveryAssistant creates no proposal.
10. **Status values are the names already in `CLAUDE.md`:** requirement `defined`, `onboarding`, `ready`, `access_restricted`, `rejected`; feed stage `candidate`, `registered`, `rejected`; load `received`, `held`, `failed_rolled_back`, `released`; proposal `pending`, `approved`, `rejected`. Later phases extend the lists by migration.
11. **Links:** people are stored as text user ids (as in phase 0). A glossary element is referenced by its `key`, not by a foreign key, because one element has several version rows. A trigger on `mappings` refuses an `element_key` that has no row in `glossary_elements` (section 6), and the repository checks it first to give a named error.
12. **Repositories offer create, get and list, plus only the status changes named in section 7.** No general update or delete.

**Glossary**

13. **Glossary element fields:** key, name, domain, data type, definition, aliases, version. Data type is the one field not named in `CLAUDE.md` or `BUILD_PLAN.md`; a glossary is not usable without it. The six data types are string, integer, decimal, date, datetime and boolean.
14. **The seed is the 33 elements in section 8.** The names of identifier types are allowed, including `cusip`, `sedol` and `figi` (rule 6 forbids real data, not the names of identifier types). Banned: real identifier values (no ISIN, CUSIP, SEDOL, FIGI, LEI or ticker belonging to a real instrument or issuer, even in a definition's example), vendor field codes, and vendor or client names. I write every definition myself.
15. **The seed is loaded by an explicit command, `python -m core.seed`.** It is safe to run again and never changes an existing element. It is not an Alembic data migration.

**API and UI**

16. **API paths are under `/api/amaze/`**, in a new `apps/api/routers/amaze.py`. Read-only. Any signed-in backend user may read; the requester gets 403. No paging (33 rows).
17. **backend-ui navigation uses the URL hash** (`#/glossary`, `#/feeds`). No router or UI library is added; no new npm package.
18. **The UI is checked by the type-check build and by my scripted browser run with screenshots.** The committed Playwright test stays in phase 7, as `BUILD_PLAN.md` says.

**Carried items**

19. **Health gains an `unavailable` list.** Any request that reaches a stub adapter returns 503, not 500, and the API starts even when the scheduler is a stub (section 5a).
20. **pytest treats every warning as an error** once the Starlette test client is gone, so the warning cannot come back unnoticed.
21. **I add two bullets to phase 3 in `BUILD_PLAN.md`** for item d, so the phase 3 plan cannot miss them.
22. **End-of-phase review** is mine, against `CLAUDE.md`'s rules and this plan.

## 5. Phase 0 items carried in

### a. `/api/health` and stub adapters

What happens today, checked by running the API once per stub:

| Stub selected | Today | After the fix |
|---|---|---|
| `SECRETS_PROVIDER=key_vault` | health returns 500 | 503, `unavailable: ["secrets"]`, `database: "not_configured"` |
| `SCHEDULER_PROVIDER=azure_functions` | the API does not start | starts; 503, `unavailable: ["scheduler"]` |
| `IDENTITY_PROVIDER=entra_id` | health 200 "ok"; `/api/me` returns 500 | 503 on both, `unavailable: ["identity"]` |
| LLM, storage or notify stub | health 200 "ok" although the adapter cannot work | 503, the adapter listed in `unavailable` |

The fix:

1. Every stub class declares `is_stub = True`. A test requires it on all six stubs and forbids it on the local adapters.
2. Health builds the six selected adapters and lists every stub in `unavailable`. `status` is `ok` only when the database is ok and the list is empty; otherwise 503.
3. Start-up does not start a stub scheduler.
4. One handler turns the stubs' `NotImplementedError` into a 503 that names the reason, for every endpoint.

```json
{"status":"error","database":"ok",
 "adapters":{"llm":"azure_openai","storage":"local","scheduler":"local","identity":"dev","secrets":"env","notify":"inapp"},
 "unavailable":["llm"]}
```

Checked by one test per selector (six), asserting 503, the adapter named, and never 500; plus the all-local case returning 200 with `"unavailable": []`. Both UIs show unavailable adapters in red.

### b. The `httpx2` warning

1. **Exact text:** ``StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.``
2. **Source:** `starlette/testclient.py`, lines 28 to 44, Starlette 1.10.0. The module tries `import httpx2 as httpx`, falls back to `import httpx`, and warns as it is imported. FastAPI's `TestClient` is the same module. The warning class extends `UserWarning`, which is why a `DeprecationWarning` filter did not match it.
3. **`httpx2` is not adopted**, as you decided.
4. **Fix:** stop importing Starlette's test client. `tests/test_api.py` calls the app in-process with `httpx.AsyncClient` and `httpx.ASGITransport`, runs start-up and shutdown with `app.router.lifespan_context(app)`, and runs as async tests under anyio's pytest plugin.
5. **Packages:** `httpx` (already pinned) and `anyio` (already installed as a dependency of Starlette and httpx; I pin `anyio==4.16.0` in `requirements-dev.txt` because the tests now use it directly). Nothing new is downloaded.
6. **Already checked** in a throwaway script outside the repository: health and `/api/me` return 200 with every warning turned into an error, and `starlette.testclient` is never imported.
7. The warning filter in `pyproject.toml` is removed and replaced by `filterwarnings = ["error"]`.

Checked by the full test run showing no warnings, and by a test that fails if any file under `tests/` imports `starlette.testclient` or `fastapi.testclient`.

### c. Proof that the storage tests catch real bugs

For each break below I change the code, run `tests/test_port_storage.py`, record which test fails, and restore the file with `git checkout`. At the end `git status` must be clean.

| # | Deliberate break in the storage code | A test should fail on |
|---|---|---|
| 1 | `put` returns the wrong reference | round trip |
| 2 | `put` leaves the temp file and never moves it into place | round trip |
| 3 | `get` returns empty bytes for a missing blob | missing blob |
| 4 | `exists` always returns true | missing blob |
| 5 | `list_keys` ignores the prefix | list with prefix |
| 6 | key check stops rejecting `..` | key cannot escape root |
| 7 | key check stops rejecting `\` | key cannot escape root |
| 8 | `list_keys` also returns leftover temp files | (I expect no test covers this) |
| 9 | the factory resolves a relative root against the current folder, not the repository | factory test |

I expect some breaks to go unnoticed: my reading says 6 (a second guard catches it), 8 and 9 (the tests run from the repository root). **For every break that no test catches, I write a test first, show it failing against the broken code, then restore the code.** The checkpoint report lists the real result for all nine.

### d. Recorded for phase 3

See section 18. Phase 1 already delivers half of the first point: `proposals.proposal_log_id` is required, so the database refuses a proposal that has no logged model call.

## 6. Data model

Every table has `id` (uuid) and `created_at`. User ids are text. **Append-only** means the trigger from assumption 6 applies.

| Table | Columns beyond `id`, `created_at` | Rules |
|---|---|---|
| `proposal_log` (phase 0, changed) | agent_name, provider, model, model_version, prompt_id, prompt_version, input_ref, output, raw_ref, latency_ms | **Append-only.** Loses status, decided_by, decided_at, decision_note |
| `proposals` | proposal_log_id, kind, payload (json), autonomy, required_approver_roles (ordered list), status, decided_at, requirement_id (optional), feed_id (optional) | kind is one of seven; autonomy is A1 or A2; at least one approver role; status is one of three |
| `approvals` | proposal_id, step, role, decided_by, decision, note | **Append-only.** One row per proposal and step; decision is approved or rejected |
| `glossary_elements` | key, version, name, domain, data_type, definition, aliases (list), created_by, proposal_id (empty for the seed) | **Append-only.** Unique key and version; data type is one of six |
| `requirements` | requester_id, title, description, status | status is one of five |
| `feeds` | code (unique), name, stage, requirement_id (optional), owner_id, source_ref, registered_at | stage is one of three; **a registered feed must have an owner** (the `no_owner` outcome, enforced by the database) |
| `mappings` | feed_id, source_field, element_key, version, proposal_id | **Append-only.** Unique feed, source field and version. **A trigger refuses an `element_key` with no row in `glossary_elements`** |
| `rules` | feed_id, rule_key, version, sql, description, proposal_id | **Append-only.** Unique feed, rule key and version |
| `loads` | feed_id, file_ref, received_at, status | status is one of four |
| `products` | code (unique), name, description, domain, requirement_id (optional) | none yet |
| `notifications` (phase 0, unchanged) | recipient, subject, body, link, read_at | none |

**View `proposal_audit`** (rule 4 in one place): one row per proposal with the agent name, provider, model and version, prompt id and version, input reference, output, the proposal's payload and status, and the list of decisions (step, role, who, decision, note, when).

**Migration `0002` and existing data.** It drops the decision columns from `proposal_log`. It refuses to run if any row there already has a decision, because that decision would be lost. The local databases have none.

**Glossary key check on `mappings`.** A `BEFORE INSERT` trigger looks up `NEW.element_key` in `glossary_elements` and raises an error naming the key if there is no row. Checking on insert is enough: `mappings` rows are never updated, and `glossary_elements` rows are never deleted (both are append-only). Checked by a test that inserts a mapping with an unknown key by raw SQL, bypassing the repository, and expects the database to refuse it; and one with a known key that succeeds.

All of this uses only features present since PostgreSQL 14 (tables, `jsonb`, check constraints, a view, `plpgsql` triggers).

## 7. Domain models and repositories

**Pydantic models** in `core/domain/`: `GlossaryElement`, `Requirement`, `Feed`, `Mapping`, `Rule`, `Load`, `Product`, `Proposal`, `Approval`, `Notification`. They validate what the database cannot say simply: keys and codes are lower-case snake_case, roles are among the seven, the approver list is not empty.

**Repositories** in `core/repositories/`, each taking a database session:

| Repository | Methods |
|---|---|
| `GlossaryRepository` | `list_current(domain, q)`, `get_current(key)`, `history(key)`, `domains()`, `add_version(...)` |
| `ProposalRepository` | `create(...)`, `get(id)`, `list(status, kind)`, `record_decision(id, user, decision, note)`, `approvals(id)`, `audit(id)` |
| `RequirementRepository` | `create(...)`, `get(id)`, `list(requester_id)`, `set_status(id, status)` |
| `FeedRepository` | `create_candidate(...)`, `get(id)`, `get_by_code(code)`, `list(stage)`, `register(id, owner_id)` |
| `MappingRepository` | `add_version(feed_id, source_field, element_key, proposal_id)`, `current_for_feed(feed_id)`, `history(...)` |
| `RuleRepository` | `add_version(feed_id, rule_key, sql, description, proposal_id)`, `current_for_feed(feed_id)`, `history(...)` |
| `LoadRepository` | `create(...)`, `get(id)`, `list_for_feed(feed_id)`, `set_status(id, status)` |
| `ProductRepository` | `create(...)`, `get(id)`, `get_by_code(code)`, `list(domain)` |
| `NotificationRepository` | `list_for(recipient)`; writing stays with the Notifier port |

**Named errors** (never a bare error for an expected case): `ProposalNotFound`, `ProposalAlreadyDecided`, `NotAnApprover` (wrong role for this step), `SameApproverTwice`, `ProposalNotApproved`, `WrongProposalKind`, `UnknownGlossaryElement`, `FeedNotFound`, `DuplicateFeedCode`, `FeedHasNoOwner`, `DuplicateProductCode`.

**What replaces `ProposalLog.record_decision()`:** `ProposalRepository.record_decision()`. It locks the proposal, applies the rules in assumption 8, inserts one `approvals` row, and sets the proposal to `rejected`, `approved` (after the last step) or leaves it `pending`.

How this is checked: one test file per repository. Each tests the normal path and every rule and named error above. `test_append_only.py` tries UPDATE and DELETE on the five append-only tables and expects the database to refuse.

## 8. Glossary seed

File `fixtures/glossary/enterprise_glossary.json`: 33 elements, each with key, name, domain, data type, definition and aliases.

**Security master (19)**

| Key | Type | Key | Type |
|---|---|---|---|
| `instrument_id` | string | `currency` | string |
| `isin` | string | `country_of_risk` | string |
| `cusip` | string | `exchange_mic` | string |
| `sedol` | string | `ticker` | string |
| `figi` | string | `issue_date` | date |
| `instrument_name` | string | `maturity_date` | date |
| `asset_class` | string | `coupon_rate` | decimal |
| `instrument_type` | string | `lot_size` | integer |
| `issuer_name` | string | `instrument_status` | string |
| `issuer_lei` | string | | |

**Pricing (14)**

| Key | Type | Key | Type |
|---|---|---|---|
| `price_date` | date | `mid_price` | decimal |
| `close_price` | decimal | `volume` | integer |
| `open_price` | decimal | `price_currency` | string |
| `high_price` | decimal | `price_source` | string |
| `low_price` | decimal | `price_type` | string |
| `bid_price` | decimal | `accrued_interest` | decimal |
| `ask_price` | decimal | `yield_to_maturity` | decimal |

Each element gets two or three aliases (for example `isin`: `isin_code`; `close_price`: `closing_price`, `close`). Phase 5's `duplicate_alias` outcome needs them.

**Loading:** `.\.venv\Scripts\python -m core.seed` inserts version 1 of every missing element with `created_by = "seed"` and prints how many it inserted and how many were already there. It never changes an existing element.

Checked by `test_glossary_seed.py`:

1. The file is valid: unique keys, only the two domains, only the six data types.
2. No key or alias appears twice across the whole glossary, ignoring case.
3. No real data: no string in the file is shaped like a real identifier value (ISIN, CUSIP, SEDOL, FIGI or LEI pattern), and none of a fixed list of vendor names or vendor field-code patterns appears. The identifier type names themselves (`cusip`, `sedol`, `figi` and so on) are allowed as keys, names and aliases.
4. Seeding twice inserts 33, then 0.

## 9. API

| Endpoint | Returns |
|---|---|
| `GET /api/amaze/glossary/domains` | each domain with its element count |
| `GET /api/amaze/glossary/elements?domain=&q=` | current elements; `q` matches key, name, alias or definition, ignoring case |
| `GET /api/amaze/glossary/elements/{key}` | one element and its version history; 404 if unknown |
| `GET /api/amaze/feeds?stage=` | feeds, newest first; an empty list in phase 1 |
| `GET /api/health` | as today, plus `unavailable` (section 5a) |

- No signed-in user: 401. Requester: 403. Any of the six backend roles: allowed.
- Each request gets its own database session.

Checked by `test_api_amaze.py`: the seeded list, the domain filter (19 and 14), search, the detail page and its 404, the empty feed list, the stage filter, and 401 and 403.

## 10. backend-ui

1. **Shell:** a header with the app title, three links (Status, Glossary, Feed registry) and the dev-user picker. The view follows the URL hash.
2. **Status:** the phase 0 content. Unavailable adapters are shown in red.
3. **Glossary:**
   - a domain filter showing the counts (All 33, Security master 19, Pricing 14);
   - a search box;
   - a table with name, key, domain, type, definition and aliases;
   - a click on a row opens its details (version, created by, history);
   - "No elements match" when a search finds nothing.
4. **Feed registry:**
   - a stage filter (All, Candidate, Registered);
   - a table with code, name, stage, owner, source and created;
   - the explicit empty state: "No feeds yet."
5. **Errors are shown, not hidden:** API unreachable, not signed in, not allowed.
6. **consumer-ui** changes only to show unavailable adapters on its status line.

Checked by `npm run build` in both projects and by loading each view in a browser with screenshots.

## 11. Files

```
docs/plans/phase-1.md                      this plan
BUILD_PLAN.md                         edit two bullets in phase 3 (item d)
CLAUDE.md                             edit layout: core/repositories/
README.md                             edit seed command, new views
pyproject.toml                        edit filterwarnings = ["error"]
requirements-dev.txt                  edit pin anyio
migrations/
  models.py                           edit
  versions/0002_proposals_approvals.py     proposals, approvals, proposal_audit view,
                                           append-only trigger, proposal_log decision columns removed
  versions/0003_glossary.py
  versions/0004_feeds_and_configuration.py requirements, feeds, mappings, rules, loads, products
core/
  proposal_log.py                     edit record() only; table class moved
  seed.py                             new  python -m core.seed
  domain/       glossary.py  requirement.py  feed.py  configuration.py (Mapping, Rule)
                load.py  product.py  proposal.py (Proposal, Approval)  notification.py
  repositories/ __init__.py  tables.py  errors.py
                glossary.py  proposals.py  requirements.py  feeds.py
                configuration.py (mappings, rules)  loads.py  products.py  notifications.py
ports/
  llm/azure_openai.py  storage/azure_blob.py  scheduler/azure_functions.py
  identity/entra_id.py  secrets/key_vault.py  notify/email_teams.py     edit is_stub = True
apps/api/
  main.py  deps.py  routers/health.py edit
  routers/amaze.py                    new
apps/backend-ui/src/
  App.tsx  api.ts                     edit
  views/StatusView.tsx  views/GlossaryView.tsx  views/FeedRegistryView.tsx   new
apps/consumer-ui/src/
  App.tsx  api.ts                     edit unavailable adapters only
fixtures/glossary/enterprise_glossary.json new
tests/
  test_api.py                         rewritten with httpx
  test_api_amaze.py  test_no_starlette_testclient.py                         new
  test_domain_models.py  test_append_only.py  test_glossary_seed.py          new
  test_repo_glossary.py  test_repo_proposals.py  test_repo_requirements.py
  test_repo_feeds.py  test_repo_configuration.py  test_repo_loads.py
  test_repo_products.py  test_repo_notifications.py                          new
  test_proposal_log.py  test_migrations.py  test_azure_stubs.py
  test_import_boundaries.py  test_port_storage.py                            edit
```

## 12. Tasks, in order

Each task: tests first and shown failing, then code, then its check, then one commit. I stop and report at the three checkpoints and continue without stopping between them.

| # | Task | Tests written first | Check |
|---|---|---|---|
| 1 | Storage proof (5c) | new tests for every break no test catches | table of nine breaks and their results; `git status` clean |
| 2 | Replace the Starlette test client (5b) | `test_no_starlette_testclient.py`; `test_api.py` rewritten | pytest passes with warnings as errors |
| 3 | Health and stubs (5a) | six stub tests, all-local test, `is_stub` test | pytest. **CHECKPOINT 1:** the break table, the health result for all six stubs, the pytest summary |
| 4 | Proposal and Approval: models, migration `0002`, repository, audit view, append-only trigger; `proposal_log` change; boundary test extended | `test_repo_proposals.py`, `test_append_only.py`; `test_proposal_log.py`, `test_migrations.py`, `test_import_boundaries.py` updated | pytest; upgrade, downgrade, upgrade |
| 5 | GlossaryElement: model, migration `0003`, repository | `test_repo_glossary.py` | pytest |
| 6 | Glossary seed file and `core.seed` | `test_glossary_seed.py` | pytest; the command inserts 33, then 0 |
| 7 | Requirement and Feed: models, migration `0004` (first part), repositories | `test_repo_requirements.py`, `test_repo_feeds.py` | pytest |
| 8 | Mapping and Rule: models, tables, repositories | `test_repo_configuration.py` | pytest |
| 9 | Load, Product, Notification: models, tables, repositories | `test_repo_loads.py`, `test_repo_products.py`, `test_repo_notifications.py`, `test_domain_models.py` | pytest; `alembic current` shows `0004 (head)`. **CHECKPOINT 2:** pytest output, `alembic current`, seed output |
| 10 | API: session per request, backend-user guard, `routers/amaze.py` | `test_api_amaze.py` | pytest |
| 11 | backend-ui shell and Status view; consumer-ui status line | none | `npm run build` in both |
| 12 | backend-ui Glossary view | none | build; view loaded in a browser |
| 13 | backend-ui Feed registry view | none | build; view loaded in a browser |
| 14 | `README.md`, `CLAUDE.md` layout, `BUILD_PLAN.md` phase 3 bullets | none | documents read back |
| 15 | Review against `CLAUDE.md`'s seven rules and this plan | none | findings recorded in this plan. **CHECKPOINT 3:** full section 13 results and screenshots. Phase 2 is not started |

Migration `0004` is written across tasks 7 to 9 and is complete at checkpoint 2. `test_domain_models.py` grows with each of tasks 4 to 9, as each model is added.

## 13. Commands that prove phase 1 is done

Run from `F:\amaze-cm-poc` in Windows PowerShell.

```powershell
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
.\.venv\Scripts\python -m alembic upgrade head
.\.venv\Scripts\python -m core.seed
.\.venv\Scripts\python -m pytest -v
.\.venv\Scripts\python -m alembic current
npm --prefix apps\backend-ui run build
npm --prefix apps\consumer-ui run build
.\scripts\dev.ps1
```

| Command | Expected result |
|---|---|
| `core.seed` | first run: 33 inserted; later runs: 0 inserted, 33 already present |
| `pytest -v` | all pass; 0 failed, 0 skipped, 0 warnings |
| `alembic current` | `0004 (head)` |
| `npm ... run build` (twice) | exit code 0 |
| `dev.ps1` | three `OK` lines and health `ok` |

Then in the browser, signed in as any backend dev user:

| URL | Expected result |
|---|---|
| `http://localhost:5173/#/glossary` | 33 elements; Security master shows 19, Pricing 14; searching `isin` leaves one row; a click shows its details |
| `http://localhost:5173/#/feeds` | "No feeds yet." |
| `http://localhost:8000/api/health` | the phase 0 result plus `"unavailable":[]` |

Stub check, with the three windows closed:

```powershell
$env:SCHEDULER_PROVIDER = 'azure_functions'
.\.venv\Scripts\python -m uvicorn apps.api.main:app --port 8000
# in a second window:
curl.exe -s -i http://127.0.0.1:8000/api/health     # expect: HTTP/1.1 503 and "unavailable":["scheduler"]
# back in the first window, after stopping the API with Ctrl+C:
Remove-Item Env:SCHEDULER_PROVIDER
```

## 14. Not in phase 1

Steps and `StepResult`, agents, any screen that creates or changes data, the approval screens, requested elements on a requirement, licence and visibility fields on a feed, load stages, product entitlement, consumer-ui features, DuckDB, the Playwright test.

## 15. Risks

1. **Tables are designed before most of them are used.** Minimal columns limit the rework, but the status lists in assumption 10 are my reading of `CLAUDE.md` and may need to change in the phase that uses them. **Phases 3 and 7 will add statuses by migration**, for example `awaiting_source_choice` on requirements (phase 3) and `quarantined` on loads (phase 7, for `resent_quarantined`). Each status list is a check constraint, so adding a value is a small migration that drops and recreates that one constraint.
2. **Append-only triggers block hand fixes.** A wrong row in a configuration table can only be corrected by a new version. That is the intent, but it will surprise anyone who tries a quick UPDATE.
3. **Migration `0002` removes columns from a phase 0 table.** It refuses to run when a decision would be lost (section 6). The downgrade puts the columns back, empty.
4. **The API tests become async** and share one rolled-back database connection between the test and the request. They run one after another, never at the same time. If this proves unstable, each request gets its own session on that connection.
5. **Warnings as errors** may surface a warning from a third-party package that I cannot fix. If so, that one warning is listed by name with a reason, as before.
6. **Search matches aliases by text.** Fine for 33 elements; a large glossary would need an index.
7. **PostgreSQL 18.6 to 18.7 in place, before task 4.** A minor upgrade keeps the data files, so the databases, the `amaze` role and its password stay. I re-check the server version and run the full test suite before starting task 4.

## 17. Results and deviations (recorded during implementation)

### Task 1: storage break results

Before task 1, three of nine breaks went unnoticed: 6, 8 and 9, as predicted. Reading the code for break 8 also showed a **real data-loss bug from phase 0**: `put("report")` wrote its temp file to `report.tmp`, which is a valid key. An existing blob `report.tmp` was overwritten and then renamed away. For the same reason, every key ending in `.tmp` was hidden from `list_keys`.

**Fix (tests written first and shown failing):**
- temp files are now `.<name>.<random>.tmp`;
- keys cannot have a segment starting with `.`, a rule `check_key` applies to every adapter, so the Azure adapter will accept the same keys;
- `list_keys` skips dot-names;
- a failed `put` removes its temp file.

After the fix, all ten breaks are caught (break 10 restores the phase 0 temp naming):

| # | Break | Caught by |
|---|---|---|
| 1 | `put` returns the wrong reference | round trip |
| 2 | temp file never moved into place | round trip, overwrite, list with prefix, and three new tests |
| 3 | `get` returns empty bytes for a missing blob | missing blob |
| 4 | `exists` always true | missing blob; unfinished writes ignored |
| 5 | `list_keys` ignores the prefix | list with prefix |
| 6 | key check stops rejecting `..` and dot segments | **new:** `..` rejected inside root; dot segments rejected |
| 7 | key check stops rejecting `\` | malformed keys |
| 8 | `list_keys` returns leftover temp files | **new:** unfinished writes ignored |
| 9 | factory root relative to the current folder | **new:** root does not depend on the current folder |
| 10 | temp file named `<name>.tmp` (phase 0 design) | **new:** neighbouring `.tmp` key untouched; unfinished writes ignored |

After every break the file was restored from its saved text; `git status` showed only the intended fix.

### Before task 2: database recreated

The PostgreSQL 18.7 upgrade created a new cluster rather than upgrading in place, so the `amaze` role and both databases were gone (server log: `Role "amaze" does not exist`). You re-ran `setup_db.ps1`; `alembic upgrade head` rebuilt `0001`; 193 tests passed on 18.7 before task 2 started.

### Task 2: Starlette test client replaced

As planned in 5b. 13 API tests kept with the same assertions, now async over `httpx.ASGITransport`; one guard test added. `pytest` runs with every warning as an error; `starlette.testclient` is never imported (checked with `python -X importtime`).

### Task 3: health with stub adapters

The same in-process check run before and after the fix:

| Selected stub | Before | After: health | After: `/api/me` |
|---|---|---|---|
| none (all local) | 200 | 200, `unavailable: []` | 200 |
| `SECRETS_PROVIDER=key_vault` | health 500 | 503, `database: not_configured`, `unavailable: ["secrets"]` | 200 |
| `SCHEDULER_PROVIDER=azure_functions` | API did not start | 503, `unavailable: ["scheduler"]` | 200 |
| `IDENTITY_PROVIDER=entra_id` | health 200; `/api/me` 500 | 503, `unavailable: ["identity"]` | 503, `Azure adapter: implement after move` |
| `LLM_PROVIDER=azure_openai` | health 200 | 503, `unavailable: ["llm"]` | 200 |
| `STORAGE_PROVIDER=azure_blob` | health 200 | 503, `unavailable: ["storage"]` | 200 |
| `NOTIFY_PROVIDER=email` | health 200 | 503, `unavailable: ["notify"]` | 200 |

Health builds the six selected adapters on each call to find the stubs. Each is cheap to build (the dev identity reads a 7-line file).

### Tasks 4 to 9: deviations and findings

1. **More named errors than section 7 listed:**
   - `ProposalLogEntryNotFound`, `ProposalForAnotherFeed` (an approved proposal cannot be used for another feed's configuration);
   - `RequirementNotFound`, `FeedNotACandidate`, `FeedNotRegistered` (loads arrive only for registered feeds);
   - `LoadNotFound`, `ProductNotFound`.
2. **More rules enforced by the database than planned:**
   - one decision per person per proposal (`uq_approvals_proposal_id_decided_by`);
   - a glossary element without a proposal must be the seed (`ck_glossary_elements_proposal_unless_seed`);
   - a proposal's `decided_at` must match its status;
   - mapping and rule rows require `proposal_id`.
3. **Bug found and fixed (task 7): ordering within a transaction.** `now()` is the transaction's start time, so rows inserted together had equal `created_at` and "newest first" came back in random order. The requirements ordering test failed 8 of 12 runs. Tables listed newest first (requirements, feeds, mappings, rules, loads, products, and by `ALTER` in `0004` proposals and notifications) now use `clock_timestamp()`. 0 failures in 12 runs after the fix; the full suite was run three more times at checkpoint 2.
4. **The test fixture rebuilds the `amaze_test` schema from scratch** (drop and recreate `public`, then `upgrade head`) instead of downgrading. Reason: `0004` was written across tasks 7 to 9, and the task 7 version's leftovers broke the next run. Downgrades stay covered by `test_upgrade_downgrade_upgrade`. `0004` reached the application database only after task 9 was complete.
5. **SQLAlchemy 2.1 deprecates `distinct(column)` for DISTINCT ON.** Warnings-as-errors caught it in task 5; the code uses `postgresql.distinct_on`.
6. **Glossary-key trigger proven:** with the `CREATE TRIGGER` line removed from `0004`, the raw-SQL test inserting an unknown key failed (`DID NOT RAISE`); restored with the Edit tool.
7. **`core/domain/common.py`** added for the two shared field types (`SnakeName`, `NonBlank`).

### Before task 10: two database gaps closed (your request after checkpoint 2)

Both added to `0004`, tests written first and shown failing (14 refusal tests did not raise before).

1. **TRUNCATE refused on all five append-only tables.** TRUNCATE skips row-level triggers, so each table gets a statement-level `BEFORE TRUNCATE` trigger using the same `amaze_refuse_update_delete()` function. Tested with `TRUNCATE <table> CASCADE`.
2. **Approval guard on insert** for `mappings`, `rules` and `glossary_elements`: the proposal must be `approved`, of the matching kind, and have at least one `approved` row in `approvals`. Only the glossary seed (`created_by = 'seed'`) is exempt. Raw-SQL tests per table: a pending proposal, an approved proposal of another kind, and a status forged by `UPDATE proposals SET status = 'approved'` with no approval row are each refused; a properly approved proposal is accepted; a seed element needs no proposal.
3. **Findings while building it:**
   - PL/pgSQL does not short-circuit `AND`, so `TG_TABLE_NAME = 'glossary_elements' AND NEW.created_by = 'seed'` failed on `mappings`, which has no `created_by` column. The check is nested.
   - The guard fires before `ck_glossary_elements_proposal_unless_seed`. That constraint stays as a second guard; one test's expected message changed.
4. **`0004`'s downgrade uses `DROP ... IF EXISTS`** for the new triggers, because the application database held the task 9 build of `0004` without them.
5. **Application database, as you asked:**

```
alembic downgrade 0003   Running downgrade 0004 -> 0003   exit 0
alembic upgrade head     Running upgrade 0003 -> 0004     exit 0
alembic current          0004 (head)
python -m core.seed      glossary seed: 0 inserted, 33 already present
```

The 14 triggers are present afterwards: 5 append-only, 5 no-truncate, 3 approval guards, 1 glossary key. The suite is at 346 passed.

## 18. Carried forward to phase 3

These two points are also added to phase 3 in `BUILD_PLAN.md` (task 14).

1. **The agent base class must log every proposal itself.** `agents/base.Agent.run()` is the only public entry point. It calls the `LLMProvider`, then `ProposalLog.record()`, then creates the proposal rows linked to that log row, and only then returns. A subclass supplies the prompt and turns the output into proposals; it cannot replace `run()`. Tests must prove:
   - a subclass that defines its own `run()` is refused when the class is created;
   - an agent run always leaves one `proposal_log` row per model call, and every proposal points at it.
2. **The HTTP connector must close its connections.** `HttpConnector` gets `close()` and works in a `with` block; the step that uses it closes it. A test asserts the connection is closed after the fetch, including when the fetch fails.
