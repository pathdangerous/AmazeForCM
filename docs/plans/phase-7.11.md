# Phase 7.11 — UI redesign

Owner-approved scope. Branch phase-7.11 from main; do not merge. The four reference
files were saved verbatim and committed first. Their figures are samples, never seeds.

## Tasks

1. Replace tokens and both shells using the approved references; keep Mantine and
   byte-identical theme.ts files. Preserve persona behavior, labels and ids. Backend
   grouped navigation links only to existing views; omit unsupported destinations.
   Keep requested group headings, even where no destination currently exists.
2. Add reusable DataGrid, KPI/section cards, status chip, confidence bar, CSS donut,
   keyboard stepper, agent panel and sticky footer in apps/ui-shared. Grid filtering,
   sorting and paging must work, not merely resemble controls.
3. Add Operations overview with read-only real data. Source each displayed measure
   from stored feeds, loads, certification or onboarding/proposal state; omit unsupported
   measures. No invented counts, trends, timestamps, lineage or agent facts.
4. Apply the shared look to every existing screen; workspace and product page closely
   follow the supplied mockups. Keep existing workflows and test IDs; preserve approval
   authorization, rejects/edits, empty/loading/error states and downloads.
5. Preserve accessible labels and >=4.5:1 text contrast; test keyboard stepper navigation.
   Run both UI suites, pytest -q -n 6, both B10 stories and the full screens run.

## Files and verification

Files: BUILD_PLAN.md; this plan; apps/ui-shared; both UI src trees and index.html;
read-only overview router and its registration under apps/api; focused API tests;
tests/test_ui_shared_look.py; e2e stories/screens only where needed for the approved
navigation/layout (do not remove behavioral assertions); docs/ui-polish.md.
Generated screenshots go to docs/phase75-screenshots/phase711/ and are excluded
from Git and publishing through .gitignore and .replitignore.

Tests first: record RED before implementation, then GREEN. Main agent verifies
the implementation and records task commits/results here. No new UI library,
no live-model calls during testing, no data mutations for dashboard figures.

## Results and deviations

- Design references: 62707a2. Main was 2351c82 when the phase branch was created.
- Mockup Accept maps to the existing approval action; preserve its accessible
  Approve name and test IDs where existing behavioral checks depend on them.
