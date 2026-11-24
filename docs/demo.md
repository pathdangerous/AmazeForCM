# Demo: from a file to released data, a second delivery that drifts, its exceptions fixed, and the audit (15 minutes)

Covers phases 7.12 to 7.16. Phase 7.16 adds **demo history** (`python -m core.seed --demo-history`: 21 past days through the
real pipeline, the same rule failing every Monday, so Reports has numbers and one weekday pattern), **Preview today so far**
on Reports (counted like a report, never stored) and the **recorded erase of old profiles** (History on the feed page; the
audit lists it). Phase 7.15 adds **Reports** (a daily and a monthly report per data owner and data steward, made
every morning, numbers counted by code, an optional narrative apart from them), recurring failure patterns, and the monthly
**Audit** with a CSV. Phase 7.13 adds **Run all agents**: after one click on the profile, every onboarding
agent drafts ahead on the drafts before it, and you review the whole chain once, top to bottom. Phase 7.14 adds
**Exceptions**: the rows a held load's rules flagged, grouped; an agent proposes one fix per group; a person approves in
bulk; the held file is rerun as a new, recorded load.

Mock provider (the default): every answer is the same each run. To show the real model instead, start the API with
`DEV_LLM_PROVIDER=azure_openai` (the actions and wording then come from the model; everything else is unchanged).
Sign in as Olive Operator (backend-ui). Files: `fixtures/synthetic/Synthetic_Security_Master.xlsx` (version 1) and
`fixtures/sources/Synthetic_Security_Master_v2.xlsx` (version 2: a new column BEST_FCF, BEST_BPS and BEST_CAPEX swapped,
BEST_DPS sent as text, eight negative BEST_SALES rows; made by `scripts/make_security_master_v2.py`).
The same path is the Playwright story `e2e/tests/demo_story.spec.ts`.

## The script

