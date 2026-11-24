# Phase 7.13: run all agents ahead, review the chain once

Request: one click runs every onboarding agent ahead; a person reviews the whole chain once and approves top to bottom.
Rule 1 is unchanged: a person approves every item, and nothing becomes configuration unapproved. Rule 2 is unchanged:
no model call in the run engine, in entitlement, or in the code that decides what runs next.

## T0: design (STOP here for review; nothing below T0 is built yet)

### What exists today (read from the code)

- A session walks `profile -> glossary -> mapping -> transformations -> rules -> go_live -> first_load`. Each agent step
  reads **approved** configuration only (`mapping_input` reads `GlossaryRepository.list_current()` and
  `MappingRepository.current_for_feed()`; `transform_input` and `rules_input` read current mappings), so step N+1 cannot
  start before step N is approved. `approve_and_next()` approves the current step and queues the next one.
- One job per session (`uq_onboarding_jobs_one_active_per_session`), a lease, a model-call cap checked when a job starts
  (`max_model_calls_per_onboarding`, 60), the cap counted by `_Counted`.
- The registered-feed rule is enforced at **configuration** insert (`mappings`, `rules`, `transformations`, 0016/0018/0023)
  and at `glossary_runs` insert (0017). A **proposal** insert has no registered-feed or element-exists check. So a draft
  proposal that names a not-yet-approved element is not refused by the database; only the configuration row is.
- The engine, the back-test and the sandbox work on in-memory objects: `_checked(agent_input, ...)` takes
  `agent_input.elements` (source_field, element key, data type) and `agent_input.transformations`; nothing in the
  back-test reads `mappings` or `rules`. A draft back-test therefore needs no write at all.

### Decision 1: how each agent drafts on pending upstream drafts

A new read-only module `core/steps/drafts.py` builds a `DraftConfig` for a feed: the approved configuration plus the
feed's **pending, not stale** proposals, taken as if approved, and the proposal ids it used. It never writes. Each
`*_input()` gets an optional `draft: DraftConfig | None`; `None` is today's behavior, byte for byte.

| Agent | Reads (draft mode) | Draft rule |
|---|---|---|
| Glossary | approved profile (the gate, see decision 4) | no change; it has no draft upstream |
| Mapping | approved glossary + pending `glossary_element` proposals | A pending proposal whose recommended option is a new element becomes a candidate element (built from its `ElementOption`, same scoring through `candidate_list`). A `duplicate_alias` proposal adds its alias to the existing element for scoring. `conflict_to_owner` (no recommended option) offers nothing: that column is **blocked** (decision 3). The mapping proposal's candidates may name a draft element key; the database does not mind (only the `mappings` row needs the element, and that row is written only on approval, after the glossary approval). |
| Transformations | approved mappings + pending `mapping` proposals with a recommended element (not a tie, not `excluded`) | A column is transformable if it is mapped or has a recommended draft mapping. |
| Rules | approved mappings + draft mappings, approved + draft transformations, glossary data types (approved or draft) | `rules_input(draft=...)` builds `elements` and `transformations` from the draft config. `_checked()` back-tests in the locked DuckDB sandbox exactly as today, on those in-memory objects. Nothing is written to `mappings`, `rules` or `transformations`. The proposal stores the back-test, as today. |
| Go-live | **not drafted** | The test load runs the engine, which reads only approved configuration (CLAUDE.md). It runs after the rules are approved, as today ("go-live test load and code version as today"). |

### Decision 2: how a draft records the upstream drafts it used

New append-only table `proposal_bases(proposal_id, base_id)` (migration 0032). Written in the same transaction as the
proposal, right after `Agent.run()` returns (the feed row is locked by the step's `before_write`, so the order is the
existing lock order). Direct bases only:

- mapping draft -> the glossary proposals of every draft element among its candidates;
- transformation draft -> the mapping proposal of its column (if that mapping is a draft);
- rule draft -> the mapping and transformation proposals (drafts) of the columns whose elements the rule names.

A draft built on approved configuration only has no rows. The `before_write` race check of each step (the existing
`_still_mappable`, `_still_as_input` and the transformation equivalent) also compares the bases' current tips: a change
during the call is the existing `ConfigurationChangedMeanwhile` discard (a discard row, nothing written).
Manual items (T7) have no bases in 7.13: a person adds on approved upstream only.

### Decision 3: what "stale" means, and what a chain cannot draft past

A pending draft D is **stale** when, for any base B recorded for D, one of these holds:

1. B was edited (the lineage tip of B is not B, status `superseded`);
2. B was rejected;
3. B is itself stale (derived recursively, the table is acyclic by kind order).

Stale is **derived, never stored**: no UPDATE on any append-only table. An approved or rejected draft is never stale (an
approved proposal is never edited). Re-run, when an edit or rejection of a base happens, in the same transaction as the
edit or rejection:

1. each pending stale draft is rejected by `system` with the fixed note `STALE_NOTE` and an event `draft_stale`;
2. each affected step queues one job (in step order); the step's existing `*_input()` already selects exactly the
   columns that have no mapping and no pending proposal, so only those columns are asked again. Rules gets an
   `only_elements` filter (the elements the stale rules named). Only steps that have stale drafts run again.

