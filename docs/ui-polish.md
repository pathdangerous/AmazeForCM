# UI polish

A running list of UI polish items, one line each: screen, issue, proposed fix. Items are added at the end of each phase and not fixed unless asked. Phase 7.5 works through them (`BUILD_PLAN.md`).

| Screen | Issue | Proposed fix |
|---|---|---|
| consumer-ui, Requirement | Elements show glossary keys (`open_price`), not names | Return each element's glossary name with the page and show the name |
| consumer-ui, Requirement | When every listed source has left the catalogue, the page says "Pick a source…" with an empty list until the next daily run closes it as `no_source_found` (independent review, fix 7) | Show "No listed source is available any more" while the list is empty, or run the close when the page is opened |
| backend-ui, Feed registry | The Owner column shows the user id (`dev-data-owner`), not the person's name (found while planning phase 4) | Return the owner's display name with the feed and show the name |
| backend-ui, Feed registry | The Source column shows the raw reference (`files/exchange_volumes.csv`), not the source's name (found while planning phase 4) | Show the catalogue name, with the reference as secondary text |
| backend-ui, Feed registry | The empty state says "(phase 3)", a project word the backend team does not know (found while planning phase 4) | Say "Feeds appear here when a requester's source is selected." |