| Min | Say | Do | Show |
|---|---|---|---|
| 0-1 | "Agents propose, people decide, rules execute." | Onboard a file: upload version 1, confirm. Press **Run all agents** (once). | The nine steps and the empty **Chain review**. Nothing is loaded yet. |
| 1-2.5 | "The profile in a business user's words." | Profile step. | Records that are incomplete, how many columns already match the glossary. **Actions**: each marked "agent applies" or "I'll do it". Select (approve) or reject each; add one of your own ("Add action"). The "I'll do it" ones become a to-do list on the feed. Approve: **the one click**. The profile registers the feed (owner, licence, visibility), so it stays a person's decision before the agents go on. |
| 2.5-3.5 | "One click ran every agent ahead; you review the chain once." | Wait a few seconds; nothing else is clicked. | **Chain review**: glossary, mapping, transformations and rules are all drafted. Blocked items (a tie, a conflict: the chain did not draft past that column) come first, then low-confidence ones. A mapping says "based on" the glossary draft it used; click it to open that step. Nothing is configuration yet: no mapping or rule exists until it is approved. |
| 3.5-4.5 | "Approve top to bottom; an edit re-runs only what it touches." | **Approve all confident** on the glossary (one approval per item, by you). Edit one glossary draft: the mapping drafts built on it go stale, are rejected by the system and re-drafted; nothing else runs again. Approve and next through glossary and mapping: the cursor moves, no agent runs again. A draft approved before what it is based on is refused ("approve top to bottom"). | Cleaning steps appear only for columns whose action was selected; a column left out is not asked about. |
| 4-6 | "A person can always add, edit or reject." | Rules step: **add a rule by hand** (it is back-tested before it is accepted; a failing SQL is refused with its reason), **edit one agent rule** (it is back-tested again), **reject one**. | Items say "added by Olive Operator". The timeline shows who made each. No model call is made for a hand-made item. |
| 6-7 | "Before go-live: what is done and what is not." | Go-live step. | The **completion report**: mappings done, pending and left out; transformations; each rule with its back-test; actions by agents vs by hand; items by agents vs by hand. |
| 7-8 | "The code that will run, versioned." | Approve go-live; open the feed page, section **Generated code**. | Version 1: staging DDL, load and curated SQL, each rule's SQL, README. The engine runs exactly these statements; the version is pinned by the go-live and cannot be edited. Release the first load; the consumer sees the product. |
| 8-9 | "The source sent a changed file." | Feed page, Loads: choose the new file (version 2), press Load now. | **Drift**: one new column, BEST_FCF (parked, not loaded); two columns swapped (information only: columns are matched by name); BEST_DPS now arrives as text (type DOUBLE to VARCHAR; it still casts, so the file loads); eight negative BEST_SALES rows, so "best sales is not negative" flags more rows than before. Each finding has an action, proposed by rules (no model). Select "map new column". |
| 9-10 | "Agents propose the update; a person approves; version 2." | All on screen, as Olive Operator: **Glossary tab**: approve BEST_FCF's element. **Feed page, Mapping**: Propose mappings, pick and approve BEST_FCF's element. **Go live**: Prepare, then Approve go-live. **Generated code**: version 2, Compare with version 1. | The diff names `curated.sql` and `README.md`: BEST_FCF is now loaded. Overview: switch Daily / Monthly; the drift event and the actions applied by agents vs by hand are counted. |
| 10-12 | "Flagged rows become exceptions; agents propose fixes; people approve; the file is rerun." | Feed page, section **Exceptions** (version 2 scored 88.6, below the threshold of 90, so it was held). Press **Propose fixes**. Tick every group, press **Approve selected**. Type a reason, press **Rerun this load**. | Groups of flagged rows by rule and value pattern (digits 9, capitals A), with counts and sample rows; one fix per group, each marked agent or "added by", with who decides next (a waiver: the data steward, then the owner named at registration; here one person holds both). The held load stays **held**. The rerun is a new load: "Rerun of load ...: reason" in Loads; **Rerun released: trust 97.1 (it was 88.6); 6 rows waived**; the certification says "waived (counted as unflagged)". The owner is notified at each step. |
| 12-13 | "Counted by who did it." | Overview. | **Exceptions**: fixes approved by agent proposals and by hand; groups open and waiting for the owner; reruns released and still held. |
| 13-14 | "A report every morning; the numbers are code's, the words are the agent's." | **Insights, Reports**: press **Make yesterday's report (data owner)**, open it. | The **Narrative** (written by the ReportSummariserAgent, logged) sits apart from the **Numbers** (loads, held, rows, flagged and waived rows, trust, open groups, waivers, reruns, drift, **recurring patterns** with their dates as evidence, groups **referred to the provider** once each). The demo's loads are today, so yesterday's report is quiet; the job makes one for every owner and steward each morning and on the 1st the month's. **New version** makes version 2: the numbers fingerprint is the same. A narrative that states a figure the report does not hold is refused and the report is kept without it. |
| 14-15 | "Every decision, who made the item, which code ran." | **Insights, Audit**: this month. Filter by feed or person. **Download CSV**. | One row per decision (who, role, approved or rejected, the item, **agent (ExceptionFixAgent)**, manual or system), the **code version** in force, **(waiver)**, and the **rerun** with who started it and why. The CSV has the same rows; a cell that starts like a formula is quoted. |
| 15-16 | "Three weeks of history, through the real pipeline." | In a terminal: `.venv\Scripts\python -m core.seed --demo-history`. Then **Insights, Reports**: **Preview today so far (data owner)**, then **Make yesterday's report (data owner)** and open it. | The seed ran the daily pipeline (fetch, step 12, the engine, steps 15 and 20) for each of the 21 days before today, with a clock and a connector it injected; no row was written by the seed itself, so every guard held. The same rule (`best_sales_not_negative`) failed on every Monday: three held loads. The **preview** is today so far, marked "Preview: not stored" (no report, no notice, no model call). Yesterday's report now has **loads, rows and a trust score**, and **Recurring failure patterns** shows `best_sales_not_negative` "every Monday: 3 of 3 loads" with the three dates. |
| 16-17 | "Old profiles go through one function, and it leaves a record." | Feed page, **History**. | Each profile says why it stays ("kept: its registration", "kept: it is the latest", a sign-off, a glossary run, a load's arrival, a code version, a report). When older profiles nobody points to exist, the panel offers: **Erase profiles older than** (a select), **Why it is erased**, **Erase**, **Confirm erase**. The erased entries stay in the list as "Profile 2: erased by Olive Operator, date: reason", and **Audit** lists the erase with who, why and which versions. In this demo both profiles are kept, so the panel says "Nothing can be erased". |

## What the demo history and the profile erase do and do not do (7.16)

- **Run order.** Onboard the security master and release its first load (minutes 0-8), then run the seed, **before** you
  load version 2 and approve any waiver: an approved waiver applies to every later scheduled load, so a Monday seeded after
  it would be waived, not flagged. (The story does it in this order; the three held Mondays then sit beside version 2's
  held load in Exceptions.) `python -m core.seed --demo-history`
  refuses in words when the feed, its go-live or its file is missing. Run it again and it changes nothing (a day that has a
  scheduled load is skipped). Run `alembic upgrade head` (0036) on dev and on Replit first.
- **What it writes.** Only what a delivery writes: one fetch, one load with its arrival, stages, records and certification
  for each of the 21 days, and the notices a load in hypercare sends (21 of them). The loads' `received_at` is the injected
  day (09:00 UTC); every other time column is the database's own, so a certification of a past load says it was made today.
- **Why a Monday.** Any 21 consecutive days hold three Mondays, which is the pattern threshold (3). The Monday files carry
  eight negative `BEST_SALES` values (8 of 70 rows: trust 88.6, held); every other day is clean: the file's one repeated
  ticker is numbered (otherwise `duplicate_key` and `one_row_per_ticker` would fail every day and make a "pattern" for every
  weekday) and each day's `RECORD_DATE` is that day (otherwise the provider date would repeat). If the feed was onboarded
  with other rules the seed says so and no pattern appears.
- **The preview** is numbers only: no narrative (that needs a logged model call), no stored row, no notice.
- **The erase** is `amaze_erase_profiles(feed, before, reason, by)`, the only way a profile is deleted. It keeps the latest
  profile and any a registration, sign-off, glossary run, arrival, code version or report points to. A profile is a
  measurement of a file, not configuration, so this is an exception to rules 1 and 7 like 7.9's feed delete (CLAUDE.md names
  it). The record (`profile_erasures`) is append-only and outlives the feed. Nothing to erase is an answer, not an error.
