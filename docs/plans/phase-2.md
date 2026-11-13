# Phase 2 plan — Consumption request and routing (17, routing checks, 01)

**Status:** awaiting approval.
**Date:** 2026-11-14. **Branch:** `phase-2`, created from `main` at `69f2d4d` (main fast-forwarded to `phase-1`). **Scope:** phase 2 of `BUILD_PLAN.md` only. Local only; nothing is pushed.

## 1. Goal and "done when"

Phase 2 is done when all five are true:

1. A requester searches in consumer-ui. Products holding the data are listed as available, or as "Access restricted" with the route to access (step 17).
2. "Request this data" runs the routing checks in order (reuse, compose, create product, raise requirement), and the requester sees the outcome.
3. **A search for missing data raises a requirement (step 01), and the requester sees its status on "My requests".** This is `BUILD_PLAN.md`'s done-when, proved by an API test and in the browser.
4. Every step returns a typed result: success or one of its named negative outcomes (rule 3).
5. `pytest` passes with nothing skipped and no warnings.

## 2. State at the start (checked on 2026-11-13)

| Item | Found |
|---|---|
| Git | Before phase 2, on `phase-1`: the configuration guard was tightened (`9976b92`, migration `0005`) and phase 1 plan section 18 was fixed (`69f2d4d`). `main` was fast-forwarded to `69f2d4d`; bundle `F:\amaze-phase1.bundle` (all refs, verified). `phase-2` was created from `main` |
| Tests | 374 passing |
| Database | `alembic current`: `0005 (head)` on the application database |
| Products, grants, requested elements | None exist. `products` has no owner, visibility or elements; `requirements` has no elements |
| Plugins | Superpowers, Karpathy, i-have-adhd and Octopus are still not loaded. I follow the same flow by hand |

## 3. Decisions you made

1. **Git:** guard fix and plan fix on `phase-1`, then fast-forward `main` and branch `phase-2` from it.
2. **Step 17 search is deterministic code, with no agent.** The DiscoveryAssistant waits until `agents/base.Agent.run()` exists (phase 3 or later). **This is a deviation from `CLAUDE.md`'s agent table**, recorded in section 16.
3. **Term matching:**
   - it ignores case and skips stop-words;
   - it tries the longest glossary phrase first;
   - the requester ticks which unmatched terms are real data needs, and only ticked terms are kept.
4. **`element_not_in_glossary`:**
   - the requirement is still saved, with the ticked terms, and the glossary owner is notified;
   - routing uses matched elements only;
   - if nothing matched, the outcome is `raise_requirement`.
5. **Entitlement:**
   - a product is `open` or `restricted` and has an owner;
   - `product_grants` holds grant and revoke events, and the latest event wins;
   - the route to access is text only: no access-request workflow.
6. **Coverage:**
   - reuse: one product holds every requested element;
   - compose: the fewest products that together cover the request, at most 3, chosen by a fixed rule;
   - create product: the elements are in Amaze's domains;
   - partial coverage: `raise_requirement` for the missing elements only, and the result names the products that cover the rest.
7. **Demo data** comes from `python -m core.seed --demo`, separate from the glossary seed. Tests build their own data.
8. **Every request is recorded** in `consumer_requests`, which is append-only.
9. **The "Known source?" check is phase 3.**
10. **A1 to A6 accepted.** A1 uses a Pydantic discriminated union on `outcome`.

## 4. Assumptions — veto any by number

**Structure**

1. **Migration `0006`, not `0005`.** `0005` is now the guard fix. `0006_consumption.py` holds every phase 2 table change (A3). It is written across tasks 1 to 3 and applied to the application database only at checkpoint 1. The test fixture already rebuilds the schema each run, so a half-written `0006` cannot leave leftovers (phase 1 plan, section 17, item 4).
2. **Steps live in `core/steps/`:**
   - `result.py` (shared base);
   - `matching.py` (terms to glossary elements);
   - `s17_discover.py`;
   - `routing.py`;
   - `s01_define_requirement.py`.

   The entitlement decision lives in `core/entitlement/decide.py`, which is pure code with no database access. The import-boundary test already covers `core/entitlement`.
