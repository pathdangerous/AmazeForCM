import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import type { Feed, User } from "../api";
import { Reply, renderWithTheme, stubFetch } from "../test/fixtures";
import FeedLifecycle from "./FeedLifecycle";

const ID = "00000000-0000-4000-8000-0000000000f1";
type Subject = Pick<Feed, "id" | "code" | "stage" | "owner_id">;
const FEED: Subject = { id: ID, code: "syn_test_feed", stage: "registered", owner_id: "dev-data-owner" };
const OWNER: User = { id: "dev-data-owner", display_name: "Devon Owner", email: "d@example.test", roles: ["data_owner"], app: "backend" };
const OPS: User = { id: "dev-operations", display_name: "Opal Operations", email: "o@example.test", roles: ["operations"], app: "backend" };
const OTHER: User = { id: "dev-backend-team", display_name: "Blake Backend", email: "b@example.test", roles: ["backend_team"], app: "backend" };

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const show = (feed: Subject = FEED, me: User | null = OWNER, handlers = { onChanged: vi.fn(), onDeleted: vi.fn() }) => {
  renderWithTheme(<FeedLifecycle feed={feed} me={me} devUser={me?.id ?? ""} {...handlers} />);
  return handlers;
};

test("a live feed offers Pause and Delete to its owner and to operations", () => {
  show(FEED, OWNER);
  expect(screen.getByRole("button", { name: "Pause" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Delete" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Resume" })).toBeNull();
  cleanup();
  show(FEED, OPS);
  expect(screen.getByRole("button", { name: "Pause" })).toBeTruthy();
});

test("a paused feed offers Resume, not Pause", () => {
  show({ ...FEED, stage: "paused" });
  expect(screen.getByRole("button", { name: "Resume" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Pause" })).toBeNull();
});

test("a candidate or rejected feed can be deleted but not paused", () => {
  for (const stage of ["candidate", "rejected"] as const) {
    show({ ...FEED, stage }, OPS);
    expect(screen.queryByRole("button", { name: "Pause" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Resume" })).toBeNull();
    expect(screen.getByRole("button", { name: "Delete" })).toBeTruthy();
    cleanup();
  }
});

test("someone who is neither the owner nor operations sees no controls", () => {
  show(FEED, OTHER);
  expect(screen.queryByRole("button")).toBeNull();
  cleanup();
  show({ ...FEED, owner_id: null }, OWNER); // a feed with no owner: operations only
  expect(screen.queryByRole("button")).toBeNull();
  cleanup();
  show(FEED, null);
  expect(screen.queryByRole("button")).toBeNull();
});

test("Pause asks for a reason, then sends it and tells the page", async () => {
  const fetchStub = stubFetch({ [`POST /api/amaze/feeds/${ID}/pause`]: { result: { outcome: "paused" } } });
  const { onChanged } = show();
  fireEvent.click(screen.getByRole("button", { name: "Pause" }));
  const dialog = screen.getByRole("dialog", { name: "Pause syn_test_feed" });
  const confirm = within(dialog).getByRole("button", { name: "Pause feed" }) as HTMLButtonElement;
  expect(confirm.disabled).toBe(true); // no reason yet
  fireEvent.change(within(dialog).getByLabelText("Why it is paused"), { target: { value: "   " } });
  expect(confirm.disabled).toBe(true); // blank
  fireEvent.change(within(dialog).getByLabelText("Why it is paused"), { target: { value: "The vendor changes its layout" } });
  expect(confirm.disabled).toBe(false);
  fireEvent.click(confirm);
  await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
  const call = fetchStub.mock.calls.find(([u]) => String(u).endsWith("/pause"))!;
  expect(JSON.parse(String(call[1]?.body))).toEqual({ reason: "The vendor changes its layout" });
  expect(screen.queryByRole("dialog")).toBeNull();
});

test("Resume asks for a reason and sends it", async () => {
  const fetchStub = stubFetch({ [`POST /api/amaze/feeds/${ID}/resume`]: { result: { outcome: "resumed" } } });
  const { onChanged } = show({ ...FEED, stage: "paused" }, OPS);
  fireEvent.click(screen.getByRole("button", { name: "Resume" }));
  const dialog = screen.getByRole("dialog", { name: "Resume syn_test_feed" });
  fireEvent.change(within(dialog).getByLabelText("Why it is resumed"), { target: { value: "The layout is stable" } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Resume feed" }));
  await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
  expect(JSON.parse(String(fetchStub.mock.calls[0][1]?.body))).toEqual({ reason: "The layout is stable" });
});

test("Cancel closes the dialog and sends nothing", () => {
  const fetchStub = stubFetch({});
  show();
  fireEvent.click(screen.getByRole("button", { name: "Pause" }));
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(fetchStub).not.toHaveBeenCalled();
});

test("Delete needs a reason and the feed code typed exactly, then deletes", async () => {
  const fetchStub = stubFetch({ [`DELETE /api/amaze/feeds/${ID}`]: { outcome: "deleted", code: "syn_test_feed", rows_deleted: { feeds: 1 } } });
  const { onDeleted } = show();
  fireEvent.click(screen.getByRole("button", { name: "Delete" }));
  const dialog = screen.getByRole("dialog", { name: "Delete syn_test_feed" });
  const go = within(dialog).getByRole("button", { name: "Delete feed" }) as HTMLButtonElement;
  expect(go.disabled).toBe(true);
  fireEvent.change(within(dialog).getByLabelText("Why it is deleted"), { target: { value: "The demo is over" } });
  expect(go.disabled).toBe(true); // the code is not typed yet
  const code = within(dialog).getByLabelText("Type syn_test_feed to confirm");
  fireEvent.change(code, { target: { value: "syn_test_fee" } });
  expect(go.disabled).toBe(true); // not the whole code
  fireEvent.change(code, { target: { value: "SYN_TEST_FEED" } });
  expect(go.disabled).toBe(true); // not the same case
  fireEvent.change(code, { target: { value: "syn_test_feed" } });
  expect(go.disabled).toBe(false);
  fireEvent.click(go);
  await waitFor(() => expect(onDeleted).toHaveBeenCalledTimes(1));
  const call = fetchStub.mock.calls[0];
  expect(call[1]?.method).toBe("DELETE");
  expect(JSON.parse(String(call[1]?.body))).toEqual({ reason: "The demo is over", confirm_code: "syn_test_feed" });
});

test("a refusal by the API shows its message in the dialog and keeps it open with the typed text", async () => {
  stubFetch({ [`DELETE /api/amaze/feeds/${ID}`]: new Reply(409, { detail: "syn_test_feed has a load in progress: wait for it to finish" }) });
  const { onDeleted } = show();
  fireEvent.click(screen.getByRole("button", { name: "Delete" }));
  const dialog = screen.getByRole("dialog");
  fireEvent.change(within(dialog).getByLabelText("Why it is deleted"), { target: { value: "The demo is over" } });
  fireEvent.change(within(dialog).getByLabelText("Type syn_test_feed to confirm"), { target: { value: "syn_test_feed" } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Delete feed" }));
  expect((await within(dialog).findByRole("alert")).textContent).toContain("load in progress");
  expect(onDeleted).not.toHaveBeenCalled();
  expect((within(dialog).getByLabelText("Why it is deleted") as HTMLTextAreaElement).value).toBe("The demo is over");
});
