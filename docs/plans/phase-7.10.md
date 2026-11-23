# Phase 7.10 — One entrance, two personas

Owner-approved scope from the current request. Work on `phase-7.10` from main;
do not merge this phase. Baseline main update was explicitly authorized separately.

## Tasks and verification

1. Add `scripts/setup_replit.sh`: recreate the existing standalone Python 3.14
   runtime and pip target packages from `requirements-dev.txt`, without a virtualenv.
   Prove it against a fresh runtime directory, preserving the working runtime until success.
2. Route `/market/` to consumer UI (Vite base) through the sole port-5000 proxy;
   other paths go to backend UI. Keep direct local ports 5173 and 5174 usable.
   Expose only 5000 -> 80 in `.replit`; cover HTTP and WebSocket routing.
3. Both pickers show exactly dev-operator and dev-requester when four-eyes is off;
   retain all existing users when it is on. Share a stored choice and navigate to the
   selected persona's app, including on local development ports. Keep fixtures intact.
4. Update Playwright stories/screens and product links for `/market/`.
   Run both B10 commands, both UI unit suites, and `pytest -q -n 6`.

Tests first: report actual RED before implementation and GREEN afterwards.
One implementation commit per numbered task. Final verification is the checkpoint;
push the branch and report results without merging.

## Files in scope

`BUILD_PLAN.md`, this plan, `scripts/setup_replit.sh`, `scripts/preview-proxy.mjs`,
`.replit`, consumer Vite configuration, both UI App components and their tests,
shared UI persona helpers and tests, dev identity API and tests if needed,
backend product-link component and affected tests, e2e configs/stories/screens,
and focused runtime/proxy verification tests.
No fixture user removal, dependency upgrades, or changes to business workflows.

## Results and deviations

- The build index lacked a 7.9 heading; added a pointer to its existing plan before 7.10.
- Baseline main was fast-forwarded and pushed to `30a2929` as authorized.
