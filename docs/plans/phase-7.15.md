# Phase 7.15: reports, recurring patterns, audit view

Goal: a daily and a monthly report per data owner and per data steward; the recurring failure patterns behind them; and a
monthly audit view of every decision. Numbers come from code only; a model may write the narrative, and the narrative never
changes a number. Branch `phase-7.15` from `main` (7.14 is merged). Migration: **0035**. Out of scope: sending email, and
the erase of profiling history (7.16).

## 1. What exists (read before designing)

- `loads` (scheduled loads; `received_at`, `status`, `rerun_of_load_id` and `rerun_reason` from 0034), `standard_records`
  (`flags`, `waived`), `load_certifications`, `exception_resolutions` (a waiver or a referral of a group, append-only),
  `proposals` and `approvals` (every decision: who, role, when, note), `code_versions`, `feed_registrations` (the owner).
- Drift events live on a load's `arrival_detail["drift"]` (7.12). The Overview (7.14) counts fixes by agent and by hand.
- The Scheduler port has `schedule_daily(job_id, func, hour=)` only; a monthly job is a daily job that acts on the 1st.
- `Agent.run()` is the only path to the model: it logs the call (rule 4). A read-only agent (`approver_roles = ()`) returns
  an output and creates no proposal. The Notifier port (in-app) is the way to tell a person; `email_teams.py` is a stub.

## 2. Design

### 2.1 Patterns (T1): `core/steps/patterns.py`, no model

A **failure** is a scheduled load (status loaded, released or held; not a rerun, which is the same file again) in which a
rule flagged at least one row. A **pattern** is a feed, a rule key, one *dimension* and its value, with at least
`PATTERN_MIN_LOADS` (default 3) failing loads in the last `PATTERN_WINDOW_DAYS` (default 90):

| Dimension | Value | Taken from |
|---|---|---|
| `weekday` | Monday ... Sunday | the load's `received_at`, UTC |
| `day_of_month` | 1 to 31 | the load's `received_at`, UTC |
| `provider_date` | an ISO date | the latest value of the feed's date-typed element in the load's records (the date the provider says the file is for) |

Evidence: for each failing load its id, received date, provider date and flagged rows; and `loads_in_window`, how many loads
the feed had on that weekday / day / provider date, so "3 of 3" reads differently from "3 of 20". Sorted by failing loads, then
feed, rule, dimension, value. **Referred groups**: the latest `exception_resolutions` row per feed, rule and pattern when its
action is `referred` (a later waiver ends it): each appears once, with its load, reason and who decided.

### 2.2 Reports (T2): `core/steps/reports.py`

A report is for one *subject* (a data owner or a data steward), one *kind* (`daily`: one UTC day; `monthly`: one UTC calendar
month) and one period. Subjects: a **data owner** = a person named in a feed's latest registration: their feeds. A **data
steward** = a person with the role (from the identity port): all registered feeds, plus the decisions they made.
`numbers` (JSON, built by code only):

- `totals`: feeds, loads, released, held, failed, quarantined; rows, flagged rows, waived rows; average trust score.
- per feed: its loads and statuses, held loads, exceptions (open, resolved by agent proposal, resolved by hand), waivers,
  referred groups, reruns (released, still held), drift events, patterns.
- `exceptions`, `waivers`, `reruns`, `drift_events`, `patterns`, `referred_groups` (summed and listed once);
  for a steward also `decisions` (approved, rejected, by kind) made in the period.

**Append-only and versioned** (migration 0035): `reports(id, created_at, kind, subject_id, subject_role, period_start,
period_end, version, numbers jsonb, numbers_sha256, narrative, narrative_log_id, generated_by)`. The database sets
`numbers_sha256` from `numbers` itself and refuses a version that is not the next for (kind, subject, role, period); UPDATE,
DELETE and TRUNCATE are refused. The same report is not generated twice by the jobs (one exists: skipped); a person asks for
a **new version** explicitly.

**The narrative** (`ReportSummariserAgent`, A1, read-only: `approver_roles = ()`): its input is the report's numbers (and the
subject's role); its output is a few sentences. The model-call pattern of CLAUDE.md: read and build the numbers, end the
transaction, call the model, then lock and write the report in one transaction; the call row is committed by `Agent.run()`
before the report is written, and the report names it (`narrative_log_id`). **The narrative never changes a number**: the
numbers are built before the call and stored as they were; and a narrative that states a number the report does not hold (a
figure among its digits that is not in `numbers`, after the period's own dates are set aside) is refused: the report is stored
**without** a narrative and the typed outcome says `narrative_refused` with the figure. A model that is unavailable gives
`no_narrative`: the report is still stored.

### 2.3 Jobs (T3)

One daily job `reports` (hour `REPORT_HOUR`, default 6 UTC) registered at every start-up like the daily fetch, only when the
daily job is enabled: it builds yesterday's daily report for every subject, and on the 1st the previous month's monthly
report; skips what exists; tells each subject through the Notifier ("Daily report: 2026-11-23", a link
`#/reports/<id>`); one subject's failure is logged and the others go on. Email stays the stub (`NOTIFY_PROVIDER=email`
unchanged). `POST /api/amaze/reports` (operations or the subject) makes a new version at once.

### 2.4 Audit view (T4): `core/steps/audit.py`

