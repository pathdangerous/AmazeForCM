import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { FEED_ID, Reply, feedPage, renderWithTheme, stubFetch } from "../test/fixtures";
import ProfileHistory from "./ProfileHistory";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const AT = "2026-11-18T09:00:00Z";
const profile = (version: number, keep_reason: string | null = null) =>
  ({ version, outcome: "profiled", at: AT, id: `00000000-0000-4000-8000-00000000010${version}`, keep_reason });
const PEOPLE = { "dev-data-owner": "Devon Owner" };
const ERASE = `POST /api/amaze/feeds/${FEED_ID}/profiles/erase`;

const show = (over = {}, canErase = true, onChange = vi.fn()) => {
  const page = feedPage({ profiles: [profile(1, "registration"), profile(2), profile(3), profile(4, "latest")], signoffs: [], ...over });
  renderWithTheme(<ProfileHistory page={page} devUser="dev-data-owner" people={PEOPLE} canErase={canErase} onChange={onChange} />);
  return onChange;
};

test("a profile that stays says why, and an erased one says who erased it and why", () => {
  show({ profiles: [profile(1, "registration"), profile(4, "latest")],
    erasures: [{ id: "e1", at: "2026-11-20T10:00:00Z", by: "dev-data-owner", reason: "Not needed any more.", erased_versions: [2, 3],
      erased_count: 2, kept_count: 1 }] });
  expect(screen.getByTestId("history-profile-1").textContent).toContain("kept: its registration");
  expect(screen.getByTestId("history-profile-4").textContent).toContain("kept: it is the latest");
  for (const version of [2, 3]) {
    const erased = screen.getByTestId(`history-profile-${version}`).textContent ?? "";
    expect(erased).toContain(`Profile ${version}: erased by Devon Owner`);
    expect(erased).toContain("Not needed any more.");
  }
});

test("select, reason, confirm: the erase is asked of the API with the version and the reason, then the page is read again", async () => {
  const fetchStub = stubFetch({ [ERASE]: { result: { outcome: "profiles_erased", erasure_id: "e1", feed_id: FEED_ID,
    erased_versions: [2, 3], kept: [] }, feed: feedPage() } });
  const onChange = show();
  expect(screen.getByTestId("erase-preview").textContent).toContain("Would erase: Profile 2, Profile 3");
  expect(screen.getByTestId("erase-preview").textContent).toContain("Kept: Profile 1 (its registration)");
  const erase = screen.getByRole("button", { name: "Erase" }) as HTMLButtonElement;
  expect(erase.disabled).toBe(true); // a reason first
  fireEvent.change(screen.getByLabelText("Why it is erased"), { target: { value: "Old files nobody uses." } });
  fireEvent.click(erase);
  expect(fetchStub.mock.calls.some(([, i]) => i?.method === "POST")).toBe(false); // nothing yet: it asks again
  fireEvent.click(screen.getByRole("button", { name: "Confirm erase" }));
  await waitFor(() => expect(onChange).toHaveBeenCalled());
  const [, init] = fetchStub.mock.calls.find(([, i]) => i?.method === "POST")!;
  expect(JSON.parse(String(init?.body))).toEqual({ before_version: 4, reason: "Old files nobody uses." });
  expect(screen.getByTestId("erase-result").textContent).toContain("Erased profile 2, 3");
});

test("the select picks the cutoff and the preview follows it", () => {
  show();
  fireEvent.change(screen.getByLabelText("Erase profiles older than"), { target: { value: "3" } });
  expect(screen.getByTestId("erase-preview").textContent).toContain("Would erase: Profile 2.");
});

test("when every profile is kept the panel says nothing can go, with no form", () => {
  show({ profiles: [profile(1, "registration"), profile(2, "latest")] });
  expect(screen.getByTestId("erase-none").textContent).toContain("Nothing can be erased");
  expect(screen.queryByRole("button", { name: "Erase" })).toBeNull();
});

test("a person who is not the owner or operations sees the history but no erase", () => {
  show({}, false);
  expect(screen.getByTestId("history-profile-2")).toBeTruthy();
  expect(screen.queryByLabelText("Erase profiles older than")).toBeNull();
});

const AGAIN = `POST /api/amaze/feeds/${FEED_ID}/profile-again`;
const showAgain = (canProfile: boolean, over = {}) => {
  const onChange = vi.fn();
  renderWithTheme(<ProfileHistory page={feedPage({ profiles: [profile(1, "registration"), profile(2, "latest")], signoffs: [], ...over })}
    devUser="dev-backend-team" people={PEOPLE} canErase={false} canProfile={canProfile} onChange={onChange} />);
  return onChange;
};

test("Profile again runs step 02 on the latest file, says no sign-off is made, and reads the page again", async () => {
  const fetchStub = stubFetch({ [AGAIN]: { result: { outcome: "profiled_again", profile: { version: 3 } }, feed: feedPage() } });
  const onChange = showAgain(true);
  expect(screen.getByTestId("profile-again-note").textContent).toContain("No sign-off is made");
  fireEvent.click(screen.getByRole("button", { name: "Profile again" }));
  await waitFor(() => expect(onChange).toHaveBeenCalled());
  expect(fetchStub.mock.calls.some(([, i]) => i?.method === "POST")).toBe(true);
  expect(screen.getByTestId("profile-again-result").textContent).toContain("Profile 3 was made");
});

test("a file with other columns is shown as an answer, with the columns, and the page is not changed", async () => {
  stubFetch({ [AGAIN]: { result: { outcome: "profile_differs", added: ["extra"], missing: ["qty"], detail: "The file's columns differ." },
    feed: feedPage() } });
  const onChange = showAgain(true);
  fireEvent.click(screen.getByRole("button", { name: "Profile again" }));
  const result = await screen.findByTestId("profile-again-result");
  expect(result.textContent).toContain("The file's columns differ.");
  expect(result.textContent).toContain("New: extra");
  expect(result.textContent).toContain("Missing: qty");
  expect(onChange).not.toHaveBeenCalled();
});

test("Profile again is offered only on a registered feed, to someone who may", () => {
  showAgain(false);
  expect(screen.queryByRole("button", { name: "Profile again" })).toBeNull();
  cleanup();
  showAgain(true, { feed: { ...feedPage().feed, stage: "candidate" } });
  expect(screen.queryByRole("button", { name: "Profile again" })).toBeNull();
});

test("a refusal by the API is shown and the form keeps what was typed", async () => {
  stubFetch({ [ERASE]: new Reply(403, { detail: "Only the owner of syn_test_feed or the operations team may do this" }) });
  const onChange = show();
  fireEvent.change(screen.getByLabelText("Why it is erased"), { target: { value: "Old." } });
  fireEvent.click(screen.getByRole("button", { name: "Erase" }));
  fireEvent.click(screen.getByRole("button", { name: "Confirm erase" }));
  expect((await screen.findByRole("alert")).textContent).toContain("Not allowed"); // describeError's words for a 403
  expect((screen.getByLabelText("Why it is erased") as HTMLInputElement).value).toBe("Old.");
  expect(onChange).not.toHaveBeenCalled();
});