3. **Steps never commit.** The API endpoint owns the transaction: routing, step 01, the request record and the notifications are committed together, or not at all.
4. **Steps receive their ports as arguments.** Step 01 is given the `Notifier` and the recipient user ids. The API finds the recipients with `Identity.list_users()`, filtered by role. Steps import no identity adapter.

**Matching (`core/steps/matching.py`)**

5. **One normalisation for both sides.** Lower-case, split on anything that is not a letter or digit, drop stop-words. The same function is applied to the query and to every glossary phrase. So `yield to maturity` and the query `yield to maturity` both become `yield maturity` and still match.
6. **Glossary phrases:** each current element's key (underscores become spaces), name and aliases. Each domain's label (`security master`, `pricing`) is also a phrase, so a domain word is never offered as an unmatched term.
7. **The stop-word list is a fixed tuple in code**, about 30 words: articles, prepositions, `and`, `or`, `data`, `need`, `want`, `please`, `show`, `me`, `all`, and so on. A test pins it.
8. **Longest match, left to right.** At each position, try the longest phrase first, down to one token. Consecutive tokens that match nothing form one unmatched term (`esg score` stays one term, not two).
9. **Ambiguous phrases:** if one normalised phrase belongs to two elements, the result lists both as candidates, with neither ticked, and the requester picks. A seed test proves today's 33 elements have no such collision, so this is a guard, not a demo path.

**Search (step 17)**

10. A product is a hit if it holds any matched element, or if any query token that matched no glossary element appears as a whole word in its name or description. Domain words also match a product's domain. Hits are sorted by the number of matched elements held (most first), then by name.
11. **Restricted products are shown to everyone**, each hit with its own outcome:
    - `found`: the requester may use it;
    - `not_entitled_visible`: shown with the route, "Access restricted. Ask <owner name> for access to <product name>."
12. **A search with no hits returns an empty list.** It is not a negative outcome of step 17; it is what leads to "Request this data".

**Entitlement (`core/entitlement/decide.py`)**

13. **The rule, in order:**
    1. an `open` product is entitled;
    2. the owner is entitled;
    3. if the user's own id has grant or revoke events, the latest one decides;
    4. otherwise, entitled if the latest event for any of the user's roles is a grant;
    5. otherwise not entitled.
14. **Event order is total:** `product_grants` has a `seq` identity column, and "latest" means the highest `seq`. Two events in one transaction never tie.
15. **No grant screen in phase 2.** Grants are written by the demo seed and the tests only. The repository records who granted or revoked.

**Routing (`core/steps/routing.py`)**

16. **The input** is the ticked element keys E and the ticked unmatched terms T. Empty E and empty T is refused by the API with 422; the UI disables the button.
17. **The order of checks.** "Cover" means the products' elements include every key in E.
    1. **E is empty** (only terms were ticked): `raise_requirement`.
    2. **reuse:** one product covers E.
    3. **compose:** two or three products together cover E.
    4. Otherwise take the best partial cover C (at most 3 products, covering as much of E as possible). Call the rest R = E minus the elements of C.
    5. **create_product:** every element in R is in the domains.
    6. **raise_requirement:** for M = R minus the in-domain elements. The result names C, the elements C covers, and any in-domain elements.
18. **The fixed rule for choosing a cover** applies to reuse, compose and the partial cover. Pick, in this order:
    1. the most elements of E covered;
    2. the fewest products;
    3. the fewest elements held beyond E;
    4. product codes in alphabetical order (the sorted list of codes compared as text).

    It is brute force over all combinations of up to 3 products. That is fine for a PoC catalogue (risk 2).