- **Not in the demo.** An erase that deletes something needs unreferenced older profiles (a candidate re-profiled before its
  sign-off); the tests and the component test cover it, the story shows the "Nothing can be erased" panel.

## What Exceptions does and does not do (7.14)

- An agent never changes data (rule 1): it proposes; a person approves; no record is ever updated. A waiver is read by the
  engine at the rerun and written into the NEW load's records (`waived`, never `flags`); the held load's records stay as
  they were. Waivers are not pinned by the go-live; a transformation fix is, so its rerun waits for the owner's go-live
  (`rerun_needs_go_live`).
- A rerun is recorded (`rerun_of_load_id` and a reason, checked by the database). Step 12 leaves out only the held load's
  own hash: any other resend is still quarantined. A held load that is rerun again needs something resolved since.
- Known gap G1: a pending rule draft in a chain is not made stale when a fix approves a newer transformation version; the
  chain review shows "tested on transformation version N (now version M)".
- Run `alembic upgrade head` (0033 and 0034) on dev and on Replit before showing it.

## What Run all agents does and does not do (7.13)

- Rule 1 is unchanged: every draft is a pending proposal; a person approves each one (one approval row per item, also
  with "Approve all confident"); the database refuses an approval before the drafts it was built on are approved.
- It stops only at the model-call cap or a failed call (enter the step again to resume), and drafts past a
  low-confidence item (flagged) but not past an item with no single value (blocked: that column only).
- Go-live, its test load and its code version, and every load after it (steps 12-15), are as before: they run on
  approved configuration only. Approval in advance is not built.

## Roadmap (not built yet)

- **More exception fixes.** 7.14 proposes a transformation, a waiver or a referral; a rule change and a remap are not built.
- **Email for reports.** The report job tells people in the app; the email adapter is still a stub.
- **CI/CD pipeline.** Generated code and configuration promoted through environments by a pipeline, not by hand.
- **In-house data agents.** The agents run on the company's own model deployment (the Azure move), behind the same port.
