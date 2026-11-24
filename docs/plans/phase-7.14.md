# Phase 7.14: exceptions, agent-proposed fixes, bulk approval, load rerun

Goal: flagged rows become **exceptions**; an agent proposes a fix per **group** of exceptions; people approve in bulk;
the held load's file is **rerun** under the new configuration; owners are told; the Overview counts fixes made by agents
and by hand. An agent never changes data (rule 1); no record is ever updated (the tables stay append-only).

Branch `phase-7.14`, rebased onto `main` after 7.13 merged. Migrations: **0033** and **0034** (7.13 used 0032; Q7). Out of scope: owner
reports and recurring-failure patterns (7.15). Words: "load rerun" in this plan is a new load of a held file; 7.13's
"stale rerun" (an agent asked again for stale drafts) is a different thing and keeps its name.

**T0 revised (2026-11-24) after the sponsor's answers; STOP for review. No code yet.**

## 1. What exists today

- `standard_records` (0018): one row per file row of a loaded file: `values` (element key to value), `flags` (rule keys,
  plus `duplicate_key`); append-only; written only while the load is `received`.
- Step 15 (`s15_certify.py`): trust score = 100 x unflagged / rows; `released` at or above `TRUST_RELEASE_THRESHOLD`,
  else `held`, and consumers + operations + backend team are told. The feed's registered owner is **not** told today.
  `amaze_guard_load_certification()` (0019, G14) recounts rows and `cardinality(flags) > 0` rows and checks score and outcome.
- Step 12: `resent_quarantined` when the SHA-256 equals that of an earlier scheduled `loaded`/`released`/`held` load of
  the feed (`LoadRepository.scheduled_hashes()`). So today a held load blocks its own file for ever.
- `plan_load()` runs a scheduled load only under the configuration the latest go-live approved (mapping, rule and
  transformation ids; code version, 0030).
- Transformations: a `transformation` proposal (agent or manual), approved by the data steward through
  `decide_transformation()` (`approve_my_steps`), becomes a `transformations` row version n+1.
- 7.13 (merged): `draft_config()` (`core/steps/drafts.py`) takes **every pending** `transformation` proposal of the feed
  as if approved; `proposal_bases` (0032) records the drafts a draft was built on (agent origin only); `stale_drafts()`
  scans every pending proposal of the chain kinds of the feed; `refresh_chain()` runs only for a session in
  `run_mode = 'chain'`; `amaze_require_bases_approved` refuses the approval of a draft whose bases are not approved;
  `amaze_delete_feed()` has a 0032 version that deletes `proposal_bases`. The transformation agent has no
  "not proposed again" memory (only `rejected_elements()` / `rejected_keys()` do, and they skip `system` rejections).
- `Agent.run()` gives every proposal of a call the agent's `approver_roles` (`agents/base.py`, line 174).

## 2. Design

### 2.1 Exception and exception group (T1)

- **Exception** = one flagged row of one load and one rule key in its `flags`. A row flagged by two rules is two
  exceptions. A view over `standard_records`; nothing is stored. A failed load has no records, so no exceptions.
- **Group** = one rule key + one **value pattern**, within one load. Elements of a rule: the mapped element keys found
  as whole words in its SQL; for `duplicate_key`, the elements of the key columns. Pattern of a row: for each of those
  elements in key order, the value text with digit runs as `9`, upper-case runs as `A`, lower-case runs as `a`, other
  characters kept; a missing or blank value `<empty>`; joined with ` | `. No element found: `(row)`.
  Example: `-12.50` and `-3.1` both `-9.9`; `ABC-123` is `A-9`.
- **One definition**: an immutable SQL function `amaze_value_pattern(values jsonb, element_keys text[]) RETURNS text`
  (migration 0033, `regexp_replace`, PostgreSQL 14 features only). The view's SQL and a database guard (2.4) call it; the
  engine's Python twin `core/engine/pattern.py` is checked against it by a parity test on the same inputs.
- A group shows: load, rule key, its description, elements, pattern, exceptions, distinct rows, up to 5 sample rows,
  `group_key` (first 16 hex of SHA-256 of `rule_key` + `\x1f` + pattern), and its state (`open`, `fix_pending`,
  `waiting_for_owner`, `resolved`, `referred`).
