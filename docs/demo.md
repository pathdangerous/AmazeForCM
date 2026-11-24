# Demo: from a file to released data, and a second delivery that drifts (11 minutes)

Covers phases 7.12 and 7.13. Phase 7.13 adds **Run all agents**: after one click on the profile, every onboarding
agent drafts ahead on the drafts before it, and you review the whole chain once, top to bottom.

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

## What Run all agents does and does not do (7.13)

- Rule 1 is unchanged: every draft is a pending proposal; a person approves each one (one approval row per item, also
  with "Approve all confident"); the database refuses an approval before the drafts it was built on are approved.
- It stops only at the model-call cap or a failed call (enter the step again to resume), and drafts past a
  low-confidence item (flagged) but not past an item with no single value (blocked: that column only).
- Go-live, its test load and its code version, and every load after it (steps 12-15), are as before: they run on
  approved configuration only. Approval in advance is not built.

## Roadmap (not built in 7.12)

- **Exception-fix loop.** When a load holds rows or a file, agents propose the fixes (a cleaning step, a rule change, a
  remap); a person approves them in bulk; an agent never changes data alone (decision T0).
- **Owner reports with recurring-failure patterns.** A report to each feed owner: what failed, how often, and the pattern.
- **Recorded erase of profiling history.** An erase of a feed's profiling history, recorded like 7.9's feed delete, never an
  edit in place (rule 7).
- **CI/CD pipeline.** Generated code and configuration promoted through environments by a pipeline, not by hand.
- **In-house data agents.** The agents run on the company's own model deployment (the Azure move), behind the same port.
- **Monthly audit view.** One page per month: every proposal, who made it (agent, rules or person), who decided it, which
  code version ran.
