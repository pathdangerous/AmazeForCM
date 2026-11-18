import { cleanup, screen, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { FEED_ID, feedPage, feedRoutes, renderWithTheme, scheduledLoad, stubFetch, USERS } from "../test/fixtures";
import FeedView from "./FeedView";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const SECTIONS = ["source", "requirements", "file", "profile", "summary", "registration", "glossary-builder", "mapping",
                  "rules", "go-live", "loads", "history"];

function show(page = feedPage(), props: Partial<Parameters<typeof FeedView>[0]> = {}, user = USERS[1]) {
  stubFetch(feedRoutes(page, user));
  return renderWithTheme(
    <FeedView feedId={FEED_ID} devUser={user.id} canAct={false} canRun={false} canMap={true} changed={0}
              onChange={() => undefined} {...props} />);
}

test("task 8: a registered feed page renders every section with a converted heading, rows keep data-column", async () => {
  show(feedPage({ loads: [scheduledLoad()] }));
  await screen.findByTestId("feed-view");
  for (const name of SECTIONS) {
    const heading = screen.getByTestId(`section-${name}`).querySelector("h3");
    expect(heading?.className, name).toContain("mantine-Title");
  }
  const row = document.querySelector('[data-testid="mapping-row"][data-column="qty"]') as HTMLElement;
  expect(row.tagName).toBe("TR");
  expect(within(row).getByTestId("approve-mapping").tagName).toBe("BUTTON");
  const load = document.querySelector('[data-testid="load-row"][data-purpose="scheduled"]') as HTMLElement;
  expect(load.textContent).toContain("on time complete");
  expect(load.textContent).toContain("trust 100.0");
  expect(within(load).getByTestId("stages").textContent).toBe(
    "raw 10→10 · curated 10→10 · validated 10→10 · standard 10→10");
});

test("task 8: the sign-off and register forms keep native radios, a native select and the disabled attribute",
  async () => {
    const candidate = feedPage({ feed: { ...feedPage().feed, stage: "candidate" }, registration: null, signoffs: [],
                                 summary_status: "pending", next_action: "sign_off", columns: [] });
    show(candidate, { canAct: true, canMap: false }, USERS[0]);
    const meets = await screen.findByTestId("outcome-meets");
    expect((meets as HTMLInputElement).type).toBe("radio");
    expect(screen.getByTestId("signoff").hasAttribute("disabled")).toBe(true); // no outcome chosen yet
    cleanup();
    show({ ...candidate, next_action: "register" }, { canAct: true, canMap: false }, USERS[0]);
    expect((await screen.findByTestId("owner")).tagName).toBe("SELECT");
  });
