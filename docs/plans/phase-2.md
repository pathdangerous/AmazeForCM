# Phase 2 plan — Consumption request and routing (17, routing checks, 01)

**Status:** implemented; checkpoint 4 awaiting your review. Approved on 2026-11-14 with three changes: an entitlement tie-break in the cover rule (assumption 25), one-word alias matches arrive unticked (assumption 13), and a phase 8 note on notifications (section 16). Those changes are applied below. It replaces the first draft (`c5b5a77`) and applies your 16 decisions (section 3).
**Date:** 2026-11-14. **Branch:** `phase-2`, created from `main` at `69f2d4d`. **Scope:** phase 2 of `BUILD_PLAN.md` only. Local only; nothing is pushed.

## 1. Goal and "done when"

Phase 2 is done when all five are true:

1. A requester searches in consumer-ui. Each matching product is listed as "Available", or as "Access restricted" with the route to access (step 17).
2. "Request this data" runs the routing checks in order (reuse, compose, create product, raise requirement), and the requester sees the outcome and its message.
3. **A search for missing data raises a requirement (step 01), and the requester sees its status on "My requests".** This is `BUILD_PLAN.md`'s done-when. It is proved by the API test `test_done_when_missing_data_raises_a_requirement_and_shows_its_status` and in the browser (section 13).
4. Every step returns a typed result (rule 3), and each of the eight outcome names has a named test (section 5).
5. `pytest` passes with nothing skipped and no warnings.

## 2. State at the start (checked on 2026-11-14)

| Item | Found |
|---|---|
| Git | `main` = `phase-1` = `69f2d4d`. `phase-2` = `c5b5a77`, which adds only the first draft of this plan. Working tree clean |
| Tests | 374 collected |
| Database | `alembic current` on the application database: `0005 (head)` |
| Products, grants, requested elements | None exist. `products` has no owner, visibility or elements; `requirements` has no elements or terms |
| Import-boundary test | Covers `core.engine`, `core.entitlement`, `core.domain` and `core.repositories`. It does **not** cover `core.steps` |
| Glossary seed | 33 elements. No phrase (key, name or alias) belongs to two elements. One phrase starts with a stop-word: `as of date` |
| consumer-ui | One page: the phase 0 status line and the dev-user picker. `api.ts` can only GET |
| Plugins | Superpowers, Karpathy, i-have-adhd and Octopus are still not loaded. I follow the same flow by hand |

## 3. Decisions you made (not reopened)

1. **Step 17 search is deterministic code** over product name, description, domain, and element keys, names and aliases. The DiscoveryAssistant moves to phase 3. This is a deviation from `CLAUDE.md`'s agent table (section 17), and phase 3 of `BUILD_PLAN.md` gets a bullet (task 12).
2. **Term matching:** request text is matched to glossary keys, names and aliases. Case is ignored, the longest phrase is tried first, stop-words are skipped. The requester ticks which unmatched terms are real; only ticked terms are kept.
3. **`element_not_in_glossary`:** the requirement is still saved with the ticked terms, and the glossary owner gets an in-app notification. Routing uses matched elements only. If nothing matched, a requirement is raised.
4. **Entitlement** lives in `core/entitlement/`. A product is `open` or `restricted`. `product_grants` is append-only and holds grant and revoke events per user or role. The latest event wins; a user's own event beats one for their role; the owner always has access. The route to access is text only: "Ask <owner> for access to <product>".
5. **Routing, in order:** `reuse` (one product covers all), `compose` (stub: the fewest products that cover the request, at most 3, by a fixed rule), `create_product` (each element has a current approved mapping on a registered feed with at least one released load; step 20 stays in phase 7), `raise_requirement`. Partial coverage: if every uncovered element is in the domains, the outcome is `create_product`; otherwise a requirement is raised for the uncovered elements only, and the result names the products covering the rest.
6. **Demo products** sit behind `python -m core.seed --demo`, separate from the glossary seed. Their codes start with `demo_`, and a test enforces it.
7. **`consumer_requests` is append-only** and holds each request's routing outcome. "My requests" shows all outcomes.
8. **"Known source?"** is left for phase 3.
9. **Each step returns a Pydantic discriminated union on `outcome`.** The shared base is `core/steps/result.py`.
10. **The consumer API is `/api/consumer/`**, for the requester only. Backend users get 403.
11. **Phase 2 tables go in migration `0006`.** `0005` is applied and is never edited.
12. **A domain word** (for example "pricing") counts as a match. It is used as a filter, never as an unmatched term or a requested element.
13. **A phrase that belongs to two elements** shows both, unticked. A test proves the 33-element seed has no such clash.
14. **An empty search** returns an empty list, not a negative outcome, and leads to "Request this data".
15. **Message after a requirement is raised:** "Requirement raised; the backend team will look for a source." The backend team gets an in-app notification. The "being onboarded" message stays in phase 3.
16. **consumer-ui** gets two hash views, Search and My requests. No new npm packages. It is checked by build and screenshots; Playwright stays in phase 7.

## 4. Assumptions — veto any by number

**(new)** was not in the first draft. **(changed)** differs from the first draft. Unmarked items are carried over.

**Structure**

1. **Migration `0006_consumption.py`** holds every phase 2 table change. It is written across tasks 1 to 3 and reaches the application database only at checkpoint 1. The test fixture rebuilds the schema each run, so a half-written `0006` leaves nothing behind.
2. **(changed) Modules.** In `core/steps/`:
   - `result.py` (shared base);
   - `matching.py` (request text to glossary elements);
   - `s17_discover.py`;
   - `routing.py`;
   - `s01_define_requirement.py`;
   - `request_data.py` (assumption 3).

   The entitlement decision is `core/entitlement/decide.py`: pure code, no database access, no ports.
3. **(new) One function runs a request: `core/steps/request_data.py`.** In order: check the input, run routing, run step 01 when the outcome is `raise_requirement`, send the notifications, record the `consumer_requests` row, return. The API endpoint only checks the caller, calls it and commits. Reason: the first draft put this sequence in the router and gave the `create_product` notification no owner.
4. **Steps never commit.** The API endpoint owns the transaction: the requirement, its elements and terms, the request record and the notifications are committed together, or not at all.
5. **(changed) Steps get what they need as arguments:** the session, the user, the `Notifier`, the recipient ids for each role, and a function `display_name(user_id)`. Steps import no adapter. The API finds recipients with `Identity.list_users()` filtered by role, and builds `display_name` on `Identity.get_user()`. An unknown owner id is shown as the id itself.
6. **(new) The import-boundary test also covers `core.steps`.** `core.steps` is added to `BOUNDARY_PACKAGES`, and the checker's self-test gets a planted violation under `core/steps/`. Every phase 2 step is deterministic, so the whole package can be covered now. Phase 3 must settle how an agent-backed step fits (section 16).

**Matching (`core/steps/matching.py`)**