- `GET /api/amaze/feeds/{id}/exceptions` (the feed's loads with flagged, waived and open-group counts) and
  `GET /api/amaze/loads/{id}/exceptions?rule=&limit=&offset=` (groups largest first, 50 a page). Read only.

### 2.2 Fixes (T2)

`ExceptionFixAgent` (new, A2, prompt `exception_fix.1.yaml`). Input: one held load's groups, at most the 20 largest,
each with rule key, description and SQL, pattern, counts, 5 samples, the column's current transformation, and the fixes a
**person** rejected before for that group (a `system` rejection is no judgement, as in 7.13 T4). A test caps the input.
One answer per group:

| Fix | Proposal | Approvers (in order) | An approval writes |
|---|---|---|---|
| `transformation` | existing kind `transformation`, payload as today plus `exception_group: {load_id, group_key}` | data steward | `transformations` row version n+1 (`decide_transformation()`); the load rerun then needs a go-live (Q1) |
| `waive` | new kind `exception_fix`, `action: waive`, reason | data steward, then the **data owner named at registration** (Q2) | after the owner step: an `exception_resolutions` row `waived` |
| `refer_to_provider` | `exception_fix`, `action: refer`, note | data steward | an `exception_resolutions` row `referred` (7.15 reads them) |

- `no_fix_proposed` (typed, per group, with the reason: the agent found none, `transformation_pending` when the column
  already has a pending transformation proposal (one per column, as today), or `rejected_before`).
- **Approvers per proposal.** `Agent.run()` gives every proposal the agent's roles. Change in `agents/base.py`: an agent
  may define `roles_for(draft) -> tuple[Role, ...]`, which must return a non-empty **prefix** of its declared
  `approver_roles` (checked when the proposals are made, refused otherwise). Default: all of them, so every existing
  agent is unchanged. `ExceptionFixAgent.approver_roles = ("data_steward", "data_owner")`; `roles_for` gives
  `("data_steward",)` for transformation and refer, the full tuple for waive.
- **Manual fixes.** A transformation by hand: the existing `add_item()`, with `exception_group` in the payload. A waive or
  refer by hand: an `exception_fix` proposal with `origin = 'manual'`, an author, no log row, the same approvers (rule 4
  logs model calls only; 7.12 T7).
- The model-call pattern of CLAUDE.md: read-only `exception_fix_input()`, end the transaction, `Agent.run(...,
  before_write=...)` re-counts the groups under the feed lock; a change is `ExceptionGroupChangedMeanwhile` (409, a
  discard row, nothing written).

### 2.3 Where 7.13 touches transformation proposals (decided here)

1. **`draft_config()` must not build on an exception fix.** Today it takes every pending transformation of the feed. A
   fix is against the live configuration and is decided on the exceptions view, not in a chain review. If a chain is
   running on the same feed (a `structure_change` onboarding), a rules draft would otherwise record the fix as a base,
   invisible in that review, and the steward's rejection of the fix would make the rule draft stale. Change: `draft_config()`
   skips pending transformations whose payload has `exception_group`. Test: a chain rules draft on a feed with a
   pending fix has no base on it.
2. **`proposal_bases`**: an exception fix is built on approved configuration only, so it has no bases (7.13 assumption 10
   for manual items; the same for this agent). `amaze_require_bases_approved` therefore always passes it. No change.
3. **`stale_drafts()` / `refresh_chain()`**: a fix has no bases, so it is never stale. Deciding a fix happens outside any
   onboarding session, so `refresh_chain()` is not called. No other pending draft can have a fix as a base (point 1).
   No change. Test: rejecting a fix rejects nothing by `system`.
4. **`run_mode`**: not touched. A fix belongs to no session.
5. **One pending transformation per column**: an onboarding draft and a fix cannot be pending on the same column at once
   (TransformationAlreadyProposed, unchanged). The fix agent skips such a group (`transformation_pending`); the onboarding
   agent skips a column with a pending fix (today's `transform_input` already does).
6. **Edit of a fix** (`edit_item()`, transformation is editable): the edit copies `exception_group` to the new version, as
   7.13 copies bases. Test.
7. **Known limit, not fixed:** a pending chain rule draft back-tested on transformation v1 is not stale when a fix approves
   v2 (stale follows drafts, not approved versions). The go-live test load runs the real v2 before anything is live.

### 2.4 Waivers, append-only records, certification (Q2)

- A waiver never edits a record. At the load rerun the engine (step 13, no model) reads the feed's current `waived`
  resolutions (`plan_load()` puts them in `LoadPlan.waivers`); for a row whose flagged rule and pattern match, the rule
  key goes into a new `standard_records.waived text[]` (default `{}`, set at insert only) **instead of** `flags`.
- Waivers are **not** pinned by the go-live (Q2): they change no value, rule or mapping; a load rerun after waivers only
  needs no go-live. Each waiver needs both approvals (steward, then the named owner) before its resolution exists.
- Trust score: waived rows count as unflagged. Since `flags` no longer holds a waived key, the score formula is unchanged.
- The certification shows waived rows separately: `load_certifications.waived_rows` (rows with no flag and at least one
  waived key) and `waived_counts` (per rule, from the validated stage). The UI shows "x flagged, y waived, z rows".

**Database changes this needs (0033, new versions, none worked around):**

| # | Object | Change |
|---|---|---|
| D1 | `amaze_guard_load_certification()` (0019, G14) | new version: also recounts `count(*) FILTER (WHERE cardinality(flags) = 0 AND cardinality(waived) > 0)` and refuses a certification whose `waived_rows` differs; refuses `flagged_rows + waived_rows > rows`. Score and outcome checks unchanged (score = 100 x (rows - flagged) / rows). |
| D2 | `load_certifications` | new columns `waived_rows integer not null default 0 check >= 0`, `waived_counts jsonb not null default '{}'`. Earlier rows have 0 and `{}` (they had no waivers: true). |
| D3 | `standard_records` | new column `waived text[] not null default '{}'`; check `NOT (flags && waived)` (a key is flagged or waived, never both). |
| D4 | new trigger `trg_standard_records_b_waived` (BEFORE INSERT, `WHEN cardinality(NEW.waived) > 0`) | each waived key has a current `waived` resolution of the feed for that rule whose pattern equals `amaze_value_pattern(NEW.values, resolution.element_keys)`. The append-only guard (0018) is untouched. |
| D5 | new trigger on `approvals` INSERT, `trg_approvals_b_waiver_owner` | the `data_owner` step of an `exception_fix` `waive` proposal is decided only by the feed's current registration owner. Beside 0024's and 0032's triggers, not an edit of them. |

### 2.5 Bulk approve (T3)

`POST /api/amaze/loads/{id}/exceptions/decision` with `{group_keys, decision, note}`. One transaction; lock order of
CLAUDE.md (source lock, feed row, then the proposal rows in id order). For each selected group: its pending
`transformation` fix through `decide_transformation()`, its `exception_fix` through a new `decide_exception_fix()`.
Each is the existing per-item decision (one approvals row per step per proposal, as 7.13's "Approve all confident"): the
steward's approval moves a waiver to `waiting_for_owner` and the owner is told; the owner selects waiver groups and
approves the same way. All or nothing: a refused group (already decided, wrong role, not the named owner, group gone)
refuses the batch naming the group. A rejection needs a note. Four-eyes follows `REQUIRE_FOUR_EYES`.

### 2.6 The load rerun and step 12's `resent_quarantined` (Q3)

`POST /api/amaze/loads/{id}/rerun` with `{reason}`: an explicit action, never a side effect of an approval.

1. A load rerun is a **new `loads` row** with `purpose = 'scheduled'` (every trigger and steps 12, 13, 15 and 20 apply
   unchanged), the held load's `file_ref` and `fetch_id`, the latest go-live, and two new columns: `rerun_of_load_id`
   (FK to `loads`) and `rerun_reason` (non-blank when set).
2. Trigger `trg_loads_b_rerun` (0033, new; 0019's load guards untouched): at insert, `rerun_of_load_id` must be a `held`
   load of the same feed whose certification outcome is `held_consumers_warned`, and the new load must be scheduled
   with a reason; on update, both columns are unchanged. A rerun of a released, quarantined, failed or abandoned load is
   refused by the database.
3. `ArrivalPlan.rerun_of`: `earlier` leaves out **only that load's** hash. A rerun whose bytes equal another
   loaded/released/held load is still `resent_quarantined`. Structure checks run as usual.
4. Recorded, never silent: arrival detail `{"rerun_of", "reason", "sha256_matches_rerun_of"}`; the notification names it;
   the Loads list shows "Rerun of load X: reason". The only path past the hash is a `rerun_of` the database checked.
5. Results (typed): `rerun_started`, then `rerun_released` or `rerun_still_held` (new score, previous score, groups still
   open); `rerun_needs_go_live` (configuration changed by a transformation fix and not yet approved by a go-live: nothing
   runs; the page links to the go-live, Q1); `nothing_to_rerun` (not `held`, file gone, or nothing resolved since the
   last rerun of this file); `rerun_in_progress` (a `received` load of the feed exists).
6. The held load stays `held` for ever; the product serves the latest released load.

### 2.7 Other tables and migration 0033 (PostgreSQL 14 features only)

- `exception_resolutions`: `id`, `created_at`, `feed_id`, `load_id`, `rule_key`, `element_keys text[]`, `pattern`,
  `group_key`, `action` (`waived` | `referred`), `reason` (non-blank), `proposal_id` (an approved `exception_fix` of the
  same feed, same action, every approver step done), `applied_by` (`agent` | `manual`, the database copies it from the
  proposal's origin). Append-only (`amaze_refuse_update_delete`, `amaze_refuse_truncate`). The latest row per feed, rule
  and pattern wins (a `referred` after a `waived` ends the waiver); never edited.
- `proposals.kind` gains `exception_fix` (`ck_proposals_kind` new version); `MANUAL_KINDS` gains it; a check: an
  `exception_fix` has a feed and `load_id`, `rule_key`, `pattern`, `group_key`, `action` in its payload.
- `amaze_delete_feed()`: new version (after 0032's) deleting `exception_resolutions` before the proposals. Loads that
  rerun one another go in the same DELETE (a NO ACTION foreign key is checked at statement end).
- Downgrade stops, naming 0033, while an `exception_resolutions` row, an `exception_fix` proposal, a rerun load, a
  waived record or a certification with `waived_rows > 0` exists (the 0031 pattern).
- I will tell you to run `alembic upgrade head` on dev and on Replit when T2 lands.

### 2.8 Owner notified, Overview counts (T4)

- On hold (`_certify`): also the feed's registered owner and the data stewards, with the number of open groups and a link
  to the exceptions view. On a steward's waiver approval: the owner ("n waivers wait for you"). On resolution: the owner
  ("n groups resolved: a by agent proposal, m by hand"). After a load rerun: owner and consumers, released or still held.
  All through `Notifier`; recipients read from the database.
- Overview `ExceptionCounts`: groups resolved in the period `by_agent` / `by_hand` (the approved proposal's origin),
  `open`, `waiting_for_owner`, and load reruns `released` / `still_held`. Counted from `proposals`,
  `exception_resolutions` and `loads`; nothing new stored.

## 3. Typed results (rule 3)

`core/steps/exceptions.py`: `fixes_proposed` | `no_fix_proposed`; `fixes_decided` | `group_changed_meanwhile` (409) |
`nothing_selected`; `rerun_started` | `rerun_released` | `rerun_still_held` | `rerun_needs_go_live` | `nothing_to_rerun` |
`rerun_in_progress`. Refused input is a `StepInputError` (4xx); named conflicts 409, never 500 (`apps/api/transactions.py`).

## 4. Assumptions (stop and ask if one is wrong)

1. An exception is a (record, rule) pair; no table stores exceptions.
2. A group is rule key + value pattern within one load; the pattern function lives in the database (2.1).
3. Groups 50 a page, largest first; 5 samples; the agent sees at most the 20 largest groups of one load.
4. A rule whose SQL names no mapped element has the one pattern `(row)`.
5. `duplicate_key` is a rule key like any other; its elements are the key columns' elements.
6. Only a `held` load's groups take fixes; a released load's flagged rows are shown read-only.
7. Fix kinds: transformation, waive, refer to provider (Q4). Rule change and remap deferred.
8. Transformation and refer: data steward. Waive: data steward, then the owner named at registration (Q2).
9. `agents/base.py` gains `roles_for()` (a prefix of the declared roles; default all). No other agent changes.
10. A load rerun is `purpose = 'scheduled'` + `rerun_of_load_id` + reason (Q3).
11. A load rerun skips the hash check against only its `rerun_of` load.
12. A load rerun after a transformation fix waits for a go-live by the owner (step 11, unchanged) (Q1).
13. Waivers are read by the engine at the load rerun, not pinned by the go-live (Q2).
14. A waived row keeps its exception visible (`waived`) and counts as unflagged; certification shows waived separately.
15. The held load stays `held`; no record is updated (tests check row values and counts before and after).
16. No model call in the load rerun, the recompute, the release or the pattern (rule 2).
17. `refer_to_provider` only records (7.15 reports).
18. Mock: a pattern of negative numbers gets `waive` ("negative values are valid for this field"); a value with spaces
    or a thousands separator gets a `transformation` (existing `trim` / `remove_thousands_separator` steps if they
    exist, else `refer`); anything else `refer_to_provider`; a group rejected before by a person, `no_fix_proposed`.
19. Notifications in-app (`Notifier`); recipients from the database.
20. UI: backend-ui only: Exceptions on the feed page's load row (groups, samples, select, "Propose fixes", "Approve
    selected", "Rerun") and an owner's "Waivers to approve" list; Overview counts; certification shows waived rows.
21. 7.13 interactions as in 2.3: fixes are excluded from `draft_config()`, have no bases, are never stale.
22. Demo: `Synthetic_Security_Master_v2.xlsx` (eight negative `BEST_SALES` rows) is the held load if its score is below the
    threshold; else the demo sets `TRUST_RELEASE_THRESHOLD` for that run (checked in T4, before the story is written).

## 5. Tasks (one commit each; tests first, RED then GREEN pasted)

| Task | What | Tests |
|---|---|---|
| T0 | this design, BUILD_PLAN entry. **STOP** | none |
| T1 | migration 0033 (`amaze_value_pattern()` only), `core/steps/exceptions.py` read side, the two GETs, the read-only Exceptions section | groups and counts; two rules = two exceptions; sample cap; paging; pattern examples; `duplicate_key`; Python/SQL parity; one query regardless of rows (5 000 rows) |
| T2 | migration 0034 (resolutions, `exception_fix` kind, D2-D5, rerun columns, delete-feed version), `roles_for()`, the agent, prompt, mock, `decide_exception_fix()`, manual fix, `draft_config()` exclusion | `fixes_proposed`, `no_fix_proposed` (each reason); input caps; one log row per call, none for manual; resolutions append-only; resolution without full approval refused; owner step by another person refused; `roles_for` must be a prefix; 2.3 points 1, 3, 6; downgrade stops; no connection during the call |
| T3 | bulk decision, load rerun, `rerun_of` in step 12, engine `waived`, certification waived counts | all or nothing; each rerun result; another file's resend still quarantined; rerun of a released load refused by the database; rerun recorded with reason; **no `standard_records` row updated**; waived row unflagged and listed; D1 refuses a wrong `waived_rows`; commit-visibility test per committing endpoint (time stated) |
| T4 | notifications, Overview counts, demo, `docs/demo.md`, `demo_story.spec.ts` | owner told on hold, waiting waivers, resolution, rerun; by-agent / by-hand counts |
| Finish | `pytest -q -n 6`, both UI suites, all Playwright stories, `graphify update .`, push, no merge | |

**Migration numbering, decided:** two migrations, so the read view can land first: **0033** (T1: `amaze_value_pattern()`)
and **0034** (T2: everything else in 2.4 and 2.7). If you prefer one, T1 computes nothing in SQL until T2 and 0033 holds all.

## 6. Risks

- Pattern elements come from a word match on the rule's SQL; aliases or expressions fall back to `(row)` (the view says so).
- A load rerun held again can loop; each needs a resolution since the last (`nothing_to_rerun`).
- `roles_for()` is a change to the agent base class every agent uses; its default keeps today's behavior and a test
  checks every registered agent still gives its declared roles.
- D4 runs a function per waived row at insert; only rows with a waiver pay it.
- 2.3 point 7 (rule draft not stale after a fix approves a new transformation version) is left as is.

## 7. Questions answered (2026-11-24)

- Q1 yes: a load rerun after a transformation fix waits for the owner's go-live (step 11).
- Q2: waivers skip the go-live pin; approved by the steward and then the data owner named at registration; waived rows
  count as unflagged and are shown separately in the certification (D1, D2).
- Q3: `purpose = 'scheduled'` + `rerun_of_load_id` + reason.
- Q4: transformation, waive, refer are enough for now.

## 8. Review of the revised T0 (2026-11-24): approved

- **Q5:** steward first, then the owner named at registration.
- **Q6:** `roles_for()` is OK. A test checks that every existing agent still gets its full declared roles, and it must
  not touch the model-call path in `Agent.run()` (it is applied where the proposals are made, after the call).
- **Q7:** two migrations: 0033 with T1, 0034 with T2.

## Known gaps (accepted)

- **G1** (2.3 point 7): a pending chain rule draft back-tested on transformation version N does not go stale when an
  exception fix approves version N+1 (stale follows drafts, not approved versions). Mitigation, built in T2: a chain
  rule draft shows "tested on transformation version N" for each transformed column it was back-tested on, so a reviewer
  sees it; the go-live test load runs the real configuration before anything is live.

## 9. Deferred

Owner reports and recurring-failure patterns (7.15); rule-change and remap fixes; exceptions of a failed load; editing a
waiver (a later resolution supersedes it); making a rule draft stale when a fix approves a new transformation version.
