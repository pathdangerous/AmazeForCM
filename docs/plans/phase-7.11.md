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
- Scoped additions: both UI TypeScript/Vite/Vitest configs resolve shared code to
  each app's existing React/Mantine installation, without new dependencies.
  The old inline 1200px-layout assertion is replaced by the approved shared
  sidebar/1280px-market layout check.
- Product metadata is read-only in core/steps/product_page.py and its API tests:
  entitled users see the latest released load's stored certification and completed
  stages. No raw storage paths, held-load scores or restricted metadata are exposed.
- RED: new backend frontend suites 7 failed / 1 passed; consumer shell 1 failed;
  overview API 5 failed. Overview API GREEN: 7 passed. Product metadata API RED:
  missing certification field. Full verification results follow below.

## Final verification

- Task 1: bdee501 — tokens, Inter, grouped backend navigation, marketplace tabs,
  working search/notifications, existing 240px persona controls.
- Task 2: 32d81ed — shared functional grids and accessible workflow components.
- Task 3: adfaf2b (read-only API), c186e52 (overview UI); no sample metrics.
- Task 4: 04944d3 — existing screens, workspace, entitled released-product
  certification and lineage; existing actions/test IDs retained.
- Task 5: contrast declarations and keyboard stepper checks; shared theme bytes
  remain identical, no library or lockfile changes.
- `npm test` backend: **218 passed**; consumer: **32 passed**; both builds passed.
- `pytest -q -n 6`: **3287 passed in 184.83s**; non-live suite, mock adapters.
- B10: original story **1 passed (34.0s)**; operator/persona **2 passed (45.3s)**.
- Screens: standard **1 passed (40.7s)**, 25 images; workspace/product **1 passed
  (46.1s)**, 17 images. All 42 current images are in
  docs/phase75-screenshots/phase711/, ignored as generated assets.
- The screenshot helper now rechecks the current step after automatic transitions;
  it previously waited for approval on a stale step while the next needed decisions.
  Existing approval and final-ready assertions remain intact.
- Rendered live preview checked at 1280px for the overview and marketplace product.
  Real-data trust/lineage are present; long agent notes and product tables scroll
  within keyboard-focusable regions instead of stretching the page.
- Branch is pushed for review, not merged.
