# Phase 7.12, section T7: manual items at every onboarding stage

Request (T7): "Add item" at each onboarding stage; edit and reject on every stage's items, agent-made or manual.

## What exists, and what does not

- Exists: `edit_item()` (glossary, mapping, transformation, rule), `POST .../items/{id}/decision` for every kind
  `decide_proposal()` knows (glossary, mapping, rule, review summary, transformation).
- Does NOT exist in this repository: T1 (profile "agent applies / I'll do it" action list), T3 (generated code), T4
  (drift stage), T6 (the extended Playwright story). Their stages cannot take a manual item until they exist.
  This plan builds the shared mechanism and the five stages that exist. Profile and drift are recorded as blocked.

## Assumptions (stop and ask if one is wrong)

1. A manual item is a `proposals` row with `origin = 'manual'`, `author` set, `proposal_log_id` NULL, status pending.
   An agent item is `origin = 'agent'`, a log row, no author. The database enforces both (migration 0028).
2. No model call, so no `proposal_log` row. `autonomy` of a manual item is `A1`; its approver roles are the agent's
   (glossary_owner / data_steward / data_steward / data_steward then checker).
3. The author's acceptance counts exactly as an edit's does: event `item_added`, then `pre_accepted` by the author.
   With four-eyes on, the approval steps still need a second person (unchanged `record_decision`).
4. Manual kinds: glossary_element, mapping, transformation, rule. Not profile_summary, not review_summary.
5. Go-live "add a note" is an edit of the review summary that appends to `notes` (the only key an edit of a review
   summary may change). The item stays an agent item; the timeline shows "edited by".
6. A column (rule key) that already has a pending item is refused with `ItemAlreadyPending` (409): edit or reject the
   pending one first. The database's one-pending indexes stay the last line.
7. Mapping "excluded": an approved mapping proposal with `excluded: true` and no candidates writes NO mappings row, so
   the column stays unmapped and the engine does not load it (CLAUDE.md: an unmapped column is not loaded). Only an
   unmapped column can be excluded (`ColumnAlreadyMapped`, 409). `mapping_input()` skips an excluded column.
8. Manual mapping to any glossary element: the 0016 trigger accepts, for a manual proposal only, any element that
   exists, provided it is the proposal's `recommended` element. A registered feed is still required.
9. A manual rule is back-tested by the same code as an agent rule (`s09_rules._checked`) before it is stored, with no
   connection held. A refusal (not a plain SELECT, unknown element, runtime error, flags more than half the history) is
   `ManualRuleRefused`, 422. An edited rule is checked the same way.
10. `GlossaryProposalPayload.run_id` becomes optional (a manual proposal comes from no run).
11. The feed deletion function (0027) skips NULL log ids; 0028 patches it.

## Tests first (tests/test_manual_items.py)

Database: manual without author, manual with a log row, agent without a log row, agent with an author, manual
profile_summary, manual rule without a back-test: each refused. Manual mapping to an existing element writes a mapping;
to a missing element is refused; an agent mapping still needs a candidate. A manual item survives an edit as manual.
Per stage (glossary, mapping incl. excluded, transformations, rules, go_live note): add, edit, reject; add writes no
`proposal_log` row; the workspace item says `origin=manual`; the timeline says "added by". Rules: a failing manual rule
is `ManualRuleRefused` and writes nothing. API: add returns 200/409/422.

## Not done here

Profile action, drift, generated code (blocked, see above). UI "Add item" forms and the Playwright story extension are
a second step after the backend is green.
