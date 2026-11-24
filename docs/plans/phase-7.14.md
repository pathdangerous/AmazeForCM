# Phase 7.14: exceptions, agent-proposed fixes, bulk approval, rerun

Goal: flagged rows become **exceptions**; an agent proposes a fix per **group** of exceptions; a person approves in bulk;
the held load's file is **rerun** under the new configuration; owners are told; the Overview counts fixes made by agents
and by hand. An agent never changes data (rule 1); no record is ever updated (the tables stay append-only).

Branch `phase-7.14` from `main`. Out of scope: owner reports and recurring-failure patterns (7.15). T0 is design only:
**stop after T0** for the sponsor's answers to section 7.

## 1. What exists today (read before designing)

- `standard_records` (migration 0018): one row per file row of a loaded file: `values` (element key to value), `flags`
  (rule keys, plus `duplicate_key`), append-only; written only while the load is `received`.
- Step 15 (`s15_certify.py`): trust score = 100 x unflagged rows / rows. At or above `TRUST_RELEASE_THRESHOLD` the load is
  `released`; below it `held`, and `consumers_of()` + operations + backend team are told. The feed's registered owner is
  **not** told today (T4 adds it). The database recounts rows and flagged rows when it writes a certification (0019, G14).
- Step 12 (`s12_arrival.py`): `check_arrival()` returns `resent_quarantined` when the file's SHA-256 equals that of an
  earlier scheduled load of the feed that is `loaded`, `released` or `held` (`LoadRepository.scheduled_hashes()`).
  So today the same file can never be loaded again while its first load is `held`.
- `plan_load()` runs a scheduled load only under the configuration the latest go-live approved (mapping, rule and
  transformation ids; a code version is generated from them, 0030). A configuration that changed gives
  `configuration_changed` and the load is not run as approved.
- Transformations: `transformation` proposal (agent: `TransformationAgent`, or manual) approved by the data steward
  becomes a `transformations` row (version n+1); the engine applies approved transformations (no model).
- Notifier port (`ports/notify`), `consumers_of()`, the Overview (`apps/api/routers/overview.py`, `ActionCounts` from 7.12 T4
  counts profile/drift actions by agent and by hand).

## 2. T0 design

### 2.1 Exception and exception group (assumptions 1 to 5)

- **Exception** = one flagged row of one load and one rule that flagged it: the pair (`standard_records.id`, rule key in
  its `flags`). A row flagged by two rules is two exceptions. It is a *view* over `standard_records`; nothing is stored.
  Not an exception: a row that did not reach the standard model (a failed load has no records; it is `failed_rolled_back`).
- **Exception group** = one rule key plus one **value pattern**, within one load. The pattern is the *shape* of the
  values the rule looks at: for each element key the rule's SQL names (a mapped element key found as a whole word in
  the rule SQL; for `duplicate_key`, the feed's key columns' elements), the value text with digit runs replaced by `9`,
  upper-case letter runs by `A`, lower-case runs by `a`, other characters kept; a missing value is `<empty>`; several
  elements are joined with ` | ` in element key order. A rule that names no element has the single pattern `(row)`.
  Example: `best_sales_is_not_negative` on `-12.50` and `-3.1` both give `-9.9`, one group; `ABC-123` gives `A-9`.
  The pattern is computed in SQL (regexp_replace; PostgreSQL 14 features only), not in Python, so counting a 100 000 row
  load does not load its rows.
- A group has: load, rule key, pattern, count of exceptions, count of distinct rows, up to 5 sample rows (row number and
  the values), a stable `group_key` (SHA-256 of `rule_key` + `\x1f` + pattern, hex, first 16).
- The view is read only (T1): `GET /api/amaze/feeds/{id}/exceptions` (per feed: its loads with a flagged count) and
  `GET /api/amaze/loads/{id}/exceptions?rule=&limit=&offset=` (the groups, largest first, 50 per page, samples included).

### 2.2 Fixes (T2): what the agent may propose per group

