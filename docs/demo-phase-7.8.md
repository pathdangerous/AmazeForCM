# Demo script: one operator, any file (phase 7.8)

About 10 minutes. Mock provider unless the Azure OpenAI settings are filled in (README, "Real model"). Synthetic data
only. Start with `scripts\dev.ps1`; backend-ui is on 5173, consumer-ui on 5174.

1. **Sign in as Olive Operator** (backend-ui). The home page is "Onboardings": every session with its step and status.
2. **Onboard a file.** Choose `fixtures/synthetic/Synthetic_Security_Master.xlsx` (70 rows, 30 columns). Show the
   preview (first 10 rows, scrolls sideways, placeholder values highlighted). Name the feed "Synthetic security
   master", code `syn_security_master`. Create the feed.
3. **The workspace.** Left: the nine steps and their state. Right: what the agent did and why (reasoning, confidence,
   the inputs it saw: columns, issues, candidate counts; never sample values unless `SEND_SAMPLE_VALUES`).
   Footer: Back, and Approve & next (disabled, with the reason, while an item needs a decision).
4. **Profile.** Point at the issues table: constant SOURCE, placeholders in COUNTRY, REGION and F_PERIOD, RECORD_DATE as
   text (DD-MM-YYYY), one all-empty column, one duplicate ticker. Approve.
5. **Glossary and mapping.** Pre-accepted items carry their confidence. **Edit one proposal** (the definition of a new
   element), save, approve. A tie asks for a choice.
6. **Transformations (step 10).** The agent proposes trims, placeholder-to-null and a date parse; approve. The key
   policy shows how the duplicate ticker is handled.
7. **Rules.** Rules that flag rows show "Flags N of M rows on this file". Reject those, approve the rest.
8. **Go-live summary.** Choose visibility (the owner chooses it here, the summary does not). Approve. The first load
   runs; with the two flagged rules rejected, 2 of 70 rows are flagged, trust 97.1, so it is released.
9. **Consumer** (consumer-ui, sign in as Riley Requester). Catalogue, open the product, preview (100 rows at most), data
   dictionary, download CSV and XLSX.
10. **Four eyes.** Set `REQUIRE_FOUR_EYES=true` and restart: the same path needs steward, checker and owner; the old
    story (`tests/poc_story.spec.ts`) runs this way.

## Known limits to say out loud

- **Mapping gold cases 8 and 14 are missed** by the Azure OpenAI model in both live runs (29 of 31 pass, both runs). They are in `fixtures/gold/mappings.json`; the live report is in
  `docs/plans/phase-7.8-progress.md`. A mapping proposal the operator sees can therefore be wrong on those shapes,
  which is what the decision step is for.
- Two profiler gold cases accept two outcomes (`meets_with_gaps` or `rejected`); see the same file.
- Mastering and transformations beyond step 10's operations stay a marked pass-through (D6).
- The operator story and the shots were written without being run in the session that wrote them.
