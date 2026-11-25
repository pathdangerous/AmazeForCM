import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import type { DeliveriesShown, DeliveryRowT, FeedScheduleT } from "../api";
import { FEED_ID, renderWithTheme, stubFetch, USERS } from "../test/fixtures";
import DeliveriesTab from "./DeliveriesTab";

const URL = (w = "30") => `GET /api/amaze/feeds/${FEED_ID}/deliveries?window=${w}`;
const TEAM = USERS[0];

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const SCHEDULE: FeedScheduleT = {
  id: "s1", feed_id: FEED_ID, version: 1, kind: "recurring", frequency: "business_days", weekday: null, day_of_month: null,
  expected_time: "07:00:00", grace_minutes: 60, timezone: "UTC", notify_owner: true, decided_by: "dev-backend-team",
  created_at: "2026-11-01T08:00:00Z",
};

function row(day: number, over: Partial<DeliveryRowT> = {}): DeliveryRowT {
  const dd = String(day).padStart(2, "0");
  return { due_at: `2026-11-${dd}T07:00:00Z`, schedule_version: 1, grace_minutes: 60, status: "on_time", timing: "on_time",
           result: "released", load_id: `l${day}`, received_at: `2026-11-${dd}T07:10:00Z`, late_minutes: null,
           minutes_from_due: 10, rows: 100 + day, ...over };
}

// 2026-11-09 is a Monday: on time, late by 30, missing, held, resent, awaited
const ROWS: DeliveryRowT[] = [
  row(9), row(10, { status: "late", timing: "late", late_minutes: 30, minutes_from_due: 90 }),
  row(11, { status: "missing", timing: "missing", result: null, load_id: null, received_at: null, minutes_from_due: null, rows: null }),
  row(12, { status: "held", result: "held" }), row(13, { status: "resent_quarantined", result: "quarantined", rows: null }),
  row(16, { status: "awaited", timing: "awaited", result: null, load_id: null, received_at: null, minutes_from_due: null, rows: null }),
];

function shown(over: Partial<DeliveriesShown> = {}): DeliveriesShown {
  return {
    outcome: "deliveries_shown", feed_id: FEED_ID, window: "30", schedule: SCHEDULE, live_since: "2026-11-09T06:00:00Z",
    now: "2026-11-16T07:30:00Z", start: "2026-11-08T18:00:00Z", rows: ROWS, unexpected: [],
    figures: { expected: 5, on_time: 3, late: 1, missing: 1, on_time_pct: 60, mean_late_minutes: 30, streak: 2,
               next_expected: "2026-11-16T07:00:00Z", unexpected: 0 },
    ...over,
  };
}

function show(answer: unknown, w = "30") {
  const fetchStub = stubFetch({ [URL(w)]: answer });
  const onOpenSchedule = vi.fn();
  renderWithTheme(<DeliveriesTab feedId={FEED_ID} devUser={TEAM.id} changed={0} onOpenSchedule={onOpenSchedule} />);
  return { fetchStub, onOpenSchedule };
}

test("the headline figures are the figures the API computed", async () => {
  show(shown());
  expect((await screen.findByTestId("dlv-on-time-pct")).textContent).toContain("60%");
  expect(screen.getByTestId("dlv-late").textContent).toContain("1");
  expect(screen.getByTestId("dlv-missing").textContent).toContain("1");
  expect(screen.getByTestId("dlv-mean-late").textContent).toContain("30 min");
  expect(screen.getByTestId("dlv-streak").textContent).toContain("2");
  expect(screen.getByTestId("dlv-next").textContent).toContain("2026-11-16 07:00 UTC");
  expect(screen.getByTestId("dlv-schedule").textContent).toBe("Recurring, on business days at 07:00 UTC, 60 min grace");
});

test("the calendar has one cell per expected delivery, each with its status in words as well as a colour", async () => {
  show(shown());
  const cells = await screen.findAllByTestId("dlv-cell");
  expect(cells.map((c) => [c.getAttribute("data-day"), c.getAttribute("data-status"), c.textContent])).toEqual([
    ["2026-11-09", "on_time", "09On time"], ["2026-11-10", "late", "10Late 30 min"], ["2026-11-11", "missing", "11Missing"],
    ["2026-11-12", "held", "12Held"], ["2026-11-13", "resent_quarantined", "13Resent, quarantined"],
    ["2026-11-16", "awaited", "16Awaited"]]);
  const late = cells[1];
  expect(late.getAttribute("style")).toContain("var(--amz-dlv-late)");
  expect(late.getAttribute("aria-label")).toBe("2026-11-10, expected 07:00 UTC: Late 30 min");
  const legend = screen.getByTestId("dlv-legend").textContent ?? "";
  for (const word of ["On time", "Late", "Missing", "Held", "Resent, quarantined", "Awaited"]) expect(legend).toContain(word);
});