7. **(new) Words and segments.** The text is lower-cased. Every character other than `a-z`, `0-9`, space, hyphen and underscore cuts it into segments. Inside a segment, hyphens and underscores count as spaces, and words are split on spaces. A phrase never spans two segments.
8. **(changed) Glossary phrases keep every word.** Each current element gives its key, its name and its aliases, split by the rule in 7 (`yield_to_maturity` becomes `yield to maturity`). Each glossary domain gives one domain phrase (`security master`, `pricing`). The first draft dropped stop-words from glossary phrases too, which turned the alias `as_of_date` into the phrase `date`: a search for any date would have matched `price_date`.
9. **(changed) The stop-word list is fixed in code and pinned by a test.** 38 words: `a an the and or of to for in on at by from with per as i we me my our us need want would like please show give get find have is are all any some data`. A stop-word is skipped only where no phrase starts at it, so `as of date` still matches.
10. **(changed) Longest match, left to right, inside one segment.** At each word, try the longest phrase first (element and domain phrases together), down to one word. If nothing matches, a stop-word is skipped and any other word joins the current unmatched term. A match, a stop-word or the end of a segment closes the term. So `esg score and carbon footprint` gives two terms; the first draft gave one.
11. **(new) No stemming, plurals or spelling help.** `prices` does not match `price`. This is risk 1.
12. **A phrase owned by two elements** lists both as candidates, neither ticked (decision 13). **(new)** If a domain phrase ever equals an element phrase, the element wins. A seed test proves today's glossary has neither case.
13. **What matching returns** (`Match`, a plain model): matched element keys, ambiguous phrases with their candidates, matched domains, and unmatched terms. Each list is in order of first appearance, with no duplicates. A domain is never in the elements or the terms (decision 12). **Ticks:** each matched element says whether it starts ticked. An element matched through its key, its name or an alias of two or more words starts ticked. An element matched only through a one-word alias (`open`, `high`, `ask`, `offer`) starts unticked. Candidates and terms start unticked.

**Search (step 17, `core/steps/s17_discover.py`)**

14. **(changed) Which products are hits:**
    1. A product is a candidate if it holds a matched element or a candidate element, or if a word of an unmatched term is a whole word of its name or description.
    2. If the query names one or more domains, only products in those domains stay (decision 12). A query made only of domain words lists every product in those domains.
    3. Order: the number of matched elements held (most first), then name, then code.
