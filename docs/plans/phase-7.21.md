# Phase 7.21: recurring or one-time, and a Deliveries dashboard

Goal. Onboarding asks whether a feed is recurring or a one-time load, and when a recurring one is expected. Each feed gets a
Deliveries tab that compares expected with actual arrivals. No model call anywhere. Branch `phase-7.21` from `main`
(a5dc80c). Migration `0039`. Out of scope: reading the arrival time from the source (see 1.3), calendar holidays, a schedule
per source, changing a schedule through a proposal (it is a person's setting, like a pause).

## 1. What exists (read before designing)

1. Step 12 has **no delivery deadline** (its own docstring, assumption 8): `late_alert` is only "the daily fetch found the
   source down" (`record_late_alert`, called by `DailyFetch._late_alert` after a `source_down` fetch of a feed with a go-live).
   It tells operations and the feed's owner. It does not use a time.
2. The daily job runs once, `DAILY_FETCH_HOUR = 6` UTC (`apps/api/jobs.py`), one fetch and load per feed. So a scheduled load's
   `received_at` is when Amaze took the file in, not when the source sent it. The demo history injects a clock
   (`HistorySpec.hour = 9`).
3. Therefore "actual arrival" in this phase is `loads.received_at` of a `purpose = 'scheduled'` load. Limitation, stated in the UI
   help text: a feed's expected time earlier than the job's run time shows as late by construction. Reading the source's own
   delivery time is a later phase.
4. A day with no load row is a missing delivery (the fetch found nothing or the source was down: `source_down` makes no load).
   A resent file makes a `quarantined` load; a breaking file a `held` one.
5. Feeds are created by `confirm_upload()` (the "Create the feed" form) and by the source-catalogue path (S4). Only the first
   asks the question; the second keeps "Schedule not set".
6. `amaze_delete_feed()` (0027, patched by 0030, 0032, 0034, 0036, 0037) hard-deletes a feed and its append-only rows; every new
   per-feed append-only table must be added to it. The audit view (`core/steps/audit.py`) unions decisions, reruns, erasures,
   uploads.

## 2. Assumptions (numbered; correct me now or I proceed)

1. No agent, no model call, no `proposal_log` row. A schedule is a person's setting, like `feed_status_events`: not a proposal.
   (Rule 1 is about agents; the person's name and time are recorded.) A test asserts `proposal_log` is untouched.
2. `feed_schedules` is append-only (UPDATE, DELETE, TRUNCATE refused; DELETE only while `amaze_delete_feed()` deletes the
   row's own feed). A change is a new version. Rule 7.
3. Columns: `id, created_at, feed_id, version, kind ('one_time'|'recurring'), frequency ('daily'|'business_days'|'weekly'|
   'monthly'), weekday (0 Monday to 6 Sunday), day_of_month (text: '1'..'28' or 'last'), expected_time (time), grace_minutes
   (0 to 1440), timezone (IANA name), notify_owner, decided_by, note`. The database checks: version is the previous plus 1
   (first is 1, only for an existing feed); one_time has no frequency, weekday, day_of_month or expected_time; recurring has a
   frequency and an expected_time; weekly has a weekday and no day_of_month; monthly has a day_of_month and no weekday; daily
   and business_days have neither; `decided_by` is not blank.
4. The IANA name is checked by the step (`zoneinfo`), not by the database (the database cannot know the zone list); an unknown
   name is `InvalidSchedule` (422). `notify_owner` defaults to true for a recurring schedule.
5. **In force.** Version 1 is in force from the beginning (it describes the feed; a feed has no earlier schedule). Version n>1 is
   in force from its `created_at`. The expected deliveries of a window are built per version: each version contributes the due
   instants that fall in `[its start, the next version's start)`. A late or missing check therefore uses the schedule in force
   when the delivery was due, and a schedule changed mid-window shows the old rule before the change and the new one after it.
6. **Live since.** Expected deliveries start at the earlier of the feed's first go-live and its first scheduled load (the demo
   history is received before the go-live row is made; a feed expects nothing before it can load). A window is 30 or 90 days
   back from now, or `all` ("since the feed began loading"), always cut at live-since. No go-live and no scheduled load: no
   expected deliveries.
7. **Due instant**: the local calendar date in the schedule's timezone at `expected_time`, converted to UTC. Daily: every date.
   business_days: Monday to Friday. weekly: the weekday. monthly: the day (`last` = the month's last day; days 1 to 28 exist in
   every month).
8. **Matching** (`core/steps/deliveries.py`, pure code). Each scheduled load goes to the one slot whose span contains its
   `received_at`; a slot's span runs from the midpoint to the previous due instant (at most 12 hours before this due) to the
   midpoint to the next (the feed's last slot runs to its due + 1 day, the first slot's start is due - 12 hours). A load outside
   every span, or a second load in a slot, is `unexpected`. The first load in a slot is the slot's delivery.
9. **Timing**: `on_time` when `received_at <= due + grace`; `late` otherwise, with `late_minutes = received_at - (due + grace)`
   rounded down. A slot with no load whose `due + grace` is in the past is `missing`; one still to come is `upcoming` and is
   not a figure (only "next expected delivery" uses it).
10. **Result** of the delivery's load: `released`, `held` (a held load: below the trust threshold or breaking), `quarantined`
    (`resent_quarantined`), `failed` (`failed_rolled_back`), `abandoned`, or `loaded`/`received` (not yet certified). One cell
    shows one **status**, chosen: `missing`, else `resent_quarantined`, else `held`, else `late`, else `on_time`. The result
    column is shown beside it, so a released load shows both (`released` is a result, not a second colour). Deviation from the
    brief's single list; recorded in the report.
11. **Figures** (all from the same table of deliveries the page shows, so the test can compare them): `expected` (due and not
    upcoming), `on_time_pct` = on_time / expected (on_time counts a delivery that arrived on time whatever its result), `late`,
    `missing`, `mean_late_minutes` (over late deliveries, None when none), `streak` (consecutive newest expected deliveries that
    are on_time with no earlier break, counted back from the newest non-upcoming), `next_expected` (the first upcoming due
    instant), `unexpected` count.
12. **Typed results** (rule 3): `deliveries_shown` (recurring), `one_time_shown` (the feed's loads and the label),
    `schedule_not_set`. Wrong feed is the usual 404 `FeedNotFound`. Not a bare error for an expected case.
13. **Missing alert.** `delivery_alerts` (append-only, `feed_id`, `due_at`, `schedule_version`, `created_at`, unique
    `(feed_id, due_at)`): `record_missing_alerts()` finds the missing deliveries in the last 30 days of a feed whose schedule in
    force has `notify_owner`, inserts one row for each not yet alerted (so once per expected delivery, tested by running it twice),
    and tells the feed's owner (and operations) through the Notifier with a `late_alert` result, like `record_late_alert`. The
    daily job calls it for each registered feed after its fetch and load. No alert before live-since or for a one-time feed. It is
    in `core/steps/` (no model) and the job passes the Notifier.
14. API (all backend roles read; the change is the backend team's, like registering): `GET /api/amaze/feeds/{id}/schedule`
    (current and every version), `POST /api/amaze/feeds/{id}/schedule` (a new version), `GET /api/amaze/feeds/{id}/deliveries?
    window=30|90|all`, `GET /api/amaze/deliveries/overview` (recurring feeds by on-time %, worst first). `confirm` takes a
    required `schedule` object; a missing one is 422 (FastAPI validation).
15. **Step API.** `confirm_upload()` takes `schedule: ScheduleInput | None = None` and writes version 1 in its own transaction
    when given. The API makes it required. (The step keeps `None` so the older step tests, which test intake and not scheduling,
    are not touched; the question being required is a test of the API.)
16. Existing feeds without a schedule: `schedule_not_set`, the feed page shows "Schedule not set" and a "Set schedule" button.
    `tests/onboarded.py::registered_feed` and the demo seed set one (a daily 06:30 UTC, 120 minutes; the demo story sets its own).
17. UI: no new library. Calendar and arrival chart are SVG and CSS grid. A status is a fill with its words as visible text, never
    colour alone; six new fill/text pairs in `CONTRAST_PAIRS` at 4.5:1 (on_time, late, missing, held, quarantined, upcoming),
    byte-identical in both apps (`tests/test_ui_shared_look.py`).
18. Marketplace product page: "Updated daily / on business days / weekly / monthly" from the feed's schedule in force, or
    "One-time load"; nothing when not set. It reads the schedule through the product's feed; no model, and no entitlement change.
19. Demo history (`HistorySpec`): new `missing_days`, `late_days` (day numbers from the oldest), `skip_weekends`, `late_hour`,
    defaults leave today's behaviour so the 7.16 tests stand; the command line's `run()` uses the demo values (one missing day,
    two late arrivals, weekends skipped, normal hour 07:20). Rows still come only from the pipeline's steps.
20. One new function (`amaze_delete_feed`, 0039 version), three new triggers on `feed_schedules`, three on `delivery_alerts`,
    two new functions (`amaze_guard_feed_schedule`, nothing else). No existing guard is loosened. If one would have to be, stop.

## 3. Triggers and functions added or given a new version in 0039

- Table `feed_schedules` + `ck_feed_schedules_*` checks (assumption 3).
- `amaze_guard_feed_schedule()` + `trg_feed_schedules_a_guard` BEFORE INSERT (version and shape; locks nothing, the step locks the
  feed row first, a unique `(feed_id, version)` stops a race).
- `trg_feed_schedules_append_only` (existing `amaze_refuse_update_delete()`), `trg_feed_schedules_no_truncate`
  (`amaze_refuse_truncate()`).
- Table `delivery_alerts`, `trg_delivery_alerts_append_only`, `trg_delivery_alerts_no_truncate`.
- `amaze_delete_feed()`: new version (from 0037's text; deletes `delivery_alerts` then `feed_schedules`).
- Downgrade: stops, naming 0039, while a schedule or an alert exists.

## 4. Tasks (one commit each, test first)

- T1 migration 0039, tables, repository, `core/steps/schedule.py` (set/change, typed), database checks.
- T2 onboarding question, `confirm` takes the schedule, feed page schedule and "Change schedule", audit rows, helpers.
- T3 `core/steps/deliveries.py` (expected vs actual, typed), missing alert and the job hook.
- T4 Deliveries tab, calendar, overview ranking, product page line, theme pairs in both apps.
- T5 demo history, `docs/demo.md`, demo story, ui-polish items, CLAUDE.md line.