`rejected_elements()` and `rejected_keys()` ignore rejections whose decision note is `STALE_NOTE`, so a stale rerun can
propose the same element or key again. (Noticed, not touched: today's Back rerun uses `RERUN_NOTE` and its system
rejection does feed `rejected_elements`. Not changed here: a possible bug to look at in its own task.)

A chain **cannot draft past** an item with no single recommended value: a `conflict_to_owner` glossary item, a mapping
tie, a mapping with no candidate. The column is **blocked**: later agents skip it (it is unmapped in the draft config,
so it is not loaded, as today), the chain records it in `chain_done.detail.blocked`, and the review screen shows it
first. A low-confidence item **with** a recommended value is drafted past and flagged "built on a low-confidence draft".
Once a person decides the blocked item (edit to a single value, approve a choice), the dependents of that column are
drafted by the same rerun path (the column now has no pending proposal downstream).

### Decision 4: where the chain starts, and the profile gate

The profile step stays a gate. Approving the profile registers the feed (owner, licence, visibility: irreversible), and
`glossary_runs`, `mappings`, `rules` and `transformations` all require a registered feed. So the chain is:

```
upload -> ProfilerAgent drafts the summary (as today)
       -> person approves the profile (one click: it registers the feed)
       -> [chain, no clicks] glossary -> mapping -> transformations -> rules drafts
       -> person reviews the chain once, approves top to bottom
       -> go-live prepare (test load, summary) as today -> owner approves -> first load
```

"Run all agents" sets `run_mode = 'chain'` on the session (at upload confirmation, or later from the workspace). In chain
mode the profile approval, instead of entering the glossary step and stopping, queues the glossary job and each finished
draft job queues the next step's job in the same transaction (`_finish_job`). Plain code decides the next step: no model.

Honest consequence for the request's test line "chain from upload to go-live review with no clicks in between": under this
design there is **one** click in the chain, the profile approval. Everything after it runs unattended. See open question Q1.

### Decision 5: the cursor, step states and the items of a step

`current_step` stays the **approval cursor**: the first step not done. Drafted-ahead steps keep the existing states
(`needs_decision` or `ready`) while the cursor is behind them; no new step state. Draft jobs do not call `move_to()`. In
chain mode `approve_and_next()` on the cursor step approves its items, moves the cursor, and **does not** queue the next
agent when that step is already drafted (it queues only when the step is `waiting` or has stale drafts). `_step_items()`
today reads the latest `agent_done` only; with per-column reruns it reads the union of `agent_done` events since the last
full run, one item per lineage tip, system-stale ones hidden.

### Decision 6: the model-call cap and failures

The cap is checked at the start of each chain job, as now (`call_cap_reached` event, step `failed`). In chain mode that
also writes `chain_stopped(reason = call_cap_reached)`; later steps stay `waiting`; the review screen shows the drafted
part and says which steps were not drafted. A failed agent call stops the chain at that step (`chain_stopped(reason =
agent_failed)`); the person re-enters the step as today and the chain resumes from it.

### Decision 7: approval order and "Approve all confident" (T2, T3)

Approval stays one decision per item through the existing `decide_*` functions (one `approvals` row per item, per
approver step). A person may approve in any order on the screen, but **the database refuses** the approval of a draft
whose bases are not all `approved`, and the step maps that to `UpstreamNotApproved` (409). "Approve all confident" per
step approves, for the current person, each item whose confidence is at or above `auto_accept_confidence`, that is not
blocked and whose bases are approved; it calls the same `decide_*` function per item, so it is N decisions by that person,
each logged as `approved` with the person as actor, not a bulk row. With four-eyes on, rules still wait for the checker.

### Triggers and constraints that need a new version (migration 0032, the next number)

| # | Object | Why | Change |
|---|---|---|---|
| 1 | new table `proposal_bases` | decision 2 | append-only (`amaze_refuse_update_delete`), no truncate (`amaze_refuse_truncate`), FK to `proposals`, `proposal_id <> base_id`, same feed, base kind earlier than proposal kind (glossary_element < mapping < transformation < rule), proposal `origin = 'agent'` |
| 2 | `amaze_may_delete()` (0027) and `amaze_delete_feed()` | rule 1/7 exception: a hard-deleted feed must take its `proposal_bases` rows | new version that knows the new table (children first, as it does for the others) |
| 3 | new trigger on `approvals` INSERT (`amaze_require_bases_approved`) | decision 7 | an `approved` decision is refused unless every base of the proposal has status `approved`. A new trigger beside `trg_approvals_not_superseded` (0024), not an edit of it |
| 4 | `onboarding_sessions.run_mode` (new column, `step` or `chain`) and `amaze_guard_onboarding_session()` (0024) | decision 4 | new version: `run_mode` may move `step -> chain`, never back; the feed, creator and creation time stay fixed |
| 5 | `ck_onboarding_events_event` (0024, extended in 0028/0029) | new events | add `chain_started`, `chain_done`, `chain_stopped`, `draft_stale` |

