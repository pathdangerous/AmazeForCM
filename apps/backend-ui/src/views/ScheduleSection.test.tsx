import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import type { FeedScheduleT } from "../api";
import { FEED_ID, renderWithTheme, stubFetch, USERS } from "../test/fixtures";
import ScheduleSection from "./ScheduleSection";

const TEAM = USERS[0];
const URL = `POST /api/amaze/feeds/${FEED_ID}/schedule`;

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function version(n: number, over: Partial<FeedScheduleT> = {}): FeedScheduleT {
  return { id: `s${n}`, feed_id: FEED_ID, version: n, kind: "recurring", frequency: "business_days", weekday: null,
           day_of_month: null, expected_time: "07:00:00", grace_minutes: 60, timezone: "UTC", notify_owner: true,
           decided_by: "dev-backend-team", created_at: "2026-11-02T08:00:00Z", ...over };
}

function show(schedule: FeedScheduleT | null, schedules: FeedScheduleT[] = schedule ? [schedule] : [], me = TEAM) {
  const onChanged = vi.fn();
  renderWithTheme(<ScheduleSection feedId={FEED_ID} schedule={schedule} schedules={schedules} me={me} devUser={me.id}
                                   onChanged={onChanged} />);
  return onChanged;
}

test("the schedule in force is written in one line with who set it", () => {
  show(version(2));
  expect(screen.getByTestId("schedule-current").textContent).toContain("Recurring, on business days at 07:00 UTC, 60 min grace");
  expect(screen.getByTestId("schedule-who").textContent).toContain("Version 2, set by dev-backend-team");
});

test("a feed without a schedule says so and offers Set schedule", () => {
  stubFetch({});
  show(null);
  expect(screen.getByTestId("schedule-not-set").textContent).toBe("Schedule not set");
  expect(screen.getByTestId("schedule-change").textContent).toBe("Set schedule");
});

test("a one-time load is labelled as one", () => {
  show(version(1, { kind: "one_time", frequency: null, expected_time: null }));
  expect(screen.getByTestId("schedule-current").textContent).toBe("One-time load");
});

test("Change schedule posts the new schedule and the page is read again", async () => {
  const fetchStub = stubFetch({ [URL]: { outcome: "schedule_set", schedule: version(2) } });
  const onChanged = show(version(1));
  fireEvent.click(screen.getByTestId("schedule-change"));
  expect(screen.getByTestId("feed-schedule-form")).toBeTruthy();
  fireEvent.change(screen.getByTestId("feed-schedule-time"), { target: { value: "08:30" } });
  fireEvent.click(screen.getByTestId("schedule-save"));
  await waitFor(() => expect(onChanged).toHaveBeenCalled());
  const call = fetchStub.mock.calls.find(([u]) => String(u).endsWith("/schedule"))!;
  expect(JSON.parse(call[1]?.body as string)).toMatchObject({ kind: "recurring", expected_time: "08:30", grace_minutes: 60 });
  expect(screen.getByTestId("schedule-note").textContent).toBe("Saved as version 2.");
});

test("the same schedule is reported as unchanged", async () => {
  stubFetch({ [URL]: { outcome: "schedule_unchanged", schedule: version(1) } });
  show(version(1));
  fireEvent.click(screen.getByTestId("schedule-change"));
  fireEvent.click(screen.getByTestId("schedule-save"));
  expect((await screen.findByTestId("schedule-note")).textContent).toContain("Nothing changed");
});

test("earlier versions are listed with who and when", () => {
  show(version(2), [version(1, { expected_time: "06:00:00" }), version(2)]);
  const list = screen.getByTestId("schedule-versions");
  expect(list.textContent).toContain("Version 1: Recurring, on business days at 06:00 UTC");
  expect(list.textContent).toContain("dev-backend-team");
});

test("only the backend team sees the button", () => {
  show(version(1), [version(1)], USERS[1]);
  expect(screen.queryByTestId("schedule-change")).toBeNull();
});