`ExceptionFixAgent` (new, autonomy A2, approver `data_steward`, one prompt file `exception_fix.1.yaml`). Input per call:
one load's groups, at most 20 (the 20 largest), each with rule key, the rule's description and SQL, pattern, count, the
5 samples, and the feed's current transformations. No whole glossary and no more rows (a test enforces the cap, as for
candidates). It answers for each group exactly one of:

| Fix | What an approval does | Goes through |
|---|---|---|
| `transformation` | stores a new `transformations` row (version n+1) for the source field | the **existing** `transformation` proposal and `decide_transformation()`; a new code version follows at the next go-live (0030) |
| `waive` | writes an `exception_resolutions` row `waived` with the steward's reason | new; the engine then records the rule key in `waived` instead of `flags` for rows matching the group (2.4) |
| `refer_to_provider` | writes an `exception_resolutions` row `referred` with a note | new; nothing is changed; 7.15's owner report will read these rows |

A group the agent cannot fix gets the negative outcome `no_fix_proposed` (typed, with the agent's reason; the group stays
open). An agent proposal is two things in one logged call (rule 4): a `transformation` proposal (existing kind, agent
origin, same log row) for a `transformation` fix, or an `exception_fix` proposal (new kind) for `waive` and
`refer_to_provider`. The `exception_fix` payload: `{load_id, rule_key, pattern, group_key, action, reason, note}`.
**Manual fixes** (a person adds one for a group): a transformation by hand uses the existing `add_item()`; a waive or a
refer by hand writes an `exception_fix` proposal with `origin = 'manual'` and an author, no log row (rule 4 logs model
calls; 7.12 T7 pattern). No made-up `proposal_log` row.
Agent flow follows CLAUDE.md's model-call pattern (read-only input, end the read transaction, `Agent.run(...,
before_write=...)`, lock and check again, write): the groups are re-counted in `before_write`; a changed group is
`ExceptionGroupChangedMeanwhile` (409 and a discard row).

### 2.3 Bulk approve (T3)

`POST /api/amaze/loads/{id}/exceptions/decision` with `{group_keys: [...], decision: approved|rejected, note}`: one
transaction, lock order of CLAUDE.md (source lock, feed row, then the proposal rows in id order). For each selected group
it decides that group's pending proposal: a `transformation` proposal through `decide_transformation()`, an `exception_fix`
proposal through a new `decide_exception_fix()`. All or nothing: one refused group (already decided, not a steward,
group gone) refuses the batch with the group named; **four-eyes** applies as in `REQUIRE_FOUR_EYES` (a second approver
step is needed where it is on). Rejection needs a note.

**Rerun** (`POST /api/amaze/loads/{id}/rerun`, a separate explicit action, never a side effect of approving):
creates a new load of the same file under the current configuration, as in 2.5. Typed results:

- `rerun_started` (a new load, `received`) then step 12, step 13, step 15 and step 20 run as for any scheduled load, giving
  the existing `released` or `held_consumers_warned`; if the rerun is held again the result is `rerun_still_held` (with the
  new score, the groups still open, and the previous score) so the UI can say the fix was not enough;
- `rerun_needs_go_live`: the approved configuration is not the one the latest go-live approved (a transformation was
  approved). The owner must approve a new go-live first (existing step 11: prepare with a test load, owner decides);
  nothing is run. The page links to it. See question Q1;
- `nothing_to_rerun`: the load is not `held`; or the file is gone from the store; or no group of it was resolved since;
- `rerun_in_progress`: a `received` load of the feed exists (one received load per feed is a database rule).

Trust score is recomputed by the **existing** certification of the new load (100 x unflagged / rows; waived rows count as
unflagged, 2.4). Release or hold is today's decision. The old load stays `held` for ever (history); the product shows the
latest released load.

### 2.4 Waivers and the append-only records

A waiver never edits a record. At the rerun the engine (step 13, deterministic, no model) reads the feed's current
`waived` resolutions, and for a row whose flagged rule and pattern match it writes the rule key into a new
`standard_records.waived text[]` column **instead of** `flags` (default empty; set only at insert, like every column). So
the row still shows the exception it had, marked waived, and the unchanged recount (`cardinality(flags) > 0`) gives the
score. The pattern is computed by the same function as the view (one definition, in `core/engine`, used by the SQL of the
view and by the engine). Waivers read by `plan_load()` are not part of the go-live's pinned ids (Q2): they reclassify a
flag, they change no value, rule or mapping.

### 2.5 The rerun and step 12's `resent_quarantined` (the hard part)

Today a held load blocks its own file for ever (`scheduled_hashes()` includes `held`). Approach (answer to "how does a
rerun pass `resent_quarantined`, explicit, recorded, never silent"):

1. A rerun is a **new `loads` row** with `purpose = 'scheduled'` (so every trigger, step 12, 13, 15 and 20 apply
   unchanged), the same `file_ref` and `fetch_id` as the held load, its own go-live (the latest) and two new columns,
   `rerun_of_load_id` (nullable FK to `loads`) and `rerun_reason` (non-blank when `rerun_of_load_id` is set).
   A new purpose value `rerun` was considered and rejected: every 0019 trigger and `load_certifications` tests
   `purpose = 'scheduled'`; a new value means rewriting four trigger functions for no gain, since `rerun_of_load_id` is
   just as explicit and queryable. Q3 asks you to confirm.
2. A trigger (migration 0032) allows `rerun_of_load_id` only at insert, only when the target is a `held` load of the
   same feed with a certification outcome `held_consumers_warned`, the new load is scheduled, and the reason is
   non-blank; it is never updated afterwards (the 0019 arrival fields are set once; these two columns are insert-only).
   Rerunning a `released`, `quarantined`, `failed_rolled_back` or `abandoned` load is refused by the database.
3. `ArrivalPlan` gets `rerun_of: UUID | None`. For a rerun `earlier` leaves out **only that one load's hash**; every other
   earlier delivery still counts, so a rerun cannot hide a real resend of some other file, and a rerun of load A whose bytes
   equal loaded load B's bytes is still `resent_quarantined`. The structure checks (missing, additive, drift) run as usual.
4. The arrival detail on the new load records `{"rerun_of": "<id>", "reason": "<text>", "sha256_matches_rerun_of": true}`,
   the notification names it, and the load's row shows "Rerun of load X: <reason>". Never silent: there is no code path
   that skips the SHA check except through the `rerun_of` the database has validated.
5. Reason text: the steward's note on the bulk decision (required), or "Exceptions resolved: n groups".

### 2.6 New tables, migration `0032`, append-only (PostgreSQL 14 features only)

- `exception_resolutions`: `id`, `created_at`, `feed_id`, `load_id` (the held load the group was seen in), `rule_key`,
  `pattern`, `group_key`, `action` (`waived` | `referred`), `reason` (non-blank), `proposal_id` (must be an approved
  `exception_fix` proposal of the same feed, action matching), `decided_by`, `applied_by` (`agent` | `manual`, copied
  from the proposal's origin by the database, not trusted from the caller). Append-only (UPDATE, DELETE, TRUNCATE
  refused; the 7.9 `amaze_delete_feed()` exception applies as for every feed table). A resolution is superseded by a
  later row for the same feed, rule and pattern (the latest wins; `referred` after `waived` un-waives it): never edited.
- `proposals.kind` gains `exception_fix`; `MANUAL_KINDS` gains it. A check: an `exception_fix` proposal has a feed and
  the keys above in its payload; a rejected or pending one never makes a resolution row.
- `loads`: `rerun_of_load_id`, `rerun_reason` (2.5). `standard_records`: `waived text[] not null default '{}'` (2.4).
- `exception_runs` is **not** added: the agent's call is already in `proposal_log`, and the counts come from proposals.
- Downgrade stops naming 0032 while any `exception_resolutions` row, an `exception_fix` proposal or a rerun load exists
  (the 0031 pattern).
- Run on dev and on Replit: `alembic upgrade head` (I tell you at T2, when 0032 first lands).

### 2.7 Owner notified (T4)

On hold (`_certify`), additionally tell the feed's registered owner (`FeedRegistrationRepository.current().owner_id`),
the data steward(s) and operations, once, with the count of exception groups: "Load held: <feed>: 3 groups, 5 rules flagged
..." and a link to the exceptions view. On resolution (a bulk approval) tell the owner: "<n> exception groups resolved by
<steward>: <a> by agent proposal, <m> by hand; rerun pending/started". After a rerun, tell the owner and the consumers
whether it was released or still held. All through `Notifier`; nothing new in the port.

### 2.8 Counts on the Overview (T4)

`ExceptionCounts` next to `ActionCounts`: groups resolved in the period `by_agent` (the approved proposal's origin is
`agent`, including transformations an agent proposed for a group) and `by_hand` (`manual`), plus `open` (groups of
currently held loads with no approved fix) and `reruns` (released / still held). Counted from `proposals` and
`exception_resolutions`; nothing new is stored.

## 3. Typed results (rule 3)

`core/steps/exceptions.py`: `fixes_proposed` | `no_fix_proposed` (per group, with reason); `fixes_applied` |
`group_changed_meanwhile` | `nothing_selected`; `rerun_started` | `rerun_still_held` | `rerun_released` |
`rerun_needs_go_live` | `nothing_to_rerun` | `rerun_in_progress`. Refused input (not a steward, an undecided note) are
`StepInputError`s mapped to 4xx in `apps/api/transactions.py`; named conflicts 409, never 500.

## 4. Assumptions (stop and ask if one is wrong)

1. An exception is a (record, rule) pair; there is no table of exceptions (a view over `standard_records`).
2. A group is rule key + value pattern within one load; the pattern is the digit/letter shape of the elements the rule's SQL
   names (2.1), computed in SQL.
3. Groups are shown largest first, 50 a page; samples are 5 rows; the agent sees at most the 20 largest groups of one load.
4. A rule whose SQL names no mapped element uses the pattern `(row)`: one group per rule.
5. `duplicate_key` is a rule key like any other; its elements are the feed's key columns' elements.
6. Only a `held` load has exceptions to resolve. A released load's flagged rows are shown, read-only, with no actions.
7. The agent's fix kinds are exactly `transformation`, `waive`, `refer_to_provider` (the requested three); no rule change
   and no remap (listed under Deferred).
8. The approver is `data_steward` for all three kinds; four-eyes follows `REQUIRE_FOUR_EYES`.
9. A rerun is a new scheduled load of the same file with `rerun_of_load_id` and a reason (2.5), never a new purpose value.
10. The rerun skips the SHA check against **only** its `rerun_of` load.
11. The rerun runs under the *current* configuration; if that is not what the latest go-live approved, nothing runs and
    the result is `rerun_needs_go_live` (the owner approves a go-live; the engine is not changed to bypass step 11).
12. A waiver is applied by the engine as a deterministic read of `exception_resolutions`, not pinned by a go-live.
13. A waived row keeps its exception visible (`waived` list on the record); the score treats it as unflagged.
14. The old held load stays `held` for ever; a rerun never changes it. The product serves the latest released load.
15. No model call in the rerun, the recompute or the release (rule 2).
16. `refer_to_provider` only records; no report is made (7.15).
17. Mock provider: `ExceptionFixAgent` answers by rule from the group: a pattern of negative numbers gets `waive` with the
    reason "negative values are real for this feed" only if a fixture says so; digits-in-text gets a `transformation`
    (the existing `trim`/`cast` step names); anything else `refer_to_provider`; an empty group gets `no_fix_proposed`.
    Deterministic, so tests and the demo repeat.
18. Notifications are in-app (`Notifier`); recipients are read from the database, not hard-coded.
19. UI: backend-ui only: an Exceptions section on the load row of the feed page (group table, sample rows, select groups,
    "Approve selected", "Propose fixes", "Rerun"), and counts on the Overview. No consumer-ui change.
20. No change to the three synthetic-data tests; the demo file `Synthetic_Security_Master_v2.xlsx` (eight negative
    `BEST_SALES` rows) is the held load of the demo.

## 5. Tasks (one commit each; tests first, RED then GREEN pasted)

| Task | What | Tests (tests/test_exceptions_*.py) |
|---|---|---|
| T0 | this design; BUILD_PLAN entry. **STOP** | none |
| T1 | `core/steps/exceptions.py` read side, the two GET endpoints, the Exceptions section (read-only) | groups and counts of a held load; a row with two rules is two exceptions; sample cap 5; paging; pattern examples of 2.1; a load with no flags has no groups; `duplicate_key`; no query loads all rows (row count 5 000, one SQL query) |
| T2 | migration 0032, `ExceptionFixAgent` + prompt + mock, `exception_fix` kind, `decide_exception_fix()`, manual fix, resolutions | `fixes_proposed`, `no_fix_proposed`; agent input cap 20 groups and 5 samples; every call has exactly one log row; manual fix has no log row; `exception_resolutions` append-only (UPDATE/DELETE refused); resolution without approved proposal refused by the database; downgrade stops; no connection held during the call |
| T3 | bulk decision, rerun, step 12's `rerun_of`, engine `waived`, release or hold | all-or-nothing batch; `rerun_started`, `rerun_released`, `rerun_still_held`, `rerun_needs_go_live`, `nothing_to_rerun`, `rerun_in_progress`; resend of another file still `resent_quarantined` during a rerun; rerun of a released load refused by the database; rerun recorded with its reason and `rerun_of`; **no `standard_records` row is updated** (row count and values of the held load unchanged, UPDATE refused); waived row counted unflagged and listed in `waived`; score recomputed; commit-visibility test for each committing endpoint (the saved rule) |
| T4 | notifications, Overview counts, demo | owner told on hold and on resolution and after rerun; by-agent vs by-hand counts; Overview shows `open`, `reruns` |
| Finish | `pytest -q -n 6`, both UI suites, all Playwright stories; `demo_story.spec.ts` extended; `docs/demo-7.12.md` renamed `docs/demo.md` with the 7.14 minutes added | |

Commit-visibility tests (memory rule): one per committing endpoint or job, happy path only; the added test time is stated
in each task's report.

## 6. Risks

- A rerun that is held again (`rerun_still_held`) can loop; each rerun needs a resolved group since the last (`nothing_to_rerun`).
- Pattern grouping by the rule's SQL text is a heuristic (a word match on element keys). Rules that use aliases or
  expressions fall back to `(row)`; the view says so. Upgrade path: the rule's own declared elements (later).
- Migration 0032 changes `loads`, `standard_records` and `proposals`; downgrade is blocked once used (as 0031).
- `BUILD_PLAN.md` on `phase-7.13` also adds a section after 7.12; merging both branches needs a hand merge there.

## 7. Questions for you (the T0 stop)

- **Q1.** After a `transformation` fix is approved, the owner must approve a new go-live (step 11) before the rerun
  (assumption 11). Alternative: the steward's approval also counts as the go-live for transformation-only changes. I
  recommend keeping step 11 (the owner approves what runs). OK?
- **Q2.** Waivers are applied by the engine without a new go-live (assumption 12). Alternative: pin them in the go-live too
  (a migration to `feed_go_lives`). I recommend not pinning (they change no value). OK?
- **Q3.** A rerun as `purpose = 'scheduled'` plus `rerun_of_load_id` and a reason (2.5), instead of a new `rerun` purpose
  (which you wrote in the brief: "explicit rerun load purpose"). The first is explicit and recorded, and avoids rewriting four
  triggers; a new purpose value is possible if you prefer it. Which?
- **Q4.** Fix kinds limited to transformation, waive and refer (assumption 7). Deferred: a rule change and a remap. OK?

## 8. Deferred

Owner reports and recurring-failure patterns (7.15); a rule-change and a remap fix; exceptions on a `failed_rolled_back`
load (no records exist); editing a waiver (a new resolution supersedes it instead).