Not changed, and why: `amaze_guard_proposal_change` (a stale draft is rejected, not `superseded`, so no new status path);
`trg_mappings_b_matches_proposal`, `trg_rules_b_matches_proposal`, `trg_transformations_b_matches_proposal` (each fires only
on the configuration row, written on approval, after its upstream is approved); the 0017 glossary-run guard (the chain
starts after registration); the go-live guards of 0018/0023 (go-live is not drafted); `amaze_valid_step_states` (no new
state). Never worked around: if a draft is refused by any of these, the code is wrong, not the trigger.

## Assumptions (numbered; stop and ask if one is wrong)

1. The profile approval stays a person's click and the chain starts after it (decision 4, Q1).
2. Staleness is derived from `proposal_bases` and the lineage; no stored flag, no UPDATE of an append-only table.
3. Only direct bases are recorded; transitivity is computed on read.
4. A stale draft is rejected by `system` with `STALE_NOTE`; the "not proposed again" memory ignores that note.
5. One job at a time per session stays (the existing unique index); the chain is sequential.
6. Draft mode adds an optional argument to `mapping_input`, `transform_input`, `rules_input`; `None` is today's code path.
7. A blocked column (no single recommended value) is skipped by later agents, not a reason to stop the whole chain (Q2).
8. Low confidence with a recommended value is drafted past and flagged; no value means blocked (Q3).
9. Go-live, its test load and the first load are untouched (out of scope: steps 12-15, approval in advance).
10. Manual items (T7) keep working on approved upstream only; their `proposal_bases` stay empty.
11. A rule's draft back-test runs in `core/engine/sandbox.py` on in-memory draft elements; no row is written to `mappings`,
    `rules` or `transformations` before the approval of its proposal.
12. "Approve all confident" is a loop of the existing per-item decisions by the same person, not a new kind of approval.
13. The chain review is a second view in the existing onboarding workspace (backend-ui), not a new app or route family.
14. `proposal_log` stays one row per answered call; a chain of N agent runs has N call rows; every agent-made draft points
    at its call row (existing `ck_proposals_origin_matches_source`).
15. The next migration is 0032. Tell the user to run `alembic upgrade head` on dev and on Replit after T1a.

## Review answers (2026-11-24, T0 approved)

- **Q1 profile gate: kept.** The profile approval is the one click: it registers the feed, and everything after it runs
  with no click until the chain review. The test "chain from upload to go-live review" therefore has exactly one person
  action before the review: approving the profile. `docs/demo.md` says so.
- **Q2: block only the column.** The chain continues for every other column (assumption 7).
- **Q3: yes.** Only "no single recommended value" blocks. Low-confidence drafts (below `auto_accept_confidence`, with a
  value) are flagged, and the review lists them first in each step, after the blocked ones.
- **Q4: three T1 commits** (T1a, T1b, T1c).
- **New T4 (the defect found in T0):** a rejection by `system` (Back's rerun, and a stale rerun) is not a person's
  judgement, so `rejected_elements()` (and, for the same reason, `rejected_keys()` for rules) no longer counts it; a
  person's rejection still counts. This replaces the `STALE_NOTE` filter of decision 3 and assumption 4: the filter is
  on who decided (`system`), not on the note. Failing test first.

## Tasks (after the review)

- **T1a** migration 0032 (the table above), `proposal_bases` repository, `run_mode`, events. Tests: bases guard at the database
  (approve a dependent before its base: refused), append-only, feed delete removes bases, `run_mode` one way.
- **T1b** `drafts.py`, draft mode for mapping, transformations, rules; bases written; the race check on bases.
  Tests: mapping to a draft element, rule back-test on draft mappings with zero rows in `mappings`/`rules`.
- **T1c** chain worker in `_finish_job`, `run_mode`, `POST /api/amaze/onboardings/{id}/run-all`, cap and failure stops.
  Tests: chain from the profile approval to drafted rules with no clicks (mock), cap honoured, one logged call per draft.
- **T2** chain review: `GET .../chain`, per-item actions (existing routes), `POST .../chain/approve-confident`, backend-ui
  view, vitest, commit-visibility test per committing endpoint (happy path only; state the added time).
- **T3** `UpstreamNotApproved` (409), stale derivation, rerun on edit or reject, `_step_items` union, `approve_and_next`
  in chain mode. Tests: stale on upstream edit, no mapping or rule row before its approval.
- **T4** defect: after Back, the mapping agent does not re-propose an element (test first, RED); `rejected_elements()`
  and `rejected_keys()` skip rejections decided by `system`; a person's rejection still counts.
- **T5** finish: `pytest -q -n 6`, both UI suites, all Playwright stories; extend `demo_story.spec.ts`; rename
  `docs/demo-7.12.md` to `docs/demo.md` and add this phase; `graphify update .`; push `phase-7.13`; do not merge.

## Out of scope

Approval in advance (option B of the request); any change to steps 12-15; drafting the go-live summary; manual items that
build on drafts; a parallel chain (one job per session stays).
