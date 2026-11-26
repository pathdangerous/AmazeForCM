# Phase 7.22: mapping before glossary; "Run all agents" removed

Owner decisions after the demo (2026-11-26):

1. "Run all agents" (phase 7.13 chain mode) is removed. The owner does not want it.
2. Mapping runs before the glossary. A column is mapped to the **existing** glossary first. A column with no fitting element is
   shown as "Cannot map: no glossary element fits", and a person chooses per column: **Ignore** (left out, not loaded) or
   **Request glossary element** (an element proposal to the glossary owner). Mapping and the later steps continue with the mapped
   columns. An ignored or requested column never blocks go-live.

Rules unchanged: rule 1 (a person decides every proposal), rule 2 (no model call in the run engine or entitlement), rule 3
(typed negative outcomes), rule 4 (every model call logged), rule 7 (versioned configuration). No external tools: everything is
built in Amaze. Branch `phase-7.22` from `main` (7001127). Next migration: **0040** (two CHECK constraints; section 5).

## T0: design (STOP here for review; nothing below is built yet)

### 1. What exists today (read from the code)

- Steps (`core/domain/onboarding.py`): `upload, profile, glossary, mapping, transformations, rules, go_live, first_load,
  product_ready`. Step names are CHECK-listed in 0024 but **their order is not enforced anywhere in the database**
  (`amaze_valid_step_states` checks the set of nine names and the states, not the order). Order lives in `ONBOARDING_STEPS`
  only. So the new order needs no migration.
- Registering the feed (`register_feed()`, step 04, called by `_approve_profile`) always queues a glossary run
  (`reason = registered`) that asks the builder about **every** new column. That is the glossary-first behaviour the owner
  does not want in the onboarding. The API's background `GlossaryRuns` worker may run it before the onboarding job does.
- `record_mappings()` (step 06) sends every column the MappingAgent could not map to an existing element **back to step 05**:
  it queues a `sent_back` glossary run in the same transaction (typed result `no_element_back_to_05`).
- 7.13 chain mode: `onboarding_sessions.run_mode`, `run_all()`, `refresh_chain()`, the chain worker in `_finish_job`, the chain
  review (`chain_review()`, `GET .../chain`), `draft_config()` (`core/steps/drafts.py`) and the `draft=` argument of
  `mapping_input`, `transform_input`, `rules_input` and their writers, derived "stale" drafts, `proposal_bases` (0032),
  `approve_confident()`, the UI button and `ChainReview.tsx`.
- Rejection memory exists for mappings (`MappingRepository.rejected_elements`) and rules (`RuleRepository.rejected_keys`), and
  both ignore a rejection by `system` (7.13 T4). **Transformations have none.**
- After go-live a new mapping makes `set(go_live.mapping_ids) != current mappings`, and the engine refuses the load until a
  new go-live is approved (`core/engine/run.py`, 7.19 plan, assumption 14). A column that joins after release reaches the
  product at the next released load (`core/steps/product_columns.py`).

### 2. The new step order

```
upload -> profile -> mapping -> glossary ("Glossary requests") -> transformations -> rules -> go_live -> first_load -> product_ready
```

Step key `glossary` is kept (it is in the CHECK constraints, events, jobs and tests); only its place and its label change
("Glossary requests" in both UIs). `BACKABLE`, `AGENT_STEPS` and the UI step lists follow the new order. Nothing in the
database changes for this.

**Mapping** (current step after the profile approval): the MappingAgent proposes against existing elements only (it already
does: its candidates come from `GlossaryRepository.list_current()`). A column it cannot map is a **cannot-map column**,
not a proposal and not a send-back (decision D2). Approve & next at mapping needs every cannot-map column decided.

**Glossary requests**: the builder drafts an element for each requested column (and only those: D4). Its items are those
`glossary_element` proposals, plus the pending mapping proposal each approval creates (D5). Approve & next never waits for an
element it cannot approve (D6). With no request at all the job finds no run and the step is ready with no items.

### 3. What of 7.13 is removed and what stays

