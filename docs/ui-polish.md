# UI polish

A running list of UI polish items, one line each: screen, issue, proposed fix. Items are added at the end of each phase and not fixed unless asked. Phase 7.5 works through them (`BUILD_PLAN.md`).

| Screen | Issue | Proposed fix |
|---|---|---|
| consumer-ui, Requirement | Elements show glossary keys (`open_price`), not names | Return each element's glossary name with the page and show the name |
| consumer-ui, Requirement | When every listed source has left the catalogue, the page says "Pick a source…" with an empty list until the next daily run closes it as `no_source_found` (independent review, fix 7) | Show "No listed source is available any more" while the list is empty, or run the close when the page is opened |
