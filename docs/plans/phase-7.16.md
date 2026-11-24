# Phase 7.16: demo data for the reports, and the recorded erase of profiling history

Goal: (T0) make 7.15's reports show something in a demo (a preview of today, and 21 days of real history with one weekday
pattern), then (T1, T2) let a person erase old profiles of a feed, only through one database function, recorded and shown
in the audit view. Branch `phase-7.16` from `main` (7.15 is merged). Migration: **0036** (T1 only). Out of scope: an erase of
anything but profiles, an erase of the latest profile, email.

## 1. What exists (read before designing)

- `feed_profiles` (0014): append-only (`amaze_refuse_update_delete()`; since 0027 it first asks `amaze_may_delete()`), one row
  per profile of a feed, version = the feed's highest + 1 (`amaze_guard_feed_version()`), so keeping the latest profile always
  keeps the version sequence intact.
- Rows that point at a profile with a foreign key: `feed_signoffs.profile_id` (and a registration points at its sign-off),
  `glossary_runs.profile_id`, `loads.arrival_profile_id`. `code_versions` and `reports` hold no profile id today.
- `amaze_delete_feed()` (0027): the model for an exception to rules 1 and 7: one function, a session setting, a record.
- `DailyFetch` (apps/api/jobs.py) is the real pipeline from the fetch to steps 12, 13, 15 and 20. `today` is already an
  argument; `LoadRepository.create(received_at=...)` already takes a time (tests use it). Reports and patterns count a load by
  `loads.received_at`.
- Reports (7.15): `build_numbers()` reads only; `POST /api/amaze/reports` refuses a period that has not ended (422).
- The audit view (7.15, `core/steps/audit.py`): decisions and reruns of a month; the CSV is the same rows.

## 2. Design

### 2.1 T0a: preview of today so far

`GET /api/amaze/reports/preview?role=` (any backend role; the signed-in person; operations may name `subject=`) returns the
`ReportNumbers` of **today's UTC day so far**: the same `build_numbers()` as a report, with `Period.daily(today)`. It stores no
`reports` row, sends no notification and makes **no model call** (so no narrative, and no `proposal_log` row): a preview is
numbers. The Reports screen gets "Preview today so far", shown with a banner "Preview: not stored" and the same numbers
table as a report. Stored reports stay past periods only: the 422 for a period that has not ended is unchanged.

### 2.2 T0b: `python -m core.seed --demo-history`

The security master (`syn_security_master`) is onboarded in the demo by a person (upload, profile, chain, go-live). The seed
needs that feed registered with a go-live; otherwise it stops with a typed refusal ("onboard the security master first").
It then runs the **real daily pipeline** (`DailyFetch.fetch_and_load`: fetch, step 12, the engine, steps 15 and 20) for each of
the 21 days before today, oldest first, with:

- an **injected clock**: `DailyFetch.clock` (default none = now) gives `LoadRepository.create(received_at=...)` the moment
  of that day (09:00 UTC); `today` is the day (the blob key, hypercare);
- an **injected connector**: a stand-in `SourceConnector` that returns that day's file (the first fetched file of the feed
  with a different `BEST_SALES` in row 1 each day, so no day is a byte-for-byte resend, and on Mondays eight negative
  `BEST_SALES` values, so `best_sales_not_negative` flags 8 of 70 rows: trust 88.6, held, as in the demo);
- **no insert** into any guarded table: every row is written by the same steps a delivery uses.

Three Mondays fall in any 21 days (`PATTERN_MIN_LOADS` is 3), so the report shows one `weekday` pattern, Monday, 3 of 3.
Safe to run again: a day that has a scheduled load is skipped. Documented in `docs/demo.md`.

### 2.3 T1: `amaze_erase_profiles(feed, before, reason, by)` (migration 0036)

A database function, the only path that deletes a `feed_profiles` row. It locks the feed row (`FOR NO KEY UPDATE`, the first
lock of the lock order), then looks at each profile of the feed made before `before`, and **keeps** it if any of these holds
(`amaze_profile_keep_reason(profile)`, one function the page reads too; the first that holds is the reason):

| Reason | When |
|---|---|
| `latest` | it is the feed's highest version |
| `registration` | a registration's sign-off is of it |
| `signoff` | a sign-off is of it (the foreign key would refuse the delete anyway) |
| `glossary_run` | a glossary run was queued on it |
| `load_arrival` | a load's `arrival_profile_id` is it |
| `code_version` | a code version's files mention its id |
| `report` | a report's numbers mention its id |