| Part of 7.13 | Fate | Why |
|---|---|---|
| "Run all agents" button, `POST .../run-all`, `run_all()` | **removed** | owner |
| `run_mode` use (`set_run_mode`, `OnboardingSession.run_mode`, `RunMode`, the API field, `chain` branches in `run_job`, `enter_step`, `approve_and_next`, `_item_step`, `decide_item`, `manual_rule_input`) | **removed** | owner. The **column** `onboarding_sessions.run_mode` stays (default `step`, nothing writes `chain` any more) |
| chain worker (`_queue_draft`, `_next_draft`, `_go_on_with_the_chain`, `_chain_stopped`, `_Env.chain/draft()`, `DRAFTED`, `CHAIN_STEPS` as a chain list) | **removed** | |
| chain review (`chain_review()`, `GET .../chain`, `ChainReview`, `ChainItem`, `ChainStep`, `ChainBase`, `TestedOn`, `transformations_tested_on()`, `ChainReview.tsx` and its test) | **removed** | `TestedOn` exists only for rule drafts built on draft transformations; with no drafts it is always the approved version |
| refresh / stale reruns (`refresh_chain`, `stale_drafts`, `_reject_stale`, `_was_blocked`, `STALE_NOTE`, events `draft_stale`, `chain_*` as new events) | **removed** | event names stay readable (see below) |
| `core/steps/drafts.py`, `draft=` parameters, `_blocked_columns` | **removed** | no caller left |
| `tests/test_chain.py`, `test_chain_review.py`, `test_chain_view.py`, chain cases of `test_api_onboarding.py`, `test_drafts.py`, `ChainReview.test.tsx`, chain cases of `WorkspaceView.test.tsx`, chain steps of `e2e/tests/demo_story.spec.ts` | **removed or rewritten to the step flow**; each listed in T1's report with why | |
| the Back / system-rejection fix (T4 of 7.13: `rejected_elements()` and `rejected_keys()` skip a rejection decided by `system`) | **stays** | owner; it is also what lets Back re-propose |
| per-step "Approve all confident" | **stays**, moved and simplified (D7) | owner |
| 0032's tables and constraints (`proposal_bases`, `trg_approvals_c_bases_approved`, `run_mode`, the four chain event names) | **left in place, unused; no data dropped** | owner. `ProposalRepository.add_bases()/bases()` stay because `tests/test_db_chain_guards.py` exercises the database guards that stay |
| `Stepper.canOpen` opening a `ready`/`needs_decision` step | left as is (harmless: in step mode only the current step has those states) | noticed, not touched |

Event names `chain_started`, `chain_done`, `chain_stopped`, `draft_stale` stay in `EventName` and in `ck_onboarding_events_event`:
an onboarding that ran in chain mode still has them in its append-only history and the timeline must still read it.

### 4. Decisions

**D1. Registration in the onboarding queues no glossary run.** `register_feed(..., queue_glossary: bool = True)`; the onboarding
passes `False`. `Registered.glossary_run_id` becomes `UUID | None`. The default stays `True` so the phase 4/5 workbench path
(step 04 then step 05 then step 06 as separate screens, `tests/test_s04_register.py`, the first Playwright story) keeps today's
behaviour. *Ask: Q1.*

**D2. A cannot-map column is derived, not a proposal.** The MappingAgent returns no proposal for it (today's behaviour: the
typed result `no_element_back_to_05` with its reason `no_candidate`, `type_does_not_fit` or `every_candidate_rejected`). In the
onboarding `record_mappings(..., send_back=False)` does not queue a glossary run and the mapping job writes the result into the
`agent_done` event detail (`cannot_map: {column: reason}`; an append-only record, no schema change). The outcome name
`no_element_back_to_05` is kept (CLAUDE.md lists it; tests use it); its docstring now says "to the person, who may request a
glossary element". `Workspace.cannot_map` lists, per column: `column`, `reason`, `state` and, once requested, the glossary
proposal's status:

| state | how it is derived (read only) |
|---|---|
| `undecided` | in the latest `cannot_map`, still unmapped, no excluded mapping proposal (pending or approved), no glossary request |
| `ignored` | a pending or approved `mapping` proposal with `excluded: true` for the column (pending: it is also an ordinary item to approve) |
| `requested` | a `column_requested` glossary run lists the column, or a `glossary_element` proposal exists for it |
| gone from the list | the column got a mapping meanwhile |

A rejected "Ignore" proposal brings the column back to `undecided`. A request cannot be taken back in 7.22 (out of scope).

**D3. Ignore = the existing manual excluded mapping.** `POST .../items` with `ManualMapping(excluded=True)` (T7 of 7.12): a
pending proposal by `manual` origin, the person as author, approved by Approve & next, no `mappings` row, so the column is not
loaded. No new code path for the decision itself.

