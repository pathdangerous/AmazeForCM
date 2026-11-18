# Phase 7.5 plan — UI polish

**Decisions (task 0):** Mantine 9.7.4; the Hexaware look of assumption 2 (R1's type scale, radius 6, 4 px grid); dark mode kept with derived tokens; every other assumption approved as written (section 17).
**Status:** approved 2026-11-18 with the decisions above.
**Date:** 2026-11-18. **Branch:** `phase-7.5`, created from `main` at `a9abed5`. **Scope:** phase 7.5 of `BUILD_PLAN.md` (work through `docs/ui-polish.md`; decide a component library and a visual reference, then apply them to both UIs); phase 7 plan section 16 note 1. Local only; nothing is pushed.

**Lean mode.** Two checkpoints: (1) after the library is installed and one screen is converted (task 4: you see that screen before the rest), (2) at the end (task 17). No step logic, guard, agent or migration changes (section 4, assumptions 15, 16, 24 are the edges). Every task names its behaviour, its test (a component check, a Playwright check or a pytest), its files and what is out of scope.

## 1. Goal and "done when"

**Phase 7.5 is done when all six are true:**

1. **The Playwright story still passes** (`BUILD_PLAN.md`): `e2e/tests/poc_story.spec.ts`, unedited, `1 passed` three runs in a row.
2. **The screenshots are approved** (`BUILD_PLAN.md`): the 25 shots of section 8, before and after, committed in `docs/phase75-screenshots/`.
3. Both UIs use the chosen library and one look: one `src/theme.ts`, byte-identical in both apps (assumption 3).
4. Every polish line triaged "7.5" in section 3 is done, each with its test; `docs/ui-polish.md` records each line's fate.
5. `pytest -q -n 6` passes, 0 skipped, `-W error`, three timed runs within the ceiling (median ≤ 200 s, single ≤ 245 s).
6. Both builds exit 0; `npm test` (component checks) passes in both UIs; bundle sizes pasted, each app within assumption 31.

## 2. State at the start (checked on 2026-11-18)
| Item | Found |
|---|---|
| Git | `main` = `a9abed5`; untracked `.claude/`, `.env.main`, `docs/phase6-screenshots/`, `docs/phase7-screenshots/` |
| Alembic | `alembic current`: `0019 (head)` on `amaze`, and on `amaze_eval` (`DATABASE_URL` set from `.env`'s `EVAL_DATABASE_URL`) |
| Tests | `pytest -q -n 6`: **`2185 passed in 211.71s`** (one run, not clean: light planning work ran beside it). Above the 200 s median ceiling, under the 245 s cap; phase 7's clean runs were 135.63 / 145.41 / 183.43 s. Story: `1 passed (26.2s)`, test 14.1 s |
| UIs | No component library and no CSS file: styles inline in each `index.html` (backend-ui tokens `--line`, `--muted`, `--accent`; consumer-ui none); dark mode via `prefers-color-scheme`. No component-test runner. Builds (from `dist/`): backend-ui JS 268 KB, **79.6 KB gz**; consumer-ui JS 240 KB, **73.3 KB gz** |
| Story spec | 88 testid selectors, plus `getByRole("button", {name: "Search", exact: true})`, `getByRole("link", {name: "See this requirement"})`, `selectOption` on native `<select>`, `.check()` on native inputs, `:not([disabled])` (section 10.3) |
| Data | `notifications.link` exists and is never filled; `GET /api/notifications` returns it; `Identity.list_users()` exists; `GET /api/amaze/proposals?status=` takes `approved`/`rejected` |

## 3. What you asked, and where this plan answers it
Library, look: 10.1, 10.2, assumption 2. Backend: section 9, assumptions 12-17. Selectors: 10.3. Screenshots: section 8. Lines P1-P30 in `docs/ui-polish.md` order. Fate: **7.5**; **8** the real model's wording; **out** of the PoC or of 7.5 (why given). API: **none** or the field. (Condensed at task 0 to stay within 250 lines: rows of one task and fate merged.)

| # | Screen: issue (short) | Fate | API | Task |
|---|---|---|---|---|
| P1 | consumer Requirement: glossary keys, not names | 7.5 | `element_names` | 5, 15 |
| P2, P29 | consumer Requirement: empty source list after the catalogue lost them (assumption 26); states are one muted line | 7.5 | none (`progress.state`) | 15 |
| P3, P4, P5 | Registry: Owner is a user id; Source is the raw reference; empty state says "(phase 3)" | 7.5 | `GET /api/amaze/people`; `source_name`; none | 5, 13 |
| P6, P9, P21 | Feed: Profile stays offered after profiling (`profile.fetch_id`, `last_file.id`); result line at the top (assumption 19); role buttons a second late (`me` from App) | 7.5 | none | 9 |
| P7, P8 | Feed: same-title requirements look identical; registration owner is a user id | 7.5 | `raised_at`, people | 5, 9 |
| P10 | Feed: ProfilerAgent mock "1 of 1 … are" | 8, mock fixed now (one line) | none | 7 |
| P11 | Feed: coverage card reads "A requirement" | 7.5 | `coverage_titles` | 5, 9 |
| P12 | Mapping: covered column says "with the glossary builder" | 7.5 | `why_none` value (assumption 15) | 5, 10 |
| P14, P16 | Glossary: alias says "Clashes with"; no "Decided" filter (existing `status=` filter) | 7.5 | none | 12 |
| P17, P18, P19 | Builder: `sent_back` run lists every column; Propose mappings offered when nothing is left; result line far from the table (assumption 19) | 7.5 | none | 10 |
| P22, P23, P24 | Rules: returned and refused lists lost (assumption 17); result line far; Go-live: Prepare disabled with no word | 7.5 | none | 11 |
| P27, P28 | Notifications: read once, no count (assumption 18); no link (assumption 16) | 7.5 | none; `link` filled | 6, 14 |
| P13, P15, P26 | Glossary: mock definition wording, editable draft; mock conflict reason; Go-live: mock "column(s)", "rule(s)" | 8 (not one line, assumption 23); P13's editing out (0016 guard) | — | — |
| P20, P30 | Mapping: glossary-rejected column goes to step 06 again (a step 06 change, assumption 24); no request-access button | out | — | — |
| P25 | Loads: legend, purposes | done in phase 7 (task 12); restyled only | — | 8 |

## 4. Assumptions — veto any by number

**Library, look, tests**

1. **Library: Mantine `@mantine/core` and `@mantine/hooks` `9.7.4`** (exact; 2026-10-25), in both UIs (section 10.1; you decide at task 0). Full `@mantine/core/styles.css`; no other Mantine package.
2. **Look (decided at task 0): the Hexaware palette below, with R1's type scale (12/14/16/20/24, 400/600, line height 1.45), radius 6, 4 px grid.** Dark values are derived (decision 3, section 17), outside the palette and marked so in `theme.ts`.

| Token | Light | Dark (derived) | Use |
|---|---|---|---|
| page / panel / subtle fill | `#F8F8F9` / `#FFFFFF` / `#EEEFF2` | `#0B1030` / `#131A3D` / `#1D2550` | backgrounds; content sits on panels |
| lines / strong lines and muted text | `#CBD0E5` / `#535983` | `#323C70` / `#A9AECF` | borders; secondary text |
| body text / headings | `#040D43` / `#07125E` | `#E6E8F5` / `#FFFFFF` | text |
| header bar | `#07125E`, white text | the same | both apps' header |
| primary | `#3C2CDA`, white text on it; also link text | fill the same; link text `#ABA3FF` | buttons, links, selected |
| focus ring and icons | `#1D86FF` | the same | never text |
| danger | `#DA2D2C` (text on panels only; white on it) | text `#FF8F8E`; fill the same | errors |
| warning / info / current-state marker | `#EA9D00` / `#14CBDE` / `#F4CB4E` | the same | fills with `#040D43` text only, never text on white |
| disabled | `#8088A7` | the same | disabled controls only: border and fill, never text (their text is muted on subtle) |
| success | `#1A7F37` (outside the Hexaware palette, marked) | text `#5CC97B` | done lines, released |

3. **One look, enforced:** `apps/backend-ui/src/theme.ts` and `apps/consumer-ui/src/theme.ts` are byte-identical (assumption 2 as a Mantine theme, plus `CONTRAST_PAIRS`: every text/background pair it uses, light and dark); both `package.json` pin the same exact versions; both `main.tsx` import `./theme`. `tests/test_ui_shared_look.py` checks all three and that every pair is at least 4.5:1 (pytest, under 0.1 s).
4. **Light and dark stay:** `defaultColorScheme="auto"`, as today's `prefers-color-scheme`, with assumption 2's dark values; screenshots are light (`colorScheme: "light"`).
5. **No other runtime package:** no icon set, router or web font; the system font stack (Segoe UI on Windows). No icons: tested text stays plain.
6. **Native controls stay native:** every `<select>` is `NativeSelect` (or a plain `<select>`), checkboxes and radios are Mantine `Checkbox`/`Radio` (native inputs), buttons are `Button` with the `disabled` prop only. Never `Select`, `Combobox`, `MultiSelect`, `aria-disabled`. Checked by rendering (section 10.3).
7. **The story spec and `e2e/playwright.config.ts` are not edited** in phase 7.5.
8. **Text inside a tested element never changes;** capitals only by CSS (`text-transform`; Playwright reads `textContent`).
9. **Component checks:** `vitest 5.1.0`, `jsdom 30.1.3`, `@testing-library/react 16.3.3`, `@testing-library/dom 10.4.2` (all over two weeks old, MIT), devDependencies of both UIs; `npm test` = `vitest run`; files `src/**/*.test.tsx`, `fetch` stubbed with synthetic pages from `src/test/fixtures.ts`; no bundle cost. Alternative: Playwright only (then P2, P7, P11 get no test: the story never reaches those states).
10. **Screens harness:** `e2e/playwright.screens.config.ts` (imports the main config's `webServer`; `testDir: "./screens"`), `e2e/screens/screens.spec.ts` walks the story's path and takes section 8's shots; run `npx playwright test -c playwright.screens.config.ts`. Not part of the story run.
11. **Before set at task 2** (section testids only, nothing visible changed), **after set at task 17**, same spec, both committed; the checkpoint 1 pair in `docs/phase75-screenshots/checkpoint1/`.

**Backend (read fields only; no migration)**

12. **Display names: one new read endpoint `GET /api/amaze/people`** (any backend role): `[{id, display_name}]` of `Identity.list_users()`. backend-ui reads it once in App; every user id shown (owner, registered by, signed off by, approved by, decisions, requester, product owner) shows the name; an unknown id shows as itself. Departure from P3/P8's "return the name with the feed": one lookup, not one field per id. Alternative: `owner_name` etc. on each response.
13. **`GET /api/amaze/feeds` adds `source_name`** (the catalogue entry of `feed.source_code`, `null` when unknown: `UnknownSource` as `_description()`), via a router model `FeedRow(Feed)`.
14. **Additive page fields:** `FeedRequirement.raised_at` (the requirement's `created_at`); `FeedPage.coverage_titles` (requirement id → title for every requirement in the latest profile's coverage); `RequirementPage.element_names` (key → current glossary name). In `core/steps/feed_page.py`, `requirement_page.py` (read models, not steps).
15. **P12, the one changed read value:** `_why_none` says `with_glossary_builder` only for an active run's column that `candidates.covered_by()` does not skip (the builder's own rule, `s05_glossary.builder_input`); a covered column gets `None`, shown "waiting for Propose mappings". Veto → phase 8.
16. **P28 links:** `link=` is passed at five sites, nothing else changes: `s12_arrival._tell` (`#/feed/<id>`), `s15_certify` held notice (`#/feed/<id>`), `s20_create_product` Ready/Access restricted (`#/requirement/<id>`) and `_tell_backend` (`#/feed/<id>`), `apps/api/jobs.py` load reports (`#/feed/<id>`). Earlier phases' notices stay text. Each UI makes a link only of its own routes (`#/feed/` backend, `#/requirement/` consumer). Touches three step modules' notify arguments: veto if that counts as step logic.
17. **P22, browser session (recommended):** `sessionStorage` key `amaze.rules.<feedId>` keeps the last Propose rules answer (returned, refused, time), shown "From the last Propose rules in this browser tab, <time>; not stored." Wrapped in try/catch. **Stored (not recommended now):** an append-only `rule_verdicts` table written by step 09 with the proposals (migration `0020`, a step 09 write, a guard): breaks this phase's no-migration, no-step-change rule; phase 8 if wanted.
18. **P27:** App polls `GET /api/notifications` every 15 s for the signed-in user; the tab reads "Notifications (N)", N = notices newer than this user's last opening of the tab (`localStorage`, per app and user); the open list renders from the same poll; Refresh stays. No read state in the database (phase 7 assumption 37).

**Wording and behaviour on screens**

19. **P9, P19, P23, one pattern:** `feed-done` and the error line render under the heading of the section holding the next action (Profile → Summary; Summarise → Sign off; Sign off → Register; Register → Registration) or of the section acted on (Mapping, Rules, Go-live, Loads), and that section scrolls into view. One `feed-done` at a time.
20. **P6:** Profile is offered only while the latest file has no profile (`profile.fetch_id !== last_file.id`); else "Profile N is of this file." Not "Profile again": the same file gives the same profile.
21. **P7:** each requirement row shows "raised <date> by <name>"; no link (the requirement page is the requester's own, in consumer-ui). Departure.
22. **P10:** `ports/llm/mock_rules.py:128` only: "… 1 of 1 requested items is in the file" (verb agrees with the found count). Mock `model_version` stays `"1"`; prompts unchanged; `evaluate_mappings` reads mappings only.
23. **Not one line, so phase 8:** P15 (the reason is the new-element reason, written before step 05 knows of the clash), P26 (plurals on lines 221 and 228). P13: wording phase 8; editing a proposal before approval is out (0016: an element must be one of its proposal's options).
24. **P20 is out of 7.5:** leaving the column out needs `s06_map.mapping_input` to skip it (a step change); its row already says "rejected by the glossary owner". Phase 8 if wanted.
25. **P29:** an ordered list `progress-steps`: Checking the first file · Setting up · Approved for loading · First delivery · Ready; the current one `aria-current="step"`; `delivery_held` and `part_missing` mark step 4 in the warning colour. `progress` stays on the sentence.
26. **P2:** while `next_action` is `pick_source` and `listed` is empty: "No listed source is available any more; the next daily run closes this request." No close on opening (a GET never writes).
27. **Checkpoint 1 screen:** backend-ui Feed registry, with both apps' shell (header, nav, dev user).
28. No new committing endpoint, so no new commit-visibility test.
29. **Time:** pytest gains under 5 s serial; component checks and screens runs are outside pytest. Ceiling unchanged.
30. `docs/ui-polish.md`: each line gains its fate (done in 7.5 task N, phase 8, out); new lines found are appended.
31. **Bundle budget:** each app at most +100 KB gz (JS + CSS) over today; measured cost of R1's component set: +45 KB JS, +36 KB CSS (section 10.1).

## 5. Steps and typed results
None changes. No outcome, transition or message of `CLAUDE.md` moves.

## 6. Data model
No migration. `notifications.link` (exists since phase 0) is filled at five sites (assumption 16).

## 7. Domain models and repositories
No change. The new fields are on read models (`feed_page.py`, `requirement_page.py`) and one router model.

## 8. Synthetic data and the demo paths
**All shots: database `amaze_test`, schema `e2e_story`,** rebuilt by `e2e/serve_api.py` when the screens run starts (so it is ready before S1); the spec walks rows E1 to E9 of phase 7 in order and shoots at each point. Viewport 1280×900, light, `fullPage` for whole pages; a feed page shot is its `section-*` element. Files `docs/phase75-screenshots/{before,after}/<id>-<slug>.png`.

| # | App: screen | State (after) | Lines |
|---|---|---|---|
| S1 | backend: Feed registry, empty | start, before E1 | P5 |
| S2 | consumer: Search, no hits, request form | E1 search | |
| S3 | consumer: Search, requirement raised | E1 submit | |
| S4 | consumer: Requirement, confirm the scope | E1 Find a source | |
| S5 | consumer: Requirement, sources listed | E1 confirm | |
| S6 | consumer: Requirement, `checking_file` | E1 pick | P1, P29 |
| S7 | backend: Feed registry, one candidate | E1 | P3, P4 |
| S8 | backend: Feed, source to profile sections | E2 Profile | P6, P7, P9, P11 |
| S9 | backend: Feed, summary and sign-off form | E2 Summarise | P9, P10 |
| S10 | backend: Feed, registration | E2 Register | P8 |
| S11 | consumer: Requirement, `setting_up` | E2 | P29 |
| S12 | backend: Glossary, pending proposals (Glenn) | E3 | P14 |
| S13 | backend: Glossary, Decided | E4 | P16 |
| S14 | backend: Feed, glossary builder and mapping (Sam) | E5 Propose mappings | P12, P17, P18, P19 |
| S15 | backend: Feed, rules (Sam) | E6 Propose rules | P22, P23 |
| S16 | backend: Feed, go-live with the summary (Blake) | E7 Prepare | P24 |
| S17 | consumer: Requirement, `approved` | E7 | P29 |
| S18 | backend: Feed, loads and product (Opal) | E8 | P25 |
| S19 | consumer: Requirement, Ready | E8 | P29 |
| S20 | consumer: Search, reuse "Available" | E8 | |
| S21 | backend: Feed, loads with the held load | E9 | |
| S22 | backend: Notifications (Opal) | E9 | P27, P28 |
| S23 | consumer: Requirement, Ready with the held note | E9 | |
| S24 | consumer: Notifications (Riley) | E9 | P27, P28 |
| S25 | backend: Status | end | |

## 9. API
| Endpoint | Who | Change |
|---|---|---|
| `GET /api/amaze/people` | any backend role | **new**, read: `[{id, display_name}]` (assumption 12) |
| `GET /api/amaze/feeds` | any backend role | each row adds `source_name` (assumption 13) |
| `GET /api/amaze/feeds/{id}` (and every answer carrying the feed page) | as today | `requirements[].raised_at`, `coverage_titles`; `columns[].why_none` per assumption 15 |
| `GET /api/consumer/requirements/{id}` (and step answers carrying it) | the requester | adds `element_names` |
| `GET /api/notifications` | any dev user | unchanged; `link` now filled for assumption 16's notices |

## 10. UI
### 10.1 Component library (you choose at task 0)
Measured: one scratch Vite 8 build per library of the same set (button, text input, native select, checkbox, radio, textarea, table, badge, alert, card, tabs, link, loader), gzip, against a React-only build (67.9 KB JS).

| | Version (released) | Bundle cost (gz) | Licence | Markup change | Accessibility |
|---|---|---|---|---|---|
| **A. Mantine (recommended)** | `@mantine/core` + `hooks` `9.7.4` (2026-10-25); React ≥ 19.2 | +45 KB JS, +36 KB CSS | MIT | Medium: tags become components, every testid and `data-*` passes through to the native element (checked: `select`, `input`, `button disabled`, `tr data-column`, `a`) | WAI-ARIA patterns, native inputs, visible focus; no published conformance report |
| B. Fluent UI React v9 | `@fluentui/react-components` `9.75.1` (2026-11-02) | +82 KB JS (styles in JS, Griffel) | MIT | High: `Field` wrappers, `makeStyles` for layout; native `Select`, inputs, `disabled` (checked) | Strongest: Microsoft's standard, Windows high contrast, tested with screen readers |
| C. Radix Themes | `@radix-ui/themes` `3.3.0` (2026-01-31; none since) | +25 KB JS, +82 KB CSS | MIT | Medium; no native select component; checkbox and radio are `button role=checkbox/radio`, so `accept-*` and `outcome-*` testids move off native inputs | Good (Radix Primitives); non-native checkbox and radio |

Why A: native controls keep every spec selector (assumption 6), the smallest JS+CSS of the two maintained options, and the simplest API for the builder. B fits an Azure/Microsoft client look best, at twice the JS and more markup change. C's last release is ten months old.

### 10.2 Visual reference (you choose at task 0; one look for both UIs)
- **R1. Quiet enterprise data tool (recommended).** White panels on a cool-grey page, one blue accent, dense tables, statuses as small tinted badges. Palette: page `#F6F8FA`, panel `#FFFFFF`, line `#D8DEE4`, text `#1F2328`, muted `#59636E`, accent `#0B5CAD` (hover `#094A8C`), success `#1A7F37`, warning `#9A6700`, danger `#CF222E`, selected `#DDF4FF`. Type: system UI, 12 / 14 (body, tables) / 16 (section) / 20 (page) / 24 (app title), weights 400/600, line height 1.45; radius 6; 4 px spacing grid.
- **R2. Fluent 2 / Azure portal.** Neutral greys, Microsoft blue, flat cards, compact command bars. Palette: page `#FAFAFA`, panel `#FFFFFF`, stroke `#D1D1D1`, text `#242424`, secondary `#616161`, brand `#0F6CBD`, success `#107C10`, warning `#8A3707`, danger `#C50F1F`. Type: Segoe UI, 12/16, 14/20, 16/22, 20/28, 28/36; radius 4.
- **R3. Analytics terminal (Carbon-like).** Square corners, strong grid, left rails on panels, monospaced codes. Palette: page `#F4F4F4`, layer `#FFFFFF`, text `#161616`, secondary `#525252`, interactive `#0F62FE`, success `#24A148`, warning `#B28600`, error `#DA1E28`. Type: system UI (IBM Plex only if you accept a font package), 12 / 14 / 16 / 20 / 28 / 32; radius 0; 8 px grid.

### 10.3 The selectors the restyle could break, and how each is kept
| Spec selector (line) | Risk | Kept by |
|---|---|---|
| `dev-user` `selectOption` (27), `owner` (76) | a custom select | `NativeSelect` (testid on `<select>`, checked) |
| `accept-*` `.check()` (59), `outcome-meets` (74) | non-native inputs | Mantine `Checkbox`/`Radio` (testid on `<input>`, checked) |
| `[data-testid="approve"]:not([disabled])` (91) | `aria-disabled` | `Button disabled` (native `disabled`, checked); the Decided list (P16) uses `glossary-decided-row`, never `glossary-proposal` |
| button "Search", exact (51, 150); link "See this requirement" (56) | icon or wording | accessible names unchanged; no icons (assumption 5); `Anchor` renders `<a>` |
| `requirement-status`, `requirement-message`, `progress`, `result-message`, `feed-done`, `stages` `toHaveText` exact (54, 61, 64, 65, 135, 144, 160) | added text, placeholders, casing | assumption 8; `stages` stays empty for a held load (no "–"); P29's list is a sibling `progress-steps`; one `feed-done` (assumption 19) |
| `load-row` `toContainText` "on time complete", "trust 100.0", "released", "breaking held", "cpn" (137 to 159) | badges with new words | badges keep `words()` text |
| `mapping-row[data-column]`, `pending-rule`/`rule-row[data-rule]`, `load-row[data-purpose]` (101 to 157) | table components drop `data-*` | `Table.Tr` passes them (checked) |
| `registration` "open", `product`, `go-live-latest` "Version 1", `glossary-decided` "Approved for …", `notification`, `hit-*` "Available", `held-note`, `run-banner-*` | rewording during polish | wording of these elements unchanged; names added beside, not in place of, these words |

Task 1 adds `section-*` testids (for the shots), before any restyle; the spec gains no selector. The story runs after every UI task.

## 11. Files
- **Documents (edit):** this file, `docs/ui-polish.md`, `BUILD_PLAN.md`, `CLAUDE.md`, `README.md`.
- **New:** `apps/{backend-ui,consumer-ui}/src/theme.ts`, `vitest.config.ts`, `src/test/fixtures.ts`, `src/**/*.test.tsx`; `e2e/playwright.screens.config.ts`, `e2e/screens/screens.spec.ts`; `docs/phase75-screenshots/`; `tests/test_ui_shared_look.py`.
- **Edit, UI:** both apps' `package.json`, `package-lock.json`, `index.html`, `src/main.tsx`, `src/App.tsx`, `src/api.ts` and every file in `src/views/`.
- **Edit, backend:** `apps/api/routers/amaze.py`; `core/steps/feed_page.py`, `requirement_page.py`; `core/steps/s12_arrival.py`, `s15_certify.py`, `s20_create_product.py`, `apps/api/jobs.py` (assumption 16 only); `ports/llm/mock_rules.py` (line 128 only).
- **Tests, edit:** `test_api_amaze.py`, `test_api_amaze_onboarding.py`, `test_api_amaze_mapping.py`, `test_api_consumer_sourcing.py`, `test_s12_arrival.py`, `test_s15_certify.py`, `test_s20_create_product.py`, `test_jobs.py`, `test_mock_rules.py`.

## 12. Tasks, in order
Method for every task (by hand, `CLAUDE.md`): unstated assumptions listed first; only section 11's files; RED pasted, then GREEN; an unexpected failure reproduced and its root cause written before the fix; files edited with the file tools only; one commit per task. Every UI task ends with both builds, `npm test` in both UIs and the story (`1 passed`, time pasted).

| # | Behaviour wanted | Test (RED first) | Files | Out of scope |
|---|---|---|---|---|
| 0 | Your decisions in this plan (library, look, vetoes) | none | this file | |
| 1 | `data-testid="section-<name>"` on each feed page section (source, requirements, file, profile, summary, signoff, register, registration, glossary-builder, mapping, rules, go-live, loads, history); nothing visible changes | story passes; builds | `FeedView.tsx`, `RulesSection.tsx`, `GoLiveSection.tsx`, `LoadsSection.tsx` | any style |
| 2 | Screens harness; the **before** set (25 files) committed | screens run `1 passed`; 25 files listed | assumption 10's files, `docs/phase75-screenshots/before/` | polish checks |
| 3 | Component checks in both UIs (assumption 9) | RED: `App.test.tsx` (the dev-user select lists the stubbed users) fails to run; GREEN `npm test` | both apps' `package.json`, lock, `vitest.config.ts`, `src/test/fixtures.ts`, `src/App.test.tsx` | other checks |
| 4 | Mantine and R1 (or your choice) in both UIs; `theme.ts`; the shell in both apps; the Feed registry converted. **CHECKPOINT 1:** before/after of S1, S7 and both shells; sizes; stop | RED: `test_ui_shared_look.py`; component check: registry renders a stubbed list with `feed-link-*`; story | `package.json`, lock, `index.html`, `main.tsx`, `App.tsx`, `theme.ts` (both); `FeedRegistryView.tsx`; the pytest | the registry's polish (task 13) |
| 5 | Read fields: people, `source_name`, `raised_at`, `coverage_titles`, `element_names`, P12's `why_none` | RED: one pytest each in `test_api_amaze.py`, `test_api_amaze_onboarding.py`, `test_api_amaze_mapping.py` (a covered column while the run is active), `test_api_consumer_sourcing.py` | `routers/amaze.py`, `feed_page.py`, `requirement_page.py`, both `api.ts` (types) | UI |
| 6 | Notification links (assumption 16) | RED: `link` asserted in `test_s12_arrival.py` (held), `test_s15_certify.py`, `test_s20_create_product.py` (Ready), `test_jobs.py` (a load report); and one test that the notify calls of steps 12, 15, 20 and `jobs.py` differ from `main` only by `link=` (decision 4) | the five sites | earlier phases' notices |
| 7 | P10 mock wording | RED: `test_mock_rules.py` asserts "1 of 1 requested items is" and "1 of 2 … is"/"2 of 2 … are" | `mock_rules.py:128` | P15, P26 |
| 8 | Feed page converted (all sections, P25 restyled), no behaviour change | component check: a registered feed page renders every `section-*` and `mapping-row[data-column]`; story | `FeedView.tsx`, `RulesSection.tsx`, `GoLiveSection.tsx`, `LoadsSection.tsx` | polish |
| 9 | P6, P7, P8, P9, P11, P21 | RED component checks: Profile hidden when profiled; "raised … by Riley Requester"; owner name; `feed-done` inside the next section; coverage title "no longer selects this feed"; FeedView takes `me` from App (no `/api/me` call) | `App.tsx`, `FeedView.tsx` | |
| 10 | P12 (UI), P17, P18, P19 | RED component checks: "waiting for Propose mappings"; run columns first, skipped under `run-not-asked`; Propose disabled with "Nothing left to map"; result above the mapping table | `FeedView.tsx` | P20 |
| 11 | P22, P23, P24 | RED component checks: returned list survives a remount (sessionStorage); result line in Rules; `prepare-why` "A summary already waits for the owner" | `RulesSection.tsx`, `GoLiveSection.tsx` | stored verdicts |
| 12 | Glossary converted; P14, P16 (decision, name, note) | RED component checks: "Same as: exchange_mic" for `duplicate_alias`; Decided lists `glossary-decided-row` from `status=approved`/`rejected` | `GlossaryView.tsx` | editing proposals |
| 13 | P3, P4, P5; Status and RunBanner converted | RED component checks: owner name, source name with reference secondary, the empty text | `FeedRegistryView.tsx`, `StatusView.tsx`, `RunBanner.tsx` | |
| 14 | Notifications both apps converted; P27, P28 | RED component checks: count after a poll with a newer notice; a `#/feed/` link in backend-ui, text in consumer-ui (and the reverse) | both `App.tsx`, `NotificationsView.tsx` | read state |
| 15 | consumer-ui converted (Search, My requests, Requirement, StatusLine); P1, P2, P29 | RED component checks: element names; the empty-list text; `progress-steps` with the current step marked | consumer `src/views/*` | P30 |
| 16 | Documents: `CLAUDE.md` (theme, component checks, screens config), `BUILD_PLAN.md` (7.5 as built), `README.md` (commands), `docs/ui-polish.md` (fates, new lines) | `test_step_results.py` (reads `CLAUDE.md`) | section 11's documents | |
| 17 | **After** set; story three times; pytest three timed runs; builds and sizes; review against this plan. **CHECKPOINT 2** | section 13 | `docs/phase75-screenshots/after/`, section 17 | merge, push |

## 13. Commands that prove it is done
| Command (from `F:\amaze-cm-poc`) | Expected |
|---|---|
| `.\.venv\Scripts\python -m pytest -q -n 6`, three times, clean (ports 8000/5173/5174 free) | all pass, 0 skipped, `-W error`; median ≤ 200 s, single ≤ 245 s |
| `cd e2e; npx playwright test`, three times | `1 passed` each |
| `cd e2e; npx playwright test -c playwright.screens.config.ts` | `1 passed`; 25 files in `after/` |
| `npm --prefix apps\backend-ui run build`, `npm --prefix apps\backend-ui test`; the same for consumer-ui | exit 0; sizes within assumption 31 |
| `.\scripts\dev.ps1` | three `OK` lines, health `ok` |

## 14. Not in phase 7.5
- P13's editing, P20's step 06 change, P22's stored verdicts, P30's access requests; the mock wording of P13, P15, P26 (phase 8).
- Links on the notices of phases 3 to 6; a read state for notifications.
- Any new outcome, message, guard, migration or agent change; a router library; icons; web fonts.

## 15. Risks
1. **TypeScript 7 with Mantine's types:** not yet built together; `skipLibCheck` is on; task 4 shows it first (checkpoint 1).
2. **Mantine's CSS reset** changes margins and fonts everywhere at once; task 4 replaces `index.html`'s inline rules in both apps in one step.
3. **Polling (P27)** adds a request every 15 s per open tab; the story is unaffected (its checks open the tab).
4. **Screenshot timestamps differ** between before and after; compare layout, not times.
5. **Repository size:** 50 PNGs, about 5 to 10 MB, committed.
6. **The screens spec repeats the story's path:** a later story change needs the same change there.

## 16. Carried forward
**Phase 8:** 1. P13, P15, P26 (the real model's wording); 2. P20 (step 06 skips a column the owner rejected) if wanted; 3. P22 stored verdicts if wanted; 4. links on phases 3 to 6's notices. **Later:** 5. P30, a request-access workflow.

## 17. Results and deviations (recorded during implementation)
**Task 0, decisions (2026-11-18):** 1. Mantine 9.7.4 (assumption 1). 2. The look is assumption 2's palette (it replaces R1's colours; R1's type scale, radius 6, 4 px grid kept); a pytest checks every `theme.ts` text/background pair at 4.5:1 and that both apps import the one theme. 3. Dark mode kept (assumption 4) with derived dark values, marked outside the palette, every dark pair in the same test. 4. All other assumptions approved as written (7, 9, 12, 15, 16, 21, 24 and `sessionStorage` for P22 included); A15: every test expectation the `why_none` change alters is pasted here old → new; A16: a test shows the notify calls differ only by `link=`. 5. Section 3 condensed (rows of one task merged) to stay within 250 lines.

**Tasks 1-4 (checkpoint 1):** 1 `59a48a6` (14 `section-*` wrappers, plain `<div>`s; product and parked lines sit in `section-mapping`). 2 `b19e547` (25 before shots; `SHOTS_SET`, `SHOTS_ONLY` pick the folder and shots; S18 is the band from `product` to `section-loads`). 3 `9a62590`. 4: Mantine, `theme.ts`, both shells, the registry; checkpoint shots S1, S2 (consumer shell), S7. Sizes gz (JS + CSS), measured at `9a62590`, then after task 4: backend-ui 80.43 → 145.35 (+64.92), consumer-ui 74.08 → 129.94 (+55.86); budget +100.
**Deviations:** D1 (task 3) vitest loads itself twice when started from a lower-case drive (`f:\`, Git Bash `/f/`): every file fails "failed to find the current suite"; root cause reproduced (`/F/` passes, `/f/` fails); run `npm test` from PowerShell or `/F/` (comment in `vitest.config.ts`). D2 (task 4) `.warn` was warning-coloured text: now a warning bar (blocks) or a warning fill with `#040D43` text (`.badge.warn`), assumption 2's rule. D3 (task 4) consumer-ui's status line moved from the navy header to a panel strip below it (its ok/bad colours fail on navy). D4 (task 4) unconverted views' plain buttons, inputs and tables get interim theme styles in `index.html` until their task. D5 (task 4) disabled = border `#8088A7`, text muted on subtle (5.85:1); `ResizeObserver` and `matchMedia` stubbed in `src/test/fixtures.ts` (jsdom has neither).