The rest are deleted inside the function. It then writes one row to `profile_erasures` and returns it as JSON. Refusals,
each a named error code: no reason or no person; a feed that does not exist; **nothing to erase** (every profile before
`before` is kept, or there is none): no record is written. `amaze_may_delete()` (0027) gets one more branch: a
`feed_profiles` row may be deleted while its id is in `pg_temp.amaze_erasable`, a temp table the function creates, fills with
only the profiles it decided to erase, and drops before it returns. Nothing else can delete a profile; TRUNCATE is still
refused.

`profile_erasures` (new, append-only, no FK on the feed, like `feed_deletions`): `id, at, feed_id, feed_code, before,
by, reason, erased_count, erased_ids, erased_versions, kept (id -> reason), kept_count`. The database checks that the counts
match the arrays and that the reason and the person are not blank.

### 2.4 T2: the screen and the audit

- Step `core/steps/profile_erase.py`: `erase_profiles(session, feed_id, user, before_version, reason)` (the owner of the feed
  or operations, as for delete/pause). Typed results: `ProfilesErased`, `NothingToErase` (with what was kept and why), and
  named errors: `ReasonNeeded`, `NotOwnerOrOperations`, `FeedNotFound`, `UnknownVersion`.
  `POST /api/amaze/feeds/{id}/profiles/erase` `{before_version, reason}`. `before` = the `created_at` of that version, so
  "erase profiles older than version N".
- The feed page's History lists every profile with its keep reason (read from `amaze_profile_keep_reason`) and every erased
  version as **"Profile 2: erased by Olive Operator, 2026-11-24: reason"**. To erase: a native select "Erase profiles older
  than", the list of what would go and what is kept, a reason, then "Erase" and "Confirm erase".
- Audit: `audit_rows()` also returns one row per erasure of the month (`kind = erasure`, `decision = erased`, item `profiles`,
  who, the reason as the note, the feed, the erased versions in the summary). The feed and person filters apply; the CSV has
  the same rows.

## 3. Assumptions (stop and ask if one is wrong)

1. The preview is numbers only: no narrative, no stored row, no notice. A narrative needs a logged model call, and a log row
   is a write; the preview must write nothing.
2. A preview is for the signed-in person's own role view; operations may preview another person (as for a new version).
3. `--demo-history` needs the demo's onboarded security master (code `syn_security_master`, a go-live, the rule
   `best_sales_not_negative`, a `BEST_SALES` column); it does not onboard it. It refuses (typed, exit 1) otherwise. If the
   feed was onboarded with other rules, the Mondays still load, but no rule flags and there is no pattern.
4. "Injected clock" means the load's `received_at` and the fetch day, not the database clock: `created_at` columns still show
   the real time (a certification of a past load says it was made today). Reports and patterns count by `received_at`.
5. The 21 days are the 21 UTC days before today. Any 21 consecutive days hold three of each weekday, so Monday has exactly 3
   failing loads; the other days are clean. The day-of-month dimension cannot reach 3 (the Mondays are 7 days apart).
6. The seed's loads notify the owner and operations as any load in hypercare does (21 notices). Not suppressed.
7. A held Monday load is not rerun by the seed.
8. Version 2 of a profile exists only for a feed whose drift or re-upload made one; the latest profile is never erased. So a
   registered feed with a single profile has nothing to erase: the panel says so and why.
9. A profile that a sign-off names is kept even when no registration follows it (the foreign key forces it); the plan's
   "registration" is the stronger case and gets its own reason.
10. `code_versions` and `reports` hold no profile id today. The check is on their text (the id appears), so a later phase
    that records one is protected already. T1 proves it for a report; a code version needs a go-live chain and is proved by
    the same SQL branch being read in the migration test.
11. Erasing writes nothing to the blob store: a profile's file stays in the fetch store (the profile is a summary of it).
12. The erase function is not a way around rule 1 or 7: a profile is a measurement of a file, not configuration, and no
    approved item depends on it except through the sign-off, glossary run and arrival that keep it. CLAUDE.md gets one
    line under the rule 1/7 exception, as 7.9 did.
13. `amaze_may_delete()` stays `STABLE`; the temp table is session-local, so another connection cannot see it. A caller who
    can create a temp table of that name in their own session can delete profiles directly (the same residual risk as 0027's
    `amaze.deleting_feed`): this is a guard against code paths, not against a database administrator.