19. **Entitlement does not change routing.** A restricted product still counts for reuse or compose. The result carries each product's access state and route.
20. **"In the domains"** means: a current approved mapping of that element on a registered feed with at least one `released` load. Nothing in Amaze meets this yet, so `create_product` is reached only in tests (built with approved proposals, as in phase 1) until phase 7.
21. **compose and create_product build nothing.** compose is the stub `CLAUDE.md` names. create_product notifies the backend team; step 20 is phase 7.
22. **Requester messages:**
    - reuse, entitled: "This data is available in <product>."
    - reuse, not entitled: "This data is in <product>. Access restricted: ask <owner> for access."
    - compose: "This data can be composed from <A> and <B>. Composition is not available in the PoC yet."
    - create_product: "This data is already in Amaze. The backend team has been asked to create a product."
    - raise_requirement: "Requirement raised. The backend team will look for a source."

    The "being onboarded" message stays in phase 3.

**Step 01 (`core/steps/s01_define_requirement.py`)**

23. **Input:** requester, query text, purpose, element keys M and ticked terms T. It creates the requirement with status `defined`, its elements and its terms.
24. **Outcomes:**
    - `defined` when T is empty;
    - `element_not_in_glossary` when T has terms. The requirement is still created, and each glossary owner gets one in-app notification listing the terms.
25. **The backend team is notified for every requirement.** An element key that is not in the glossary raises `UnknownGlossaryElement`, and the API turns it into 422. The database refuses it too (assumption 27).
26. **Requirement title:** the query text, cut at 120 characters. Purpose goes into the existing `description` column.

**Data model (migration `0006`)**