15. **(changed) Each hit has its own outcome.** Restricted products are shown to everyone.
    - `found`: the requester may use it. The UI badge is "Available".
    - `not_entitled_visible`: shown with the route. The UI badge is "Access restricted" (`CLAUDE.md`'s wording), and the route is exactly "Ask <owner display name> for access to <product name>" (decision 4).
16. **No hits is an empty list** (decision 14). **(new)** A blank query also returns 200 with an empty match and an empty list.
17. **(new) Step 17's typed result is per product.** `discover()` returns the `Match` and a list of hits, each `found` or `not_entitled_visible`. This follows from decision 14: an empty list is not an outcome.

**Entitlement (`core/entitlement/decide.py`)**

18. **The rule, in order:**
    1. an `open` product is entitled, whatever its grant events say;
    2. the owner is entitled;
    3. if the user's own id has grant or revoke events, the latest one decides;
    4. otherwise, entitled if the latest event for any one of the user's roles is a grant;
    5. otherwise not entitled.
19. **Event order is total.** `product_grants` has a `seq` identity column, and "latest" means the highest `seq`.
20. **(new) The decision names the rule that fired.** `decide(visibility, owner_id, user_id, roles, events)` returns `Access` with `entitled` and `reason`: `open`, `owner`, `user_grant`, `user_revoke`, `role_grant` or `no_grant`. Each line of assumption 18 then has a test that names it. `route_to_access(owner_name, product_name)` builds the route text.
21. **No grant screen in phase 2.** Grants are written by tests only. The repository records who granted or revoked.

**Routing (`core/steps/routing.py`)**

22. **The input** is the ticked element keys E and the ticked terms T. Domains from the search are not part of it (decision 12). Empty E and empty T is refused with `NothingToRequest` (422); the UI disables Submit.
23. **(changed) The order of checks.** "Cover" means the products' elements include every key.
    1. Find the best cover C of E by the fixed rule in 25 (at most 3 products; empty when no product holds any element of E). R is E minus the elements C holds.
    2. T is not empty: `raise_requirement` (assumption 24). This includes the case where nothing matched (decision 3).
    3. R is empty and C is one product: `reuse`.
    4. R is empty and C is two or three products: `compose`.
    5. Every element of R is in the domains: `create_product`.
    6. Otherwise `raise_requirement` for **R, every uncovered element** (decision 5). The result names C and what each product covers, and marks which elements of R are already in the domains. The first draft raised the requirement only for the elements outside the domains.
24. **(new) Ticked terms always lead to a requirement.** The requirement holds T and R (R may be empty), and the result names the products that cover the rest of E. Reason: decisions 3 and 5 do not say what happens to a ticked term when products cover every matched element, and the first draft dropped it silently. The cover and in-domain checks still use matched elements only. If you veto this: the outcome comes from E alone, and the terms are saved on the request and sent to the glossary owner with no requirement.
25. **The fixed rule for choosing a cover.** Pick, in this order:
    1. the most elements of E covered;
    2. the fewest products;
    3. the most products the requester is entitled to;
    4. the fewest elements held beyond E;
    5. product codes in alphabetical order (the sorted list of codes compared as text).

    It is brute force over all combinations of up to 3 products (risk 2).
26. **Entitlement never changes the outcome.** A restricted product still counts for reuse or compose. Entitlement only breaks a tie between covers (assumption 25, line 3): when an open and a restricted product cover the same elements, the open one is chosen. The result carries each product's access state and route.
27. **"In the domains"** means: some feed is `registered`, has at least one load with status `released`, and its current mapping (the highest version of a source field) points at the element. Every mapping row is approved, because the database refuses any other (`0005`). Nothing in Amaze meets this until phase 7, so `create_product` is reached only in tests.
28. **compose and create_product build nothing.** compose is the stub `CLAUDE.md` names. For `create_product`, `request_data` notifies the backend team. Step 20 is phase 7.
29. **(changed) Requester messages, exact text:**
    - reuse, entitled: "This data is available in <product>."
    - reuse, not entitled: "This data is in <product>. Access restricted. Ask <owner> for access to <product>."
    - compose: "This data can be composed from <A> and <B>. Composition is not available in the PoC yet." (three products: "<A>, <B> and <C>")
    - create_product: "This data is already in Amaze. The backend team has been asked to create a product."
    - raise_requirement: "Requirement raised; the backend team will look for a source." (decision 15)

    Extra lines (`notes`) are built by `request_data` and shown under the message:
    - one per covering product: "Already available: <element names> in <product>." A restricted product adds its route;
    - when terms were ticked: "Not in the glossary yet: <terms>. The glossary owner has been told."

**Step 01 (`core/steps/s01_define_requirement.py`)**

30. **Input:** requester, query text, purpose, element keys and terms. It creates the requirement with status `defined`, its elements and its terms. It refuses with `NothingToRequest` when both lists are empty.
31. **Outcomes:**
    - `defined` when there are no terms;
    - `element_not_in_glossary` when there are terms. The requirement is still created with status `defined`, and each glossary owner gets one in-app notification listing the terms.
32. **(changed) Notifications, all in-app:**

    | When | To | Subject |
    |---|---|---|
    | every requirement (decision 15) | each `backend_team` user | "New requirement: <title>" |
    | terms were ticked | each `glossary_owner` user | "Terms not in the glossary" |
    | `create_product` | each `backend_team` user | "Product requested from data already in Amaze" |

    The body lists the requester's name, the elements and the terms. There is no link, because backend-ui has no screen for them yet. **(new)** Each result lists the user ids it notified, so an empty list is visible (risk 9).
33. **Requirement title:** the query text, cut at 120 characters. Purpose goes into the existing `description` column. **(new)** Purpose is optional.
34. **(new) Terms are cleaned before they are saved.** Each term is split by the rule in 7 and joined with single spaces; blanks and duplicates are dropped. A term equal to a glossary phrase is refused with `TermInGlossary` (422), because `element_not_in_glossary` would then be untrue. The UI never sends one.

**Data model (migration `0006`)**

35. Tables are in section 6. `product_elements` and `requirement_elements` reuse phase 1's `amaze_require_glossary_key()` trigger, so an unknown key is refused even by raw SQL. The column is named `element_key`, as the trigger expects.
36. **`products` gains `owner_id` and `visibility`**, both NOT NULL with no default. `0006` refuses to run if `products` already has rows, as `0002` did for decisions. The application database has none.
37. **Append-only** (UPDATE, DELETE and TRUNCATE refused by phase 1's function): `product_elements`, `product_grants`, `requirement_elements`, `requirement_terms`, `consumer_requests`.
38. **A product has at least one element, fixed when the product is created.** Pydantic and the repository check it; the database cannot say it simply.

**Demo data**

39. **Demo product codes start with `demo_`** (decision 6). A test checks the fixture file. **(new)** The loader also refuses a row without the prefix. No grants are seeded.

**API and UI**

40. **`/api/consumer/` is for the requester only.** Not signed in: 401. Any backend role: 403. A request that is not the caller's own: 404, so it does not reveal that it exists.
41. **consumer-ui** has two hash views, `#/search` and `#/requests`, plus the phase 0 status line. No new npm package. **(new)** Its `api.ts` gains `apiPost`; backend-ui's copy is not touched, so the two files stop being identical.
42. **backend-ui does not change.** The backend team's and the glossary owner's notifications are written to the in-app table but have no screen yet.
43. **I check the UI** by building it and by a scripted browser run with screenshots (section 10). The committed Playwright test stays in phase 7.

**Docs**

44. **(changed) Task 12 edits three documents:**
    - `BUILD_PLAN.md`, phase 3, one new bullet: "DiscoveryAssistant (step 17): search by element, domain or purpose, on `agents/base.Agent.run()`, beside phase 2's deterministic search (moved from phase 2; phase 2 plan, section 17)."
    - `CLAUDE.md`: the layout lines for `core/steps/`, `core/entitlement/` and `core/seed.py`, and `core/steps/` added to the sentence about the import-boundary test. Its agent table is not edited; the deviation is recorded in section 17.
    - `README.md`: the demo seed command, the consumer-ui views, `0006 (head)`.

## 5. Steps and typed results

### 5.1 The shapes

```python
# core/steps/result.py
class StepResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    outcome: str                       # each subclass narrows it to one Literal

# core/steps/s17_discover.py
class Found(StepResult):
    outcome: Literal["found"] = "found"
    product: ProductSummary            # code, name, description, domain, element keys

class NotEntitledVisible(StepResult):
    outcome: Literal["not_entitled_visible"] = "not_entitled_visible"
    product: ProductSummary
    route: str                         # "Ask <owner> for access to <product>"

Hit = Annotated[Found | NotEntitledVisible, Field(discriminator="outcome")]

# core/steps/routing.py
class Reuse(StepResult):               # outcome "reuse"
    product: ProductAccess             # code, name, covers, entitled, route
class Compose(StepResult):             # outcome "compose"
    products: list[ProductAccess]      # two or three
class CreateProduct(StepResult):       # outcome "create_product"
    elements: list[str]                # R: to be built from the domains
    covered_by: list[ProductAccess]
class RaiseRequirement(StepResult):    # outcome "raise_requirement"
    elements: list[str]                # R: every uncovered element
    terms: list[str]                   # T
    in_domains: list[str]              # the part of R already in the domains
    covered_by: list[ProductAccess]

RoutingResult = Annotated[Reuse | Compose | CreateProduct | RaiseRequirement, Field(discriminator="outcome")]

# core/steps/s01_define_requirement.py
class Defined(StepResult):             # outcome "defined"
    requirement: Requirement
    notified: list[str]
class ElementNotInGlossary(StepResult):  # outcome "element_not_in_glossary"
    requirement: Requirement
    terms: list[str]
    notified: list[str]

DefineRequirementResult = Annotated[Defined | ElementNotInGlossary, Field(discriminator="outcome")]
```

### 5.2 The functions

| Module | Function | Returns | Writes |
|---|---|---|---|
| `matching.py` | `match(text, phrases)` | `Match` | nothing |
| `s17_discover.py` | `discover(...)` | the `Match` and `list[Hit]` | nothing |
| `routing.py` | `route(...)` | `RoutingResult` | nothing |
| `s01_define_requirement.py` | `define_requirement(...)` | `DefineRequirementResult` | the requirement, its elements and terms; notifications |
| `request_data.py` | `request_data(...)` | the request id, the routing result, the step 01 result if any, `message`, `notes` | the `consumer_requests` row; the `create_product` notification |

### 5.3 Every outcome name and its test

The names are `CLAUDE.md`'s, letter for letter.

| Outcome | Step | Named test |
|---|---|---|
| `found` | 17 | `test_s17_discover.py::test_found_for_an_open_product` |
| `not_entitled_visible` | 17 | `test_s17_discover.py::test_not_entitled_visible_carries_the_route` |
| `reuse` | routing | `test_routing.py::test_reuse_when_one_product_covers_every_element` |
| `compose` | routing | `test_routing.py::test_compose_when_two_or_three_products_cover_every_element` |
| `create_product` | routing | `test_routing.py::test_create_product_when_every_uncovered_element_is_in_the_domains` |
| `raise_requirement` | routing | `test_routing.py::test_raise_requirement_for_the_uncovered_elements_only` |
| `defined` | 01 | `test_s01_define_requirement.py::test_defined_when_no_terms_were_ticked` |
| `element_not_in_glossary` | 01 | `test_s01_define_requirement.py::test_element_not_in_glossary_saves_the_terms_and_tells_the_glossary_owner` |

`known_source` and `no_known_source` belong to phase 3 (decision 8) and are not implemented.

`test_step_results.py` also checks:

- **the names match `CLAUDE.md`:** the test reads the lines "17 Discover", "Routing checks" and "01 Define requirement" from `CLAUDE.md` and compares the names in backticks with each union's outcomes. A rename on either side fails the test;
- each result round-trips through JSON via its `TypeAdapter`;
- an unknown outcome is refused.

### 5.4 Each decision and its test

| Decision | Named test |
|---|---|
| 1 search is deterministic | `test_import_boundaries.py` with `core.steps` in the boundary; `test_checker_catches_planted_violation[core.steps]` |
| 2 matching | `test_matching.py`: `test_case_is_ignored`, `test_longest_phrase_first`, `test_stop_words_are_skipped`, `test_stop_word_list_is_pinned`, `test_a_phrase_starting_with_a_stop_word_still_matches`, `test_a_stop_word_closes_an_unmatched_term`, `test_key_and_name_matches_arrive_ticked`, `test_a_one_word_alias_match_arrives_unticked`; `test_api_consumer.py::test_only_ticked_terms_are_kept` |
| 3 terms | `test_routing.py::test_only_terms_raises_a_requirement`, `test_ticked_terms_raise_a_requirement_even_when_products_cover_the_elements` |
| 4 entitlement | `test_entitlement.py`: `test_open_product_ignores_grant_events`, `test_owner_always_has_access`, `test_latest_user_event_wins`, `test_user_event_beats_role_event`, `test_latest_role_event_wins`, `test_any_granted_role_is_enough`, `test_no_event_is_not_entitled`, `test_route_text`; `test_repo_grants.py::test_events_are_append_only_and_ordered_by_seq` |
| 5 routing | the four outcome tests, plus `test_checks_run_in_order`, `test_cover_rule_tie_breaks` (one case per tie-break), `test_an_open_product_beats_a_restricted_one_covering_the_same_elements`, `test_at_most_three_products`, `test_partial_cover_with_in_domain_rest_is_create_product`, `test_restricted_product_still_counts`; `test_coverage.py` (in the domains: registered feed, released load, current mapping; and the three ways to miss) |
| 6 demo seed | `test_demo_seed.py`: `test_every_demo_code_starts_with_demo`, `test_loader_refuses_a_code_without_the_prefix`, `test_glossary_seed_creates_no_product` |
| 7 request log | `test_append_only.py[consumer_requests]`; `test_api_consumer.py::test_my_requests_lists_every_outcome` |
| 10 requester only | `test_api_consumer.py`: `test_backend_user_gets_403`, `test_no_user_gets_401`, `test_another_requesters_request_is_404` |
| 11 migration | `test_migrations.py`: `HEAD = "0006"`, `test_upgrade_downgrade_upgrade`, `test_0006_refuses_when_products_has_rows` |
| 12 domain word | `test_matching.py::test_a_domain_word_is_never_a_term_or_an_element`; `test_s17_discover.py::test_a_domain_word_filters_the_hits`; `test_glossary_seed.py::test_no_domain_phrase_is_an_element_phrase` |
| 13 two elements | `test_matching.py::test_a_phrase_of_two_elements_lists_both`; `test_glossary_seed.py::test_no_phrase_belongs_to_two_elements` |
| 14 empty search | `test_s17_discover.py::test_no_hits_is_an_empty_list`; `test_api_consumer.py::test_blank_query_returns_empty` |
| 15 message | `test_request_data.py::test_raise_requirement_message_and_backend_notification` |

Decisions 8, 9 and 16 are covered by sections 14, 5.3 and 10.

## 6. Data model (migration `0006`)

Every new table has `id` (uuid) and `created_at` (`clock_timestamp()`).

| Table | Columns beyond `id`, `created_at` | Rules |
|---|---|---|
| `products` (changed) | + `owner_id` (text), `visibility` | both NOT NULL; owner not blank; visibility is `open` or `restricted` |
| `product_elements` | product_id, element_key | **Append-only.** Unique (product, element). Glossary-key trigger |
| `product_grants` | seq (identity), product_id, subject_type, subject, action, decided_by | **Append-only.** subject_type is `user` or `role`; action is `grant` or `revoke`; a role subject is one of the seven roles; subject and decided_by not blank |
| `requirement_elements` | requirement_id, element_key | **Append-only.** Unique (requirement, element). Glossary-key trigger |
| `requirement_terms` | requirement_id, term | **Append-only.** Unique (requirement, term); term not blank |
| `consumer_requests` | requester_id, query, purpose, element_keys (text[]), terms (text[]), outcome, product_codes (text[]), requirement_id (optional), result (jsonb: the typed result, message and notes as returned) | **Append-only.** outcome is one of the four routing names; `requirement_id` is set exactly when outcome is `raise_requirement`; element_keys and terms are not both empty |

`consumer_requests.element_keys` is an array, so the glossary-key trigger cannot guard it; `request_data` checks the keys first, and the requirement's own elements are guarded by the database.

**PostgreSQL 14 or older only:** identity column (10), `jsonb`, arrays and `cardinality()` (9.4), `gen_random_uuid()` (13), check constraints, plpgsql triggers. Not used: `NULLS NOT DISTINCT` and `MERGE` (15), SQL/JSON constructors and `JSON_TABLE` (16, 17), `uuidv7()` and virtual generated columns (18). The local server is 18, so this is checked by review in task 13, not by a test (risk 7).

## 7. Domain models and repositories

| Item | Change |
|---|---|
| `core/domain/product.py` | `ProductDraft` gains `owner_id`, `visibility`, `element_keys` (at least one) |
| `core/domain/entitlement.py` | `GrantEvent`, `Access` (`entitled`, `reason`) |
| `core/domain/requirement.py` | `Requirement` gains `elements` and `terms` |
| `core/domain/consumer_request.py` | `ConsumerRequest` |
| `ProductRepository` | `create` writes the elements; `list_with_elements()`, `elements_of()` |
| `ProductGrantRepository` (new) | `record(product_id, subject_type, subject, action, decided_by)`, `events_for(product_id)` in `seq` order |
| `RequirementRepository` | `create` takes element keys and terms; `get` and `list` return them |
| `ConsumerRequestRepository` (new) | `record(...)`, `get_for(requester_id, id)`, `list_for(requester_id)` newest first |
| `DomainCoverage` (new, `core/repositories/coverage.py`) | `in_domains(element_keys) -> set[str]`, the query in assumption 27 |

New named errors: `ConsumerRequestNotFound`, `NothingToRequest`, `TermInGlossary`. Reused: `UnknownGlossaryElement`, `ProductNotFound`, `RequirementNotFound`, `DuplicateProductCode`. An unknown visibility, subject type or action is refused by Pydantic and by a check constraint.

## 8. Demo seed (`python -m core.seed --demo`)

`fixtures/demo/products.json`. Synthetic names and invented descriptions only.

- `python -m core.seed` loads the glossary only, as today. `--demo` loads the demo products only.
- Safe to run again: it inserts missing products by code and never changes one.
- It needs the glossary first. If an element key is missing, it stops with "Run python -m core.seed first" and exit code 1.

| Code | Name | Domain | Visibility | Owner | Elements |
|---|---|---|---|---|---|
| `demo_daily_close_prices` | Daily close prices | pricing | open | dev-data-owner | instrument_id, price_date, close_price, price_currency |
| `demo_bid_ask_quotes` | Bid and ask quotes | pricing | open | dev-data-owner | instrument_id, price_date, bid_price, ask_price |
| `demo_bond_reference` | Bond reference data | security_master | restricted | dev-data-owner | instrument_id, isin, issuer_name, maturity_date, coupon_rate |

No grants are seeded, so Riley Requester is not entitled to `demo_bond_reference`.

The searches it makes possible, signed in as Riley Requester:

| # | Search | Hits | "Request this data" |
|---|---|---|---|
| 1 | `close price` | Daily close prices: Available | `reuse` |
| 2 | `maturity date` | Bond reference data: Access restricted, "Ask Devon Owner for access to Bond reference data" | `reuse`, with the same route in the message |
| 3 | `close price and bid price` | Bid and ask quotes; Daily close prices | `compose` of the two |
| 4 | `close price and volume` | Daily close prices | `raise_requirement` for `volume`; note "Already available: Close price in Daily close prices."; step 01 `defined` |
| 5 | `volume` | none: "No products hold this data." | `raise_requirement`; step 01 `defined`. **The done-when path** |
| 6 | `esg score` | none | the term is unticked; once ticked: `raise_requirement`, step 01 `element_not_in_glossary` |
| 7 | `close price and esg score` | Daily close prices | term ticked: `raise_requirement` for the term, naming Daily close prices (assumption 24). Term unticked: `reuse` |
| 8 | `pricing` | Bid and ask quotes; Daily close prices (domain filter) | nothing to tick; Submit is disabled |
| 9 | `quotes` | Bid and ask quotes (a word of its name) | the term `quotes` is unticked |

Checked by `test_demo_seed.py`:

- the tests named in section 5.4;
- every element key and domain exists in the glossary seed, and every owner is a dev user;
- the rule 6 checks from `test_glossary_seed.py` also run on this file;
- a second run inserts 0;
- a missing glossary gives the named message.

## 9. API (`apps/api/routers/consumer.py`)

| Endpoint | Returns |
|---|---|
| `GET /api/consumer/search?q=` | `match` (elements, each with whether it starts ticked; ambiguous candidates; domains; terms) and `hits` (each `found` or `not_entitled_visible`, with the product's elements and, where restricted, the route) |
| `POST /api/consumer/requests` | body `{query, purpose, element_keys, terms}`. Calls `request_data` and commits. 201 with the request id, the routing result, the step 01 result if any, `message` and `notes` |
| `GET /api/consumer/requests` | the caller's requests, newest first: when, query, outcome, products, message, and the requirement's current status where there is one |
| `GET /api/consumer/requests/{id}` | one of the caller's requests with its stored result; 404 otherwise |

- 401 if not signed in. 403 for any backend role.
- 422 for an unknown element key, nothing to request, a term that is a glossary phrase, a blank query on POST, or input over the limits (query 500 characters, purpose 2000, 20 terms of 100 characters).
- Never a 500 for an expected case.
- The notifier is a FastAPI dependency built on the request's session, so its rows join the same transaction and a test can replace it.

Checked by `test_api_consumer.py`. The tests load the glossary and the demo fixture into the test's own transaction:

- one test per row of section 8's table (nine);
- the tests named in section 5.4;
- the 422 cases, one each;
- **the done-when test:** search `volume` gives no hits; POST gives 201, `raise_requirement` and the decision 15 message; GET shows the request with requirement status `defined`;
- `test_a_failed_request_leaves_nothing_behind`: the notifier fails on its last call, after the requirement and the first notification were written. Afterwards there is no requirement, no element or term row, no notification and no request row.

## 10. consumer-ui

1. **Shell:** the title, two links (Search, My requests), the dev-user picker and the phase 0 status line. The view follows the URL hash; an empty hash opens Search.
2. **Search:**
   - a box and a Search button;
   - the hits, each with its name, description, elements, and an "Available" or "Access restricted" badge with the route;
   - "No products hold this data." when there are none;
   - below the hits, "Request this data": matched elements ticked or unticked as the match says (assumption 13), candidate elements unticked, unmatched terms unticked under the question "Is this a data item you need?", and an optional purpose box;
   - Submit is disabled while nothing is ticked;
   - after Submit: the message, the notes and a link to My requests.
3. **My requests:** a table of when, what was asked, outcome, products, requirement status and message, newest first. "No requests yet." when it is empty.
4. **Errors are shown, not hidden:** API unreachable, not signed in, not allowed, validation messages.

Checked by `npm run build` and by a scripted browser run. Screenshots go to `.playwright-mcp/` (git-ignored):

- `phase2-search-1.png` to `phase2-search-9.png`: one per row of section 8's table, after Submit where the row has a request;
- `phase2-requests.png`: My requests after those rows, showing `reuse`, `compose` and `raise_requirement`. `create_product` cannot appear in the demo (risk 3); the API test `test_my_requests_lists_every_outcome` covers all four;
- `phase2-api-down.png`: Search with the API stopped, showing "API unreachable".

The run also checks that the browser console has no errors or warnings.

## 11. Files

```
docs/plans/phase-2.md                        this plan
BUILD_PLAN.md                         edit   phase 3 bullet (assumption 44)
CLAUDE.md                             edit   layout lines; boundary sentence (assumption 44)
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
  steps/       result.py  matching.py  s17_discover.py  routing.py
               s01_define_requirement.py  request_data.py          new
apps/api/
  main.py  deps.py                    edit   consumer router; requester guard; notifier dependency
  routers/consumer.py                 new
apps/consumer-ui/src/
  App.tsx  api.ts                     edit
  views/SearchView.tsx  views/RequestsView.tsx  views/StatusLine.tsx   new
fixtures/demo/products.json           new
tests/
  test_repo_products.py  test_repo_requirements.py  test_append_only.py
  test_migrations.py  test_domain_models.py  test_glossary_seed.py
  test_import_boundaries.py                                              edit
  test_repo_grants.py  test_repo_consumer_requests.py  test_coverage.py
  test_entitlement.py  test_step_results.py  test_matching.py
  test_s17_discover.py  test_routing.py  test_s01_define_requirement.py
  test_request_data.py  test_demo_seed.py  test_api_consumer.py          new
```

## 12. Tasks, in order

Each task: tests first and shown failing, then code, then its check, then one commit. I stop at the four checkpoints and continue without stopping between them.

| # | Task | Tests written first | Check |
|---|---|---|---|
| 1 | Products: owner, visibility, `product_elements` (`0006`, first part); `ProductRepository` | `test_repo_products.py`, `test_append_only.py`, `test_domain_models.py` | pytest |
| 2 | `product_grants` and the entitlement decision | `test_repo_grants.py`, `test_entitlement.py` | pytest |
| 3 | Requirement elements and terms, `consumer_requests`, `DomainCoverage`; `0006` complete | `test_repo_requirements.py`, `test_repo_consumer_requests.py`, `test_coverage.py`, `test_migrations.py` | pytest; `alembic upgrade head`; `alembic current` is `0006 (head)`. **CHECKPOINT 1:** pytest summary, `alembic current`, the list of triggers on the five new append-only tables |
| 4 | `StepResult`; boundary test extended to `core.steps`; term matching | `test_step_results.py`, `test_import_boundaries.py`, `test_matching.py`, the clash test in `test_glossary_seed.py` | pytest |
| 5 | Step 17 discover | `test_s17_discover.py` | pytest |
| 6 | Routing checks | `test_routing.py` | pytest |
| 7 | Step 01, its notifications, and `request_data` | `test_s01_define_requirement.py`, `test_request_data.py` | pytest. **CHECKPOINT 2:** pytest summary, and the section 5.3 table with each test shown passing |
| 8 | Demo seed (`--demo`) | `test_demo_seed.py` | pytest; the command inserts 3, then 0 |
| 9 | Consumer API | `test_api_consumer.py` | pytest. **CHECKPOINT 3:** pytest summary, and the done-when API test's output |
| 10 | consumer-ui shell and Search view | none | `npm run build`; view loaded in a browser |
| 11 | consumer-ui My requests view | none | build; view loaded in a browser |
| 12 | `README.md`, `CLAUDE.md`, `BUILD_PLAN.md` (assumption 44) | none | documents read back |
| 13 | Review against `CLAUDE.md`'s seven rules, the PostgreSQL 14 list and this plan | none | findings recorded in section 17. **CHECKPOINT 4:** section 13 results and the screenshots. Phase 3 is not started |

`test_step_results.py` grows with tasks 4 to 7, as each union is added. `test_append_only.py` grows with tasks 1 to 3.

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
| `core.seed` | `glossary seed: 0 inserted, 33 already present` |
| `core.seed --demo` | first run: `demo seed: 3 inserted, 0 already present`; later runs: `0 inserted, 3 already present` |
| `pytest -v` | all pass; 0 failed, 0 skipped, 0 warnings |
| `alembic current` | `0007 (head)` |
| `npm ... run build` (twice) | exit code 0 |
| `dev.ps1` | three `OK` lines and health `ok` |

Then in the browser at `http://localhost:5174`, signed in as Riley Requester, run the nine searches in section 8's table and check each outcome. Finally, the done-when path:

1. search `volume`;
2. see "No products hold this data.";
3. submit;
4. see "Requirement raised; the backend team will look for a source.";
5. open `#/requests` and see the request with outcome `raise_requirement` and requirement status `defined`.

## 14. Not in phase 2

- The DiscoveryAssistant agent, `agents/base.py`, prompts (phase 3).
- The "Known source?" check, its two outcomes, and S1 to S4 (phase 3).
- The "being onboarded", "Ready" and requirement-level "Access restricted" messages (phases 3 and 7).
- Any change to a requirement's status after `defined`.
- Access-request workflow and grant screens.
- The composition engine (stub only).
- Step 20 product creation (phase 7).
- backend-ui screens for requirements or notifications.
- A notifications view in consumer-ui.
- Requirement de-duplication.
- The committed Playwright test (phase 7).

## 15. Risks

1. **Matching is text only.** No plurals, stemming or synonyms beyond the glossary aliases: `prices` is offered as an unmatched term, and "rate of return" is offered as `rate` and `return`. One-word aliases such as `open`, `high`, `ask` and `offer` match everyday words, so an element matched only that way arrives unticked (assumption 13) and is requested only if the requester ticks it. One-word keys and names (`volume`, `currency`, `ticker`) still arrive ticked. The requester's ticks are the safety net until the DiscoveryAssistant.
2. **The cover search is brute force over combinations of up to 3 products.** It is instant for tens of products and slow for thousands. A production catalogue needs an index or a greedy rule; the tie-break rule would then need re-checking.
3. **`create_product` cannot be shown in the demo** until phase 7 produces released loads. It is covered by tests only.
4. **Demo products have no feeds or loads behind them.** The `demo_` prefix marks them; phase 7's real products will sit beside them.
5. **No de-duplication.** Two identical requests raise two requirements. Fine for the PoC; the backend team will see both.
6. **Grant and revoke events are trusted as written.** Who may grant is not checked until there is a grant screen.
7. **PostgreSQL 14 compatibility is checked by review, not by a test.** Both laptops run 18, so a newer feature would not fail locally.
8. **Assumption 24 changes one visible case.** A request whose elements are all covered, plus one ticked term, shows `raise_requirement`, not `reuse`. The note names the product, so the requester still sees the data is available.
9. **Notifications reach only users who hold the role.** The dev users always include one backend team member and one glossary owner. With Entra ID a role could be empty; the result's `notified` list would show it. Phase 8 must decide what happens then.
10. **API write tests share the test's rolled-back transaction.** The endpoint commits and rolls back on that session, so test data must be committed to the savepoint before the request. If this proves unstable, each request gets its own session on the same connection (phase 1 plan, risk 4).

## 16. Carried forward to later phases

Notes only; nothing here is built in phase 2.

**Phase 3**

1. **DiscoveryAssistant** (the `BUILD_PLAN.md` bullet in assumption 44).
2. **Agent-backed steps and the import boundary.** Phase 2 puts all of `core.steps` inside the boundary. The phase 3 plan must choose: steps receive the agent as an argument and stay inside, or the boundary becomes a list of the deterministic step modules.
3. **"Known source?"** starts from a requirement with status `defined`.

**Phase 8**

4. **Email and Teams notifications must go out after the commit.** In phase 2 the in-app notifier writes its rows inside the request's transaction, so a rolled-back request leaves no notification. An email or Teams message cannot be rolled back. The phase 8 adapter must therefore use an outbox: the step writes the message to a table inside the transaction, and a sender delivers it only after the commit. In-app notifications stay in the transaction as they are.

## 17. Results and deviations (recorded during implementation)

1. **Deviation from `CLAUDE.md`'s agent table (decision 1):** step 17 has no DiscoveryAssistant in phase 2. Search is deterministic matching (assumptions 7 to 17). The agent is added in phase 3, once `agents/base.Agent.run()` exists. The search is read-only and makes no model call, so rules 1 and 4 are not affected.

### Tasks 1 to 3 (checkpoint 1)

Tests: 374 at the start, 507 at checkpoint 1. All pass, none skipped, no warnings; the full suite was run twice.

**Finding from phase 1, not fixed here: check constraints are named twice over in the database.**

- Migrations `0002` to `0004` passed full names such as `ck_products_code_format`. `migrations/env.py` gives Alembic the naming convention, so each name was treated as the short name and prefixed again: the database holds `ck_products_ck_products_code_format`. Every check constraint from `0002` to `0004` is affected.
- Nothing is broken. The tests match constraint names as part of the error text, so they pass, and `test_orm_models_match_migrations` does not compare check constraints.
- `0006` names its check constraints with `op.f()`, so they are right (`ck_products_visibility`). `test_0006_check_constraints_have_the_convention_names` guards it.
- Not fixed, because an applied migration is never edited. Renaming the old constraints needs its own migration. Your decision.

**Deviations from the plan:**

1. `test_migrations.py` changed in task 1, not task 3. `test_single_head` fails as soon as `0006` exists.
2. `migrations/models.py` did not change. Every new table class is in `core/repositories/tables.py`, which it already imports.
3. `NothingToRequest` and `TermInGlossary` are added in task 7, where they are first raised. `ConsumerRequestNotFound` was added in task 3.

**More than the plan listed:**

1. `GlossaryRepository.require_keys()`: one query that names every unknown key. Products, requirements and the request log all use it.
2. `ConsumerRequestRepository.record()` also checks the element keys, because no trigger can guard an array column.
3. `ProductWithElements` is what `list_with_elements()` returns. Elements and terms are returned in alphabetical order.
4. The entitlement decision treats any visibility other than `open` as restricted (fail closed), and never reads a user id as a role.
5. `seq` is `GENERATED ALWAYS`: the database refuses a hand-written value, so nobody can insert an event that sorts before an existing one.

**Trigger proof.** With `APPEND_ONLY` and `GLOSSARY_KEYED` emptied in `0006`, 18 tests failed: 15 in `test_append_only.py` (UPDATE, DELETE and TRUNCATE on the five tables), the two glossary-key tests and the grants ordering test. The file was restored with the Edit tool.

**Application database:**

```
alembic upgrade head     Running upgrade 0005 -> 0006     exit 0
alembic current          0006 (head)
python -m core.seed      glossary seed: 0 inserted, 33 already present
```

The 12 new triggers are present: 5 append-only, 5 no-truncate, 2 glossary key (`product_elements`, `requirement_elements`). The six tables hold no rows.

### Before task 4: migration `0007`, constraint names (your request after checkpoint 1)

1. **Test first.** `test_migrations.py::test_no_constraint_name_repeats_its_prefix` reads every constraint in `public` from `pg_constraint` and fails on a name that starts with its prefix twice (`ck_x_ck_x_`, `uq_x_uq_x_`, and so on). Before `0007` it failed on 32 names, all of them check constraints: approvals 4, feeds 4, glossary_elements 6, loads 2, mappings 2, products 3, proposals 6, requirements 2, rules 3.
2. **`0007_constraint_names.py`** renames those 32 with `ALTER TABLE ... RENAME CONSTRAINT` (PostgreSQL 9.2). The names are written out in the migration. Only names change, not definitions. The downgrade renames them back. The new names are the ones the table classes in `tables.py` already produce, so `tables.py` did not change.
3. **From now on, explicit names use `op.f()`.** `0006` already does.
4. **Tests that match these constraint names:** `test_repo_feeds.py:106` (`ck_feeds_registered_has_owner`), `test_repo_loads.py:67` (`ck_loads_status`), `test_repo_proposals.py:187` (`ck_proposals_decided_at_matches_status`), `test_repo_requirements.py:58` (`ck_requirements_status`). **None needed a change.** Each matches its name as a substring of the error text, and the short name is inside the doubled one too. The only edit was `HEAD = "0007"` in `test_migrations.py`.
5. **Test database:** `downgrade base`, then `upgrade head`: both ran, `current` is `0007 (head)`. A separate check gave 32 doubled names at `0006` and 0 at `0007`, which proves the downgrade restores the old names.
6. **Application database:**

   ```
   alembic upgrade head     Running upgrade 0006 -> 0007     exit 0
   alembic current          0007 (head)
   python -m core.seed      glossary seed: 0 inserted, 33 already present
   ```

   0 of its 200 constraints repeat a prefix.
7. **Correction to my own check.** My first SQL check for doubled names used a regular expression with a back-reference. It returned 0 even at `0006`, so it was wrong. The counts in points 5 and 6 come from the same Python check as the test.
8. **Tests:** 508 pass.
9. **Section 13 now expects `0007 (head)`, not `0006`.**

### Tasks 4 to 7 (checkpoint 2)

Tests: 592 pass, none skipped, no warnings. Each of the eight outcome tests in section 5.3 passes by name, and every test named in section 5.4 for these tasks exists.

**Deviations from the plan:**

1. **`NothingToRequest` and `TermInGlossary` are in `core/steps/errors.py`, not `core/repositories/errors.py`.** They are refusals of a step's input, not repository errors. Their base class is `StepInputError`; the API maps it to 422 in task 9.
2. **`route()` is pure.** It takes the catalogue (each product with its elements and the requester's access) and the requested elements that are in the domains, and reads nothing. `route_request()` builds those from the database. Most routing tests use `route()`; two use `route_request()`.
3. **`discover()` and the routing checks take `user_id` and `roles`, not a `User`.** That is all the entitlement decision needs.
4. **`ProductGrantRepository.events_by_product()`** reads every product's events in one query, for search and routing.
5. **Notes are written for `compose`, `create_product` and `raise_requirement`**, one per product named, with the route for a restricted one. For `reuse` the message already names the product and its route.
6. **One test was wrong, not the code.** The first "most covered" case in `test_cover_rule_tie_breaks` had two products holding the same element, so rule 5 (codes) decided it. The case now has only the products that rule 1 must choose.

**Proof that the boundary test covers `core.steps` (tasks 4 to 7):** the planted `from ports.llm.base import LLMProvider` in `core/steps/s17_discover.py` is reported as `core.steps.s17_discover -> ports.llm`. With `core.steps` taken out of `BOUNDARY_PACKAGES`, the same tree gives no violation.

### Tasks 8 and 9 (checkpoint 3)

Tests: 631 pass, none skipped, no warnings; the full suite was run twice.

**Task 8, demo seed.**

```
python -m core.seed --demo     demo seed: 3 inserted, 0 already present
python -m core.seed --demo     demo seed: 0 inserted, 3 already present
python -m core.seed            glossary seed: 0 inserted, 33 already present
```

- `python -m core.seed` without the flag still loads the glossary only.
- With elements missing, `--demo` prints "... Run python -m core.seed first." to stderr and exits with code 1 (`DemoNeedsGlossary`).
- The demo descriptions avoid words that would change section 8's searches.

**Task 9, consumer API.**

1. `test_api_consumer.py` has 30 tests:
   - the nine searches of section 8;
   - the done-when test;
   - every outcome on My requests;
   - only ticked terms kept;
   - the 404 on another requester's request;
   - the blank query;
   - 401, and 403 for each of the six backend roles;
   - eight 422 cases;
   - the one-transaction test.
2. **The done-when test** (`test_done_when_missing_data_raises_a_requirement_and_shows_its_status`): search `volume` gives no hits; POST gives 201 with `raise_requirement` and "Requirement raised; the backend team will look for a source."; `GET /api/consumer/requests` shows it with requirement status `defined`; the detail endpoint returns the stored result.
3. **Proof for the one-transaction test.** With `session.rollback()` removed from the endpoint's error path, `test_a_failed_request_leaves_nothing_behind` failed: the requirement was left behind. The line was restored with the Edit tool.
4. **More than the plan listed:**
   - `requester_user` (in `deps.py`) is the requester-only guard.
   - `get_request_notifier` is the notifier dependency that the tests replace.
   - `GET /requests/{id}` returns the same 404 for an unknown id and for another requester's request.
5. **An unexpected error is not turned into a 422.** The endpoint rolls back and re-raises it. A stub notifier still gives 503, through the phase 1 handler.
6. **Not run against the application database:** a live POST. `consumer_requests` and the requirement tables are append-only, so a test row could never be removed. The browser run in tasks 10 and 11 writes the section 8 requests, as section 13 plans.

### Tasks 10 to 12

1. **Tasks 10 and 11 are one commit** (`5479009`). The shell imports both views, so they do not build apart.
2. **Browser run** (Playwright through the MCP browser; screenshots in `.playwright-mcp/`, git-ignored), signed in as Riley Requester on the application database. All nine searches of section 8 gave the planned hits, ticks and outcomes:

   | # | Search | Seen |
   |---|---|---|
   | 1 | `close price` | Daily close prices, Available; `close_price` ticked; "This data is available in Daily close prices." |
   | 2 | `maturity date` | Bond reference data, Access restricted, "Ask Devon Owner for access to Bond reference data"; the reuse message with the route |
   | 3 | `close price and bid price` | both pricing products; the compose message and one note per product |
   | 4 | `close price and volume` | Daily close prices; "Requirement raised; ..." and "Already available: Close price in Daily close prices." |
   | 5 | `volume` | "No products hold this data."; "Requirement raised; the backend team will look for a source." |
   | 6 | `esg score` | no hits; the term unticked and Submit disabled; ticked: requirement raised and the glossary-owner note |
   | 7 | `close price and esg score` | Daily close prices; term ticked: requirement raised, naming Daily close prices |
   | 8 | `pricing` | both pricing products (domain filter); nothing to tick; Submit disabled |
   | 9 | `quotes` | Bid and ask quotes; the term `quotes` unticked; Submit disabled |

   My requests (`phase2-requests.png`) lists 8 requests: `reuse`, `compose` and `raise_requirement`, with requirement status `defined` where one was raised. The browser console had no errors or warnings with the API running.
3. **My first browser script had a race, and it left one extra row in the application database.** After clicking Search it read the previous search's form before the new results arrived. Rows 1 to 6 were still submitted correctly, because Submit stays disabled until the new results arrive. In row 7, though, the term was ticked on the old form and then reset, so the request was recorded as `reuse` (the term-unticked case). That row is valid but was not intended, and it stays: the table is append-only. Row 7 with the term ticked was then run once more. The run also showed a real UI fault, fixed: a new search left the previous results and ticks on screen. It now clears them first.
4. **API stopped** (`phase2-api-down.png`): the status line shows "API unreachable" and the sign-in area shows "API unreachable. Is it running on port 8000?". Two fixes were needed to get there:
   - the Vite dev proxy answers 502 when the API is down, which was shown as "returned HTTP 502";
   - a failed dev-user list was silent.

   The four console errors in this state are the browser logging the 502 responses.
5. **Task 12:** `CLAUDE.md` (layout of `core/steps/`, `entitlement/decide.py`, `seed.py --demo`; `core/steps/` in the import-boundary sentence), `BUILD_PLAN.md` (phase 3 DiscoveryAssistant bullet) and `README.md` (demo seed, marketplace views, `0007 (head)`, the phase 2 append-only tables). Read back; UTF-8 without a BOM.
6. **Two repository files were edited with `sed`, not the Edit tool:** the `HEAD` line of `test_migrations.py` and this plan's status line. That breaks a standing instruction. Both files were checked: UTF-8, no BOM, only the intended line changed.

### Section 13 results

```
alembic upgrade head        no change (already at head)
python -m core.seed         glossary seed: 0 inserted, 33 already present
python -m core.seed --demo  demo seed: 0 inserted, 3 already present
pytest -v                   631 passed; 0 failed, 0 skipped, 0 warnings
alembic current             0007 (head)
npm ... run build           backend-ui exit 0; consumer-ui exit 0
dev.ps1                     three OK lines; health {"status":"ok", ..., "unavailable":[]}
```

Through the running services: both UIs return 200; `GET /api/consumer/search?q=volume` gives no hits; My requests shows `volume` as `raise_requirement` with requirement status `defined`; a backend user gets 403 on `/api/consumer/requests`. The browser done-when path (search `volume`, submit, see the message, see the request) is row 5 of the browser run.

## 18. End-of-phase review (task 13)

Octopus is not loaded, so this is my own review against `CLAUDE.md` and this plan. You can also run `/code-review`.

| Rule | Phase 2 state |
|---|---|
| 1 AI proposes, people decide | No agent and no model call in phase 2. Nothing writes configuration. Products are written only by the demo seed and the tests; grants only by the tests (risk 6) |
| 2 No model call in engine or entitlement | `core/entitlement/decide.py` is pure code with no database or ports. The boundary test now also covers `core/steps`, with a planted violation proving it |
| 3 Explicit negative outcomes | The eight outcomes are discriminated unions whose names the tests read from `CLAUDE.md`. Refused input is a named error and a 422; an empty search is an empty list; nothing expected gives a 500 |
| 4 Every proposal logged | No proposal is made in phase 2 |
| 5 Azure behind interfaces | Notifications go through the `Notifier` port, chosen by a FastAPI dependency. No Azure import. An email stub gives 503. Phase 8 needs an outbox (section 16) |
| 6 Synthetic data only | The demo file passes the glossary seed's real-data checks; owners are dev users; `demo_` is enforced |
| 7 Versioned, never edited in place | Five more append-only tables, guarded by triggers that 18 tests prove. A product's elements are fixed at creation. The only UPDATE path in phase 2's tables is the phase 1 `RequirementRepository.set_status`, which phase 2 never calls |

**PostgreSQL 14:** `0006` and `0007` use an identity column (10), `jsonb`, arrays and `cardinality()` (9.4), `gen_random_uuid()` (13) and `RENAME CONSTRAINT` (9.2); the new queries use `DISTINCT ON`. Nothing newer.

**Plan done-when (section 1):** all five met. Search shows Available or Access restricted with the route; Request this data runs the checks in order and shows the outcome; missing data raises a requirement whose status shows on My requests (test and browser); every step returns a typed result and each of the eight outcomes has a named test; 631 tests pass with nothing skipped and no warnings.

**Nothing from phases 3 to 8 was built.** No agent, no "Known source?", no status after `defined`, no grant screen, no step 20, no Playwright test in the repository.

**Findings left open (none blocks phase 2):**

1. **The application database holds 8 marketplace requests, 4 requirements and 6 notifications from the browser run** (counted from the tables; the 6 are 4 to the backend team and 2 to the glossary owner). One is the unintended `reuse` row from finding 3 above. They cannot be deleted (append-only), by design.
2. **Notifications have no screen.** The backend team and glossary owner rows exist in `notifications`, but neither UI shows them (section 14).
3. **`CLAUDE.md`'s agent table still lists the DiscoveryAssistant at step 17.** This is the recorded deviation (section 17, item 1); phase 3 adds the agent.
4. **The three `dev.ps1` windows are still open.** Close them before running `dev.ps1` again.