14. `profile_erasures.feed_id` has no FK and `amaze_delete_feed()` leaves the records (like `feed_deletions`).
15. Who may erase: the feed's owner or operations (the rule of pause and delete).
16. A feed's History shows erased versions from `profile_erasures`; a deleted feed's page does not exist.

## 4. Tasks (one commit each; tests first, RED then GREEN)

| Task | What | Tests |
|---|---|---|
| plan | this plan, BUILD_PLAN entry | none |
| T0a | `preview_numbers()` route, Reports screen button | preview stores nothing (no `reports`, no notification, no `proposal_log` row); numbers equal a stored report's for the same state; a period that has not ended is still refused to store; UI shows the banner |
| T0b | `DailyFetch.clock`, `apps/api/demo_history.py`, `--demo-history` | 21 loads on 21 days through the real pipeline; the Monday loads held; one weekday pattern (Monday 3 of 3); reports of those days non-zero; run again changes nothing; refused without the feed; no direct insert (no new test helper writes guarded tables) |
| T1 | migration 0036, `ProfileErasureRow`, step-less function tests | kept profiles are refused (latest, registration, sign-off, glossary run, arrival, report); the record is written; nothing else is deletable (direct DELETE, TRUNCATE, other tables inside the window); nothing to erase writes no record; no reason, no person; append-only record |
| T2 | step, API, History panel, audit rows and CSV | erase through the API; typed outcomes; owner or operations only; History shows "erased by"; audit lists it; filters; CSV |
| Finish | `pytest -q -n 6`, both UI suites, all Playwright stories, `demo_story.spec.ts` and `docs/demo.md`, `graphify update .`, push, no merge | |

## 5. Risks

- The seed's day files are made from the feed's first fetched file: if that file has no `BEST_SALES` column the seed refuses
  (named), it does not guess another column.
- An erase cannot be undone. The function refuses without a reason and a person, the screen asks twice, and the record names
  every id and version.
- The demo story's feed has two profiles at most, both kept: the panel shows the "nothing to erase" outcome there. The erase
  itself is proved by the tests and a component test.

## 6. Deferred

Erasing other history (loads, standard records); an erase of the latest profile; scheduled retention; email.

## 7. Outcome and deviations

- Commits: plan `98ffcc2`, T0a `c346726`, T0b `7cf5044`, T1 `60ca0e9`, T2 `39ce9f0`; the finish: see `git log phase-7.16`.
- Assumption 3 changed in detail: the demo defaults also name the feed's date column (`RECORD_DATE`, written `31-12-2019`),
  because a constant provider date would make a second pattern (a provider-date one) beside the weekday one.
- Found by running the story, not by the unit tests: (1) an approved waiver applies to every later scheduled load, so the
  seed must run before any waiver (the story seeds right after code version 1); (2) the security master's two duplicate rows
  flag `duplicate_key` and `one_row_per_ticker` on every day, and 7.15's patterns report a rule that fails on every delivery
  once per weekday (14 patterns). The seed numbers repeated tickers (`unique_column`) so ordinary days are clean. The
  pattern finder itself is unchanged: a rule that fails on every load of a feed still appears seven times. Worth a decision
  in a later phase ("always failing" is a standing defect, not a weekday pattern).
- Files outside the plan: `apps/api/jobs.py` (the `clock` field), `core/steps/errors.py` (`UnknownProfileVersion`),
  `core/repositories/tables.py` (`ProfileErasureRow`), `core/seed.py` (the flag), `core/steps/feed_page.py`, `audit.py`,
  `apps/api/routers/amaze.py` and `reports.py` (the routes), `e2e/demo_history.py` (the command on the story's database),
  `tests/test_migrations.py` (head 0036, the two 0036 migration tests) and `tests/test_feed_delete.py` (`profile_erasures`
  is a record that outlives the feed, like `feed_deletions`), `CLAUDE.md` (one sentence under the rule 1/7 exception).
- Not done: an erase that deletes something is not shown in the demo story (the demo feed's two profiles are both kept); the
  tests and the component test prove it. The `code_version` keep reason is proved by a code version that names a profile id
  in its files, since no code version records one today.
- The two 0036 migration tests (`tests/test_migrations.py`) were written after the migration, so no RED line was taken for
  them; the 12 database tests were seen failing first (`12 failed`, `function amaze_erase_profiles(...) does not exist`).