27. Tables are listed in section 6. `product_elements` and `requirement_elements` reuse phase 1's `amaze_require_glossary_key()` trigger, so an unknown key is refused even by raw SQL.
28. **`products` gains `owner_id` and `visibility`**, both NOT NULL with no default. `0006` refuses to run if `products` already has rows, as `0002` did for decisions. The application database has none.
29. **Append-only** (UPDATE, DELETE and TRUNCATE refused, using phase 1's functions): `product_elements`, `product_grants`, `requirement_elements`, `requirement_terms`, `consumer_requests`. A product's element list is fixed when the product is created.

**API and UI**

30. **`/api/consumer/` is for the requester role only.** Backend users get 403 and no user gets 401, mirroring phase 1. A request that is not the caller's own returns 404, so it does not reveal that it exists.
31. **consumer-ui** has two hash views, `#/search` and `#/requests`, plus the phase 0 status line. No new npm package.
32. **backend-ui does not change.** The backend team's notifications are written to the in-app table but have no screen yet.
33. **I check the UI** by building it and by a scripted browser run with screenshots. The committed Playwright test stays in phase 7.

**Docs**

34. **I add one bullet to phase 3 in `BUILD_PLAN.md`:** "DiscoveryAssistant (step 17) on `Agent.run()`, behind the deterministic search." This keeps decision 2 from being lost. Say no if you would rather place it yourself.

## 5. Typed step results (A1)

```python
# core/steps/result.py
class StepResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    outcome: str                       # each subclass narrows it to one Literal

# core/steps/s01_define_requirement.py
class Defined(StepResult):
    outcome: Literal["defined"] = "defined"
    requirement: Requirement
    elements: list[str]

class ElementNotInGlossary(StepResult):
    outcome: Literal["element_not_in_glossary"] = "element_not_in_glossary"
    requirement: Requirement
    elements: list[str]
    terms: list[str]

DefineRequirementResult = Annotated[Defined | ElementNotInGlossary, Field(discriminator="outcome")]
```

The same pattern applies to step 17 hits (`found`, `not_entitled_visible`) and to routing (`reuse`, `compose`, `create_product`, `raise_requirement`).

Checked by `test_step_results.py`:

- each union's outcome names equal the list in `CLAUDE.md`, copied into the test;
- each result round-trips through JSON via its `TypeAdapter`;
- an unknown outcome is refused.

## 6. Data model (migration `0006`)

Every new table has `id` (uuid) and `created_at` (`clock_timestamp()`, phase 1 plan, section 17, tasks 4 to 9, item 3).

| Table | Columns beyond `id`, `created_at` | Rules |
|---|---|---|
| `products` (changed) | + `owner_id` (text), `visibility` (`open` or `restricted`) | both NOT NULL; check on visibility |
| `product_elements` | product_id, element_key | **Append-only.** Unique (product, element). Glossary-key trigger |
| `product_grants` | seq (identity), product_id, subject_type (`user` or `role`), subject, action (`grant` or `revoke`), decided_by | **Append-only.** A role subject must be one of the seven roles |
| `requirement_elements` | requirement_id, element_key | **Append-only.** Unique. Glossary-key trigger |
| `requirement_terms` | requirement_id, term | **Append-only.** Unique; term is not blank |
| `consumer_requests` | requester_id, query, purpose, element_keys (text[]), terms (text[]), outcome, product_codes (text[]), requirement_id (optional), result (jsonb snapshot of the routing result) | **Append-only.** outcome is one of the four routing names; `requirement_id` is required exactly when outcome is `raise_requirement` |

Only features present since PostgreSQL 14: tables, arrays, `jsonb`, an identity column, check constraints and plpgsql triggers.

## 7. Domain models and repositories

| Item | Change |
|---|---|
| `core/domain/product.py` | `ProductDraft` gains `owner_id`, `visibility`, `element_keys` (at least one) |
| `core/domain/entitlement.py` | `GrantEvent`, `Access` (`entitled`, or `not_entitled` with route) |
| `core/domain/requirement.py` | `Requirement` gains `elements` and `terms` |
| `core/domain/consumer_request.py` | `ConsumerRequest` |
| `ProductRepository` | `create` writes the elements; `list_with_elements()`, `elements_of()` |
| `ProductGrantRepository` (new) | `record(product_id, subject_type, subject, action, decided_by)`, `events_for(product_id)` |
| `RequirementRepository` | `create` takes element keys and terms; `get` and `list` return them |
| `ConsumerRequestRepository` (new) | `record(...)`, `get_for(requester_id, id)`, `list_for(requester_id)` |
| `DomainCoverage` (new, in `core/repositories/coverage.py`) | `in_domain(element_keys) -> set[str]`, the query in assumption 20 |

New named errors: `ConsumerRequestNotFound` and `NothingToRequest`. Existing ones are reused: `UnknownGlossaryElement`, `ProductNotFound`, `RequirementNotFound`. An unknown visibility value is refused by Pydantic and by a check constraint.

## 8. Demo seed (`python -m core.seed --demo`)

`fixtures/demo/products.json`. Synthetic names and invented definitions only. Safe to run again: it inserts missing products by code and never changes one. It needs the glossary seed first, and stops with that message if the glossary is empty.

| Code | Name | Visibility | Owner | Elements |
|---|---|---|---|---|
| `daily_close_prices` | Daily close prices | open | dev-data-owner | instrument_id, price_date, close_price, price_currency |
| `bid_ask_quotes` | Bid and ask quotes | open | dev-data-owner | instrument_id, price_date, bid_price, ask_price |
| `bond_reference` | Bond reference data | restricted | dev-data-owner | instrument_id, isin, issuer_name, maturity_date, coupon_rate |

No grants are seeded, so Riley Requester is not entitled to `bond_reference`.

The searches it makes possible:

| Search | Outcome |
|---|---|
| `close price` | hit `daily_close_prices` (found); request: `reuse` |
| `maturity date` | hit `bond_reference` (not_entitled_visible); request: `reuse` with "Access restricted" |
| `close price and bid price` | request: `compose` (`bid_ask_quotes` and `daily_close_prices`) |
| `close price and volume` | request: `raise_requirement` for `volume`, naming `daily_close_prices` for `close_price` |
| `volume` | no hits; request: `raise_requirement` (**the done-when path**) |
| `esg score` | unmatched term; ticked: `raise_requirement`, step 01 `element_not_in_glossary` |

Checked by `test_demo_seed.py`: every element key exists in the glossary; the rule 6 checks from `test_glossary_seed.py` also run on this file; a second run inserts 0; a missing glossary gives the named message.

## 9. API (`apps/api/routers/consumer.py`)

| Endpoint | Returns |
|---|---|
| `GET /api/consumer/search?q=` | matched elements, ambiguous candidates, matched domains, unmatched terms, and the hits (each `found` or `not_entitled_visible`, with the product's elements and the route) |
| `POST /api/consumer/requests` | body `{query, purpose, element_keys, terms}`. Runs routing, step 01 if needed, records the request, sends notifications, commits. 201 with the routing result and message |
| `GET /api/consumer/requests` | the caller's requests, newest first, each with the requirement's current status where there is one |
| `GET /api/consumer/requests/{id}` | one of the caller's requests; 404 otherwise |

- 401 if not signed in, 403 for backend roles, 422 for an unknown element key or nothing to request. Never a 500 for an expected case.
- Owner display names for routes come from `Identity.get_user()`. An unknown owner id is shown as the id itself.

Checked by `test_api_consumer.py`, one test per row of section 8's search table, plus:

- the auth cases;
- the 404 on another requester's request;
- the 422 cases;
- **the done-when test:** search `volume`, then POST, then GET shows the request with `raise_requirement` and requirement status `defined`;
- a test that a failure inside step 01 leaves no `consumer_requests` row and no notification behind (one transaction).

## 10. consumer-ui

1. **Shell:** the title, two links (Search, My requests), the dev-user picker and the phase 0 status line.
2. **Search:**
   - a box and a Search button;
   - the hits, each with its name, description, elements, and an "Available" or "Access restricted" badge with the route;
   - "No products hold this data." when there are none;
   - below the hits, "Request this data": the matched elements as ticked checkboxes, ambiguous candidates unticked, unmatched terms unticked with the question "Is this a data item you need?", and a purpose box;
   - Submit shows the routing message and a link to My requests.
3. **My requests:** a table of when, what was asked, outcome, products, requirement status. "No requests yet." when it is empty.
4. **Errors are shown, not hidden:** API unreachable, not signed in, not allowed, validation messages.

## 11. Files

```
docs/plans/phase-2.md                        this plan
BUILD_PLAN.md                         edit   phase 3 bullet (assumption 34)
CLAUDE.md                             edit   layout: core/steps/ modules; seed --demo
README.md                             edit   demo seed command, consumer-ui views, alembic head 0006
migrations/
  models.py                           edit
  versions/0006_consumption.py        new
core/
  seed.py                             edit   --demo flag
  domain/      product.py  requirement.py                          edit
               entitlement.py  consumer_request.py                 new
  repositories/ tables.py  errors.py  products.py  requirements.py edit
                grants.py  consumer_requests.py  coverage.py       new
  entitlement/ decide.py                                           new
  steps/       result.py  matching.py  s17_discover.py  routing.py  s01_define_requirement.py   new
apps/api/
  main.py  deps.py                    edit   consumer router; requester guard
  routers/consumer.py                 new
apps/consumer-ui/src/
  App.tsx  api.ts                     edit
  views/SearchView.tsx  views/RequestsView.tsx  views/StatusLine.tsx   new
fixtures/demo/products.json           new
tests/
  test_repo_products.py  test_repo_requirements.py  test_append_only.py
  test_migrations.py  test_domain_models.py  test_glossary_seed.py       edit
  test_repo_grants.py  test_repo_consumer_requests.py  test_coverage.py
  test_entitlement.py  test_step_results.py  test_matching.py
  test_s17_discover.py  test_routing.py  test_s01_define_requirement.py
  test_demo_seed.py  test_api_consumer.py                                new
```

## 12. Tasks, in order

Each task: tests first and shown failing, then code, then its check, then one commit. I stop at the four checkpoints and continue without stopping between them.

| # | Task | Tests written first | Check |
|---|---|---|---|
| 1 | Products: owner, visibility, `product_elements` (`0006`, first part); `ProductRepository` | `test_repo_products.py`, `test_append_only.py`, `test_domain_models.py` | pytest |
| 2 | `product_grants` and the entitlement decision | `test_repo_grants.py`, `test_entitlement.py` | pytest |
| 3 | Requirement elements and terms, `consumer_requests`, `DomainCoverage`; `0006` complete | `test_repo_requirements.py`, `test_repo_consumer_requests.py`, `test_coverage.py`, `test_migrations.py` | pytest; `alembic upgrade head`; `alembic current` is `0006 (head)`. **CHECKPOINT 1:** pytest output and `alembic current` |
| 4 | `StepResult` and term matching | `test_step_results.py`, `test_matching.py`, the collision check in `test_glossary_seed.py` | pytest |
| 5 | Step 17 discover | `test_s17_discover.py` | pytest |
| 6 | Routing checks | `test_routing.py`: each outcome, the order, the cover rule's four tie-breaks, the 3-product cap, partial coverage, empty E | pytest |
| 7 | Step 01 and its notifications | `test_s01_define_requirement.py` | pytest. **CHECKPOINT 2:** pytest output and the routing test list |
| 8 | Demo seed (`--demo`) | `test_demo_seed.py` | pytest; the command inserts 3, then 0 |
| 9 | Consumer API | `test_api_consumer.py` | pytest. **CHECKPOINT 3:** pytest output and the done-when API test |
| 10 | consumer-ui shell and Search view | none | `npm run build`; view loaded in a browser |
| 11 | consumer-ui My requests view | none | build; view loaded in a browser |
| 12 | `README.md`, `CLAUDE.md`, `BUILD_PLAN.md` | none | documents read back |
| 13 | Review against `CLAUDE.md`'s seven rules and this plan | none | findings recorded here. **CHECKPOINT 4:** section 13 results and screenshots. Phase 3 is not started |

## 13. Commands that prove phase 2 is done

Run from `F:\amaze-cm-poc` in Windows PowerShell.

```powershell
.\.venv\Scripts\python -m alembic upgrade head
.\.venv\Scripts\python -m core.seed
.\.venv\Scripts\python -m core.seed --demo
.\.venv\Scripts\python -m pytest -v
.\.venv\Scripts\python -m alembic current
npm --prefix apps\backend-ui run build
npm --prefix apps\consumer-ui run build
.\scripts\dev.ps1
```

| Command | Expected result |
|---|---|
| `core.seed --demo` | first run: 3 products inserted; later runs: 0 inserted, 3 already present |
| `pytest -v` | all pass; 0 failed, 0 skipped, 0 warnings |
| `alembic current` | `0006 (head)` |
| `npm ... run build` (twice) | exit code 0 |
| `dev.ps1` | three `OK` lines and health `ok` |

Then in the browser at `http://localhost:5174`, signed in as Riley Requester, run each search in section 8's table and check its outcome. Finally:

1. search `volume`;
2. see "No products hold this data.";
3. submit;
4. see "Requirement raised. The backend team will look for a source.";
5. open `#/requests` and see the request with status `defined`.

## 14. Not in phase 2

- The DiscoveryAssistant agent, `agents/base.py`, prompts.
- The "Known source?" check and S1 to S4.
- Access-request workflow and grant screens.
- The composition engine (stub only).
- Step 20 product creation.
- backend-ui screens for requirements or notifications.
- A notifications view in consumer-ui.
- Requirement de-duplication.
- The Playwright test.

## 15. Risks

1. **Matching is text only.** "closing" does not match "close", and no synonyms exist beyond the glossary aliases. The DiscoveryAssistant is the planned answer. Until then, the requester's ticks are the safety net.
2. **The cover search is brute force over combinations of up to 3 products.** It is instant for tens of products and slow for thousands. A production catalogue needs an index or a greedy rule; the fixed tie-break rule would then need re-checking.
3. **`create_product` cannot be shown in the demo** until phase 7 produces released loads. It is covered by tests only.
4. **Demo products have no feeds or loads behind them.** They exist so search and routing can be seen. Phase 7's real products will sit beside them with nothing marking which are demo. Say if you want demo products flagged.
5. **No de-duplication.** Two identical requests raise two requirements. Fine for the PoC; the backend team will see both.
6. **Grant and revoke events are trusted as written.** Who may grant is not checked until there is a grant screen.

## 16. Results and deviations (recorded during implementation)

1. **Deviation from `CLAUDE.md`'s agent table (decision 2):** step 17 has no DiscoveryAssistant in phase 2. Search is deterministic matching (section 4, assumptions 5 to 12). The agent is added after `agents/base.Agent.run()` exists. Read-only, so rule 1 and rule 4 are not affected.