test("the table lists every expected delivery with its arrival, its minutes from expected and its rows", async () => {
  show(shown());
  const rows = await screen.findAllByTestId("dlv-row");
  expect(rows.length).toBe(ROWS.length);
  const first = within(rows[rows.length - 1]).getAllByRole("cell").map((c) => c.textContent);
  expect(first.slice(0, 6)).toEqual(["2026-11-09 07:00 UTC", "2026-11-09 07:10 UTC", "10", "On time", "released", "109"]);
});

test("the arrival chart and the rows chart draw a mark for each delivery that has the value", async () => {
  show(shown());
  await screen.findByTestId("dlv-arrival-chart");
  expect(screen.getAllByTestId("dlv-point").length).toBe(4); // the missing and the awaited deliveries did not arrive
  expect(screen.getAllByTestId("dlv-point").filter((p) => p.tagName === "rect").length).toBe(1); // the held one is a square
  expect(screen.getAllByTestId("dlv-bar").length).toBe(3); // only a certified delivery has a row count
});

test("a different window asks the API for it", async () => {
  const fetchStub = stubFetch({ [URL()]: shown(), [URL("90")]: shown({ window: "90" }) });
  renderWithTheme(<DeliveriesTab feedId={FEED_ID} devUser={TEAM.id} changed={0} onOpenSchedule={vi.fn()} />);
  fireEvent.change(await screen.findByTestId("dlv-window"), { target: { value: "90" } });
  await waitFor(() => expect(fetchStub.mock.calls.some(([u]) => String(u).endsWith("window=90"))).toBe(true));
});

test("a one-time feed shows its loads and the label, and no calendar, late or missing", async () => {
  show({ outcome: "one_time_shown", feed_id: FEED_ID, label: "One-time load", schedule: { ...SCHEDULE, kind: "one_time", frequency: null,
        expected_time: null }, loads: [{ load_id: "l1", received_at: "2026-11-10T03:00:00Z", status: "released", purpose: "scheduled", rows: 7 }] });
  expect((await screen.findByTestId("dlv-one-time-label")).textContent).toBe("One-time load");
  expect(screen.getByTestId("dlv-one-time-loads").textContent).toContain("2026-11-10 03:00 UTC");
  expect(screen.queryByTestId("dlv-calendar")).toBeNull();
  expect(screen.queryByTestId("dlv-late")).toBeNull();
  expect(screen.queryByTestId("dlv-missing")).toBeNull();
});

test("a feed with no schedule says so and points to where it is set", async () => {
  const { onOpenSchedule } = show({ outcome: "schedule_not_set", feed_id: FEED_ID });
  expect((await screen.findByTestId("dlv-not-set")).textContent).toContain("Schedule not set");
  fireEvent.click(screen.getByTestId("dlv-open-schedule"));
  expect(onOpenSchedule).toHaveBeenCalled();
});

test("unexpected deliveries are listed with why", async () => {
  show(shown({ unexpected: [
    { load_id: "u1", received_at: "2026-11-15T07:00:00Z", status: "released", reason: "no_delivery_was_due" },
    { load_id: "u2", received_at: "2026-11-12T07:40:00Z", status: "released", reason: "second_delivery_for_one_due" }] }));
  const list = await screen.findByTestId("dlv-unexpected");
  expect(list.textContent).toContain("2026-11-15 07:00 UTC: no delivery was due then");
  expect(list.textContent).toContain("a second delivery for one that was already received");
});

test("a feed that has not loaded says no delivery is expected", async () => {
  show(shown({ rows: [], live_since: null, figures: { expected: 0, on_time: 0, late: 0, missing: 0, on_time_pct: null,
        mean_late_minutes: null, streak: 0, next_expected: null, unexpected: 0 } }));
  expect((await screen.findByTestId("dlv-not-live")).textContent).toContain("no delivery is expected");
  expect(screen.getByTestId("dlv-on-time-pct").textContent).toContain("none due yet");
  expect(screen.queryByTestId("dlv-calendar")).toBeNull();
});
