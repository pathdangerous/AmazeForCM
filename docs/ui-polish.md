# UI polish

A running list of UI polish items, one line each: screen, issue, proposed fix. Items are added at the end of each phase and not fixed unless asked. Phase 7.5 works through them (`BUILD_PLAN.md`).

| Screen | Issue | Proposed fix |
|---|---|---|
| consumer-ui, Requirement | Elements show glossary keys (`open_price`), not names | Return each element's glossary name with the page and show the name |
| consumer-ui, Requirement | When every listed source has left the catalogue, the page says "Pick a source…" with an empty list until the next daily run closes it as `no_source_found` (independent review, fix 7) | Show "No listed source is available any more" while the list is empty, or run the close when the page is opened |
| backend-ui, Feed registry | The Owner column shows the user id (`dev-data-owner`), not the person's name (found while planning phase 4) | Return the owner's display name with the feed and show the name |
| backend-ui, Feed registry | The Source column shows the raw reference (`files/exchange_volumes.csv`), not the source's name (found while planning phase 4) | Show the catalogue name, with the reference as secondary text |
| backend-ui, Feed registry | The empty state says "(phase 3)", a project word the backend team does not know (found while planning phase 4) | Say "Feeds appear here when a requester's source is selected." |
| backend-ui, Feed | "Profile the latest file" stays offered after the latest file is profiled; pressing it profiles the same file again as a new version (phase 4 browser run, row L) | Offer it only when a newer file has arrived, or label it "Profile again" with the file's date |
| backend-ui, Feed | Two requirements with the same title ("volume") are listed, and their coverage shown, as identical rows (row L) | Show each requirement's raised date and requester's name, and link it |
| backend-ui, Feed | The registration shows the owner's user id (`dev-data-owner`), not the name chosen in the list (rows L, M) | Show the owner's display name, as in the owner list |
| backend-ui, Feed | After Profile, Summarise or Sign off, the next form appears lower on the page while the result line is at the top; on a long profile the next step is out of view (rows L to O) | Scroll to the next form, or show the result line beside the button pressed |
| backend-ui, Feed | The ProfilerAgent's summary reads "1 of 1 requested items are in the file" (the mock's wording) | Word the mock's summary "1 of 1 requested items is in the file"; the real model's wording is for phase 8 |
| backend-ui, Feed | Coverage cards of requirements that no longer select the feed read 'A requirement' | Show the requirement's title, marked 'no longer selects this feed', or hide the card |