For a month (`YYYY-MM`), one row per **decision**: an `approvals` row (who, role, decision, note, when) with its proposal's
kind and summary, the feed, **by** (`agent` proposed / `manual` / `rule`; a decision by `system` is `system`), the agent (from
the logged call), the **code version** of the feed in force at that moment (the latest `code_versions` row at or before it),
whether it is a **waiver**, and, for a rerun, **one row per rerun load** (who started it, its reason, the load it reruns).
Filters: feed, person. Ordered by time. `GET /api/amaze/audit?month=&feed=&person=` and `GET /api/amaze/audit.csv`
(the same rows, RFC 4180, UTF-8, one header row; a cell that starts with `= + - @` is prefixed with a quote, so a
spreadsheet never runs it). Who started a rerun was not recorded before 7.15: migration 0035 adds `loads.rerun_by`
(nullable for the reruns that exist; the step always sets it).

## 3. Assumptions (stop and ask if one is wrong)

1. A failure is a rule flagging at least one row of a scheduled load that was loaded, released or held; a rerun is not counted.
2. The three dimensions are those of the brief; `provider_date` is the date the file is for, read from the records, not an
   arrival date. A feed with no mapped date element has no provider-date patterns.
3. `PATTERN_MIN_LOADS` (3) and `PATTERN_WINDOW_DAYS` (90) are settings; patterns are computed when asked, never stored apart
   from the report that shows them.
4. A pattern shows `loads_in_window` beside `failing_loads`: it does not hide that a rule failed on one day in ten.
5. Referred groups are read from `exception_resolutions`: the latest row per feed, rule and pattern, `referred`; once each.
6. A data owner's report covers the feeds whose latest registration names them; a steward's covers all registered feeds.
7. Days and months are UTC. A daily report is for the day before the job runs; a monthly one for the month before the 1st.
8. Numbers are counted by code from the tables; no number comes from a model, and none is estimated.
9. The report is append-only; `numbers_sha256` is set by the database; a new version is a new row.
10. The narrative is optional, separate, logged; a refused or missing narrative never blocks the report.
11. The narrative check is: every number written in the narrative (digits, with a decimal point or percent sign allowed) is one
    the report holds, or one of the period's dates. It is a check, not a proof of meaning: the report page shows the numbers
    beside it.
12. `ReportSummariserAgent` is A1 and read-only (no proposal, no approver): it informs, it does not change configuration.
13. A report is read by its subject, and by any backend role (the same rule as the Overview).
14. The audit view lists approvals and reruns only. Creating a proposal is not a decision; a manual proposal's author is shown
    on its decisions.
15. The CSV neutralises formula cells. No file is stored: it is built per request.
16. The report job is one more daily job; there is no monthly scheduler. Email is not sent (stub).
17. A rerun's `rerun_by` is set from this phase; reruns before it show "not recorded".

## 4. Tasks (one commit each; tests first, RED then GREEN)

| Task | What | Tests |
|---|---|---|
| T0 | this plan, BUILD_PLAN entry | none |
| T1 | `patterns.py`, settings | pattern found / not found (below N, a different day, a rerun not counted, a load with no failure); evidence dates; `loads_in_window`; provider date; referred groups once; a waiver ends a referral; deterministic (same twice) |
| T2 | migration 0035 (reports, `rerun_by`), `reports.py`, `ReportSummariserAgent`, API, Reports screen | numbers equal the tables; append-only, versioned, hash; the narrative never changes a number; a refused narrative; no narrative; owner and steward scope; one call row per narrative; referred groups appear once |
| T3 | jobs, notices | daily and monthly built once; skipped when they exist; notice to the subject; a failing subject does not stop the others; commit visibility |
| T4 | `audit.py`, API, CSV, Audit screen | every approval and rerun of the month; filters; by agent / manual / system; code version; waiver; CSV neutralised; month bounds |
| Finish | `pytest -q -n 6`, both UI suites, all Playwright stories, demo story and `docs/demo.md`, `graphify update .`, push, no merge | |

## 5. Risks

- A pattern on three loads can be chance: `loads_in_window` is shown; N is a setting.
- The narrative check cannot judge words. It stops a wrong *figure*; it does not stop a wrong *claim*. The numbers sit beside it.
- Reports for many feeds can be large; `numbers` lists only what happened in the period, not every feed's history.

## 7. Outcome and deviations (T1 to T4)

- Commits: T0 `5dd9992`, T1 `b49f924`, T2 `d07459e`, T3 `ff5bc8c`, T4 and finish: see `git log phase-7.15`.
- Files outside the plan: `core/domain/pattern.py` and `core/domain/report.py` (the models; the step modules import them),
  `core/repositories/reports.py`, `apps/api/routers/reports.py` and `audit.py`, `tests/reporting.py` (loads on chosen dates),
  `tests/configured.py` (`go_live()` lets a named owner other than `dev-data-owner` decide), `tests/test_api.py` and
  `tests/test_jobs.py` (the start-up job list now holds the report job), `tests/phase711.shell.test.tsx` (two more views).
- `core/steps` may not import `ports.llm` (rule 2's boundary test caught it): `write_report()` takes the errors that mean "the
  model could not answer" from its caller (`no_narrative_on`). The notice to a subject lives in `core/steps/reports.py`, so the
  pinned digest of the notify calls in `jobs.py` is unchanged.
- The `reports` period check is the database's too: a daily report is one day, a monthly one a calendar month.
- A report of a period that has not ended is refused (422): the demo's loads are of today, so its report is of yesterday and quiet.
- The code version of a decision is the latest `code_versions` row at or before it. Approvals are stamped with the transaction
  start, so tests on one shared transaction cannot show it; `tests/test_api_audit.py` proves it on committed data.
- Not done: a monthly scheduler of its own (a daily job that acts on the 1st), email, a report per requester.

## 6. Deferred

Sending email (stub stays); a monthly scheduler of its own; erase of profiling history (7.16); a report per requester.