**D4. Request glossary element = a glossary run for that column only.** New endpoint `POST /api/amaze/onboardings/{id}/
glossary-requests {column}` and step function `request_glossary()`: lock feed row then session row, check the column is an
`undecided` cannot-map column of the current mapping step, then `queue_run(reason="column_requested", columns=[column])`, event
`glossary_requested`, kick the glossary worker. New run reason `column_requested` (migration 0040). `builder_input()` treats it
like `structure_change` for the **set of columns** (only the run's own columns are looked at, so the builder never asks about an
unmapped or ignored column that was not requested) but, like a sent-back column, skips only the name rule. It still skips a
mapped column and a column that already has a glossary proposal (`already_asked`). The GlossaryBuilderAgent, its prompt, its
logged call and its proposals are unchanged. The builder runs in the background as soon as the run is queued, so the draft is
usually ready when the person reaches the Glossary requests step; if not, the step's job runs it as today.
Typed result `glossary_requested` (`column`, `run_id`, `created`); refusals are named (`NotACannotMapColumn`, 409; the existing
`StepNotEnterable` and `StepNotReady`).

**D5. Approving a requested element creates that column's mapping proposal, by code, from the approval.** `decide_element()`
(the one decision function every route uses) gets one more outcome field `mapping_proposal_id`. When the approved proposal came
from a `column_requested` run (the proposal payload's `run_id` points at it) and the column is still unmapped, not excluded and
has no pending mapping proposal, it creates a pending `mapping` proposal for the column and the approved element, **in the same
transaction**: `origin = 'manual'`, `author` = the person who approved the element, `proposal_log_id` none (no model is called,
so no made-up log row, as in 7.12 T7), approver role `data_steward`, one candidate = the element, recommended, with a reason
that names the request. Chosen over a second MappingAgent run because it is deterministic, needs no model call inside the
approval, and works the same after go-live, when no onboarding session or job exists. The steward still decides it (rule 1).
It is an existing kind, origin and role: no new guard. *Ask: Q2.*

**D6. The Glossary requests step never blocks the onboarding.** Approve & next at this step approves, as the person, every
pending glossary item the person may approve (role `glossary_owner`) and that is **pre-accepted** (at or above the confidence
threshold); for each approved element it then approves the mapping proposal D5 created if the person holds `data_steward`.
Everything else stays pending: a conflict, a low-confidence element, an item the person has no role for. Those columns are
**waiting for glossary (not loaded yet)** and the step moves on. With the dev operator (all roles, four-eyes off) the whole
request is approved and mapped in one click; with separate people the owner decides on the Glossary page and the steward
approves the mapping later. *Ask: Q3.*

**D7. "Approve all confident" stays per step, with no chain.** `POST /api/amaze/onboardings/{id}/steps/{step}/approve-confident`
(no body; replaces `POST .../chain/approve-confident {step}`), for the four steps with agent items (mapping, glossary,
transformations, rules). It approves, through the step's own decision function, one approval row per item, each pending item
that is at or above the threshold, not waiting for a second person (the bases condition goes away with the bases). Returns the
approved ids and the workspace. A button in the step's own item list in `WorkspaceView`.

**D8. A rejected transformation is not proposed again (T5).** `TransformationInput.rejected` carries, per column, the ops of
every transformation proposal a **person** rejected with a reason (`decided_by <> 'system'`, non-blank note), and the reason.
The prompt's input schema gains the field (a new prompt version, never an edit: `transformation.v3.yaml`); the mock respects
it; and `TransformationAgent.to_proposals()` drops any `(column, op)` that was rejected, and the column if no step is left, so
the guarantee is code, not only the model's behaviour (the same shape as `kept_elements()` for mappings). *Ask: Q4.*

**D9. Where the names appear (T3, T4).**
- Completion report (`core/steps/completion.py`): `MappingFigures` gets `waiting_for_glossary: list[str]`; `undecided` no
  longer counts them; `words` names ignored and waiting columns; `ready_for_go_live` is true with them present.
- Product page (`core/steps/product_page.py`, consumer UI): a product with a feed lists `columns_not_loaded.ignored` and
  `columns_not_loaded.waiting_for_glossary` by source column name, "waiting for glossary (not loaded yet)".
- Feed page (backend UI): the same two lists next to the mappings.
- Element link (T4): one shared component in `apps/ui-shared` (`ElementLink`), used in the mapping step, the feed page, the
  completion report and the Rules "Show rows" panel. Name shown as a link; hover **and keyboard focus** open a card with name,
  definition, data type and aliases (from `GET /api/amaze/glossary/elements/{key}`, fetched once per key); click goes to the
  element on the Glossary page; Esc closes the card; the link is a real `<a href>`, so it is reachable by Tab and works with
  Enter.

### 5. Lifecycle of a requested column

| When the element is approved | What happens |
|---|---|
| **During the onboarding, in the Glossary requests step** | D6: element and its mapping approved in the same click when the person holds both roles; the column is mapped before Transformations, so the transformation and rule agents see it like any mapped column |
| **During the onboarding, later (out of the step: the owner uses the Glossary page while the session is at Transformations or Rules)** | D5 still creates the pending mapping proposal. It is not an item of any later step; the steward approves it from the proposals page (existing route). The column is then mapped but has no transformation or rule proposal from this session (limit, below) |
| **After go-live (the 7.19 path)** | D5 creates the pending mapping proposal; once the steward approves it the current mappings differ from the go-live's, so the engine refuses scheduled loads until a new go-live version is approved (existing behaviour, `config_differs`); the first released load after that adds the element to the feed's product (`gain_elements`) and tells each consumer once. Nothing new is built for this |
| **Never (rejected or left pending)** | the column stays "waiting for glossary (not loaded yet)" on the report, the feed page and the product page |

### 6. Triggers, functions and constraints: what changes (migration 0040)

| # | Object | Change |
|---|---|---|
| 1 | `ck_glossary_runs_reason` (0016, widened by 0019) | new version: `registered, requested, sent_back, structure_change, column_requested`. `GlossaryRunReason`, `GLOSSARY_RUN_REASONS` and `tests/test_domain_models.py` follow |
| 2 | `ck_onboarding_events_event` (0024, widened 0028, 0032) | new version: the 0032 names plus `glossary_requested` |

Downgrade stops, naming 0040, while a `column_requested` run or a `glossary_requested` event exists (the 0019/0032 pattern).
PostgreSQL 14 features only.

**Looked at and not changed, and why** (never worked around: if one refuses, the code is wrong):
`amaze_guard_onboarding_session()` (no new column moves; `run_mode` simply stops being written); `trg_approvals_c_bases_approved`
and `amaze_guard_proposal_base()` (no row is written to `proposal_bases` any more, so they never fire); `amaze_delete_feed()`
(no new table); `ck_proposals_origin_matches_source`, `ck_proposals_manual_kinds` and `ck_proposals_names_a_column` (D5 is a
manual `mapping` proposal with a feed and a column: allowed since 0028); `trg_mappings_b_matches_proposal` (the approved
proposal's candidate is the element); the glossary-run guard of 0017 (a run still names the feed's registered profile);
`uq_proposals_one_pending_per_feed_column` (the glossary proposal is decided before its mapping proposal is created);
`amaze_require_glossary_option()` (the element written is one of its proposal's options); the go-live guards of 0018/0023/0037
(a requested column has no mapping, so it is in no go-live); `amaze_valid_step_states` (same nine names).

### 7. Assumptions (numbered; stop and ask if one is wrong)

1. Step order is only in code (`ONBOARDING_STEPS`); the database checks the set of names (section 1). No data migration of
   sessions.
2. Registration in the onboarding queues no glossary run; registration elsewhere is unchanged (Q1).
3. A cannot-map column is the agent's typed outcome `no_element_back_to_05`, recorded in the `agent_done` detail; it is not a
   proposal and needs no new proposal kind or origin.
4. Ignore is the existing excluded-mapping item; nothing new is written for it.
5. Request needs a new glossary run reason (`column_requested`, migration 0040) so the builder asks about the requested columns
   only; `sent_back` and `registered` would also ask about every other unmapped column.
6. One glossary run is active per feed (existing unique index); a second request while one is queued joins its columns
   (existing `GlossaryRunRepository.queue`); while one is running, the new column waits for the next run (existing behaviour,
   typed `created = false`).
7. The builder run is not counted in the session's model-call cap (it never was: the registration run is not either); each call
   is still logged (rule 4).
8. D5's mapping proposal is manual by origin and authored by the approving glossary owner (Q2). It is not an agent proposal and
   writes no `proposal_log` row.
9. D6: a step that only has items nobody can decide still ends; the column's state is shown, not hidden.
10. "Approve all confident" approves only pre-accepted items the person may approve; it never approves a conflict, a tie or a
    low-confidence item.
11. Rule 2 holds: no model call in the run engine, in entitlement, or in any code that decides what runs next; the Request
    decision only queues a run, it calls no model.
12. T5's "kind" is the transformation step's `op` (Q4). The memory is per feed and column and counts only a person's rejection
    with a reason; a rejection by `system` (Back) does not count.
13. A requested column approved after the Transformations step has no transformation or rule proposal from this session (known
    limit); the steward may add one by hand while the step is current, or after go-live through the existing path.
14. An open onboarding at deploy time has the old order or chain drafts. **No data migration is written** (no data is dropped);
    finish or delete such feeds (`amaze_delete_feed()`, 7.9) before pulling 7.22 on dev and on Replit. Sessions already `done` are
    unaffected. *Ask: Q5.*
15. Backward compatibility of the API: `OnboardingSession.run_mode` disappears from responses; `.../run-all`, `.../chain` and
    `.../chain/approve-confident` return 404. The two UIs are built from this repository, so they move together.
16. The consumer product page names source columns; they are the file's own column names and are shown only to someone who can
    see the product (open or entitled), as its column list is today.
17. Tests use the existing fixtures (`db_session`, `tests/configured.py`, `tests/onboarded.py`, `tests/step_chains.py`, the
    dev users) and the mock provider; no new test infrastructure. Each new committing endpoint (`glossary-requests`,
    `steps/{step}/approve-confident`) gets one happy-path commit-visibility test; the added test time is stated at the end.
18. Ports 8000, 5173, 5174 are checked before any run that starts the stack; if busy: stop and ask.
19. Lock order unchanged: `request_glossary()` takes the feed row, then the session row, then `queue_run`'s own locks
    (glossary_runs row); `decide_element()` keeps feed row, proposal row, glossary lock, and inserts its mapping proposal after.

### 8. Open questions (my default is in bold; I proceed with it unless you say otherwise)

- **Q1.** Registration outside the onboarding (the phase 4/5 workbench screens): **keep queueing the full glossary run** (only
  the onboarding skips it), or remove it everywhere? Removing it changes the first Playwright story and phase 4/5 tests.
- **Q2.** The mapping proposal after an element approval: **manual, authored by the approving glossary owner, no model call**
  (D5), or a MappingAgent run (needs a job and a model call; cannot work after the onboarding session is done)?
- **Q3.** Glossary requests step: **never blocks; an element the person cannot or does not approve stays pending as "waiting
  for glossary"** (D6), or should a pending conflict keep the step from finishing like every other step?
- **Q4.** T5 "kind": **each op of a rejected proposal, per column** (a later run may not propose that op for that column), or the
  whole combination of ops?
- **Q5.** Open onboardings on dev and Replit when 7.22 lands: **finish or delete them first, no data migration**.

## Tasks (after the review), one commit each

- **T1 Remove "Run all agents".** Failing test first: the three routes are 404, `Workspace` and `OnboardingSession` have no
  `run_mode`, no `Run all agents` button, no `ChainReview`. Then remove the code in section 3, move per-step approve-confident to
  `/steps/{step}/approve-confident` (D7), delete or rewrite every chain test to the step flow. T1's report lists each test file
  removed or rewritten and why. Keeps the old step order; suite green at the commit.
- **T2 Mapping first.** Step order (section 2); `register_feed(queue_glossary=...)`; `record_mappings(send_back=...)`; mapping job
  writes `cannot_map`; `Workspace.cannot_map`; Approve & next blocked by an undecided cannot-map column; Ignore from the cannot-map
  row (existing item endpoint); completion report and `StepView` labels. Tests: cannot-map items for unmapped columns, no
  glossary run queued at registration or by the mapping step in the onboarding, order, Back, approve blocked.
- **T3 Request glossary element.** Migration 0040 (section 6); `column_requested` in `builder_input()`; `request_glossary()` and its
  endpoint; D5 in `decide_element()`; D6 in the glossary step; D9 lists on the completion report, feed page and product page.
  Tests: Request path, Ignore path, request while a run is active, go-live with a requested column pending, approval mid-onboarding
  yields its mapping proposal, approval after go-live yields it and the load is refused until the new go-live, a requested column
  is never built for any other column, migration up and down. Commit-visibility test for the new endpoint.
- **T4 Element link.** `ElementLink` in `apps/ui-shared`, used at the four places; vitest: hover and focus show name, definition,
  data type, aliases; click target is the Glossary element; Esc closes; Tab reaches it.
- **T5 Transformation rejections.** D8. Failing test first: a rejected transformation with a reason is proposed again today.
- **T6 Finish.** Demo story and `docs/demo.md` (no Run all agents; one cannot-map column, Request it, approve it; hover a
  mapping to see its definition); `CLAUDE.md` (steps, the new rule on registration and requests, 7.13 notes); `docs/ui-polish.md`;
  `BUILD_PLAN.md`; `pytest -q -n 6`, both UI suites, the three Playwright stories (summary lines pasted); `graphify update .`;
  push `phase-7.22`; do not merge. Tell the user to run `alembic upgrade head` on dev and on Replit (0040).

## Out of scope

Taking back a glossary request; asking for several requested columns in one click; a MappingAgent run after an element approval;
changing which element a mapping uses; steps 12 to 15; the legacy workbench screens (Q1); deleting `proposal_bases` or
`run_mode`; a chain of any kind.
