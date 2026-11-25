import { NativeSelect, TextInput } from "@mantine/core";
import type { FeedScheduleT, ScheduleBody } from "../api";

// Phase 7.21: "Recurring feed or one-time load?" and, for a recurring feed, when it is expected. The same form is on the
// upload screen (required before "Create the feed") and on the feed page ("Change schedule"). Radios and selects are native.
export type ScheduleValue = {
  kind: "" | "recurring" | "one_time"; frequency: "daily" | "business_days" | "weekly" | "monthly"; weekday: string;
  day: string; time: string; grace: string; timezone: string; notify: boolean;
};

export const EMPTY_SCHEDULE: ScheduleValue = {
  kind: "", frequency: "daily", weekday: "0", day: "1", time: "07:00", grace: "60", timezone: "UTC", notify: true,
};

export const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const FREQUENCIES: { value: ScheduleValue["frequency"]; label: string }[] = [
  { value: "daily", label: "Every day" }, { value: "business_days", label: "Business days (Monday to Friday)" },
  { value: "weekly", label: "Weekly" }, { value: "monthly", label: "Monthly" },
];
const DAYS = [...Array.from({ length: 28 }, (_, i) => String(i + 1)), "last"];

export function scheduleValid(v: ScheduleValue): boolean {
  if (v.kind === "") return false;
  if (v.kind === "one_time") return true;
  const grace = Number(v.grace);
  return /^\d\d:\d\d(:\d\d)?$/.test(v.time) && Number.isInteger(grace) && grace >= 0 && grace <= 1440 && v.timezone.trim() !== "";
}

export function scheduleBody(v: ScheduleValue): ScheduleBody {
  if (v.kind !== "recurring") return { kind: "one_time" };
  return {
    kind: "recurring", frequency: v.frequency, weekday: v.frequency === "weekly" ? Number(v.weekday) : null,
    day_of_month: v.frequency === "monthly" ? v.day : null, expected_time: v.time, grace_minutes: Number(v.grace),
    timezone: v.timezone.trim(), notify_owner: v.notify,
  };
}

export function valueOf(s: FeedScheduleT | null | undefined): ScheduleValue {
  if (!s) return EMPTY_SCHEDULE;
  if (s.kind === "one_time") return { ...EMPTY_SCHEDULE, kind: "one_time" };
  return {
    kind: "recurring", frequency: s.frequency ?? "daily", weekday: String(s.weekday ?? 0), day: s.day_of_month ?? "1",
    time: (s.expected_time ?? "07:00").slice(0, 5), grace: String(s.grace_minutes), timezone: s.timezone, notify: s.notify_owner,
  };
}

// One line, as the pages write it ("Recurring, on business days at 07:00 UTC, 60 min grace").
export function describeSchedule(s: ScheduleBody): string {
  if (s.kind === "one_time") return "One-time load";
  const when = { daily: "every day", business_days: "on business days", weekly: `every ${WEEKDAYS[s.weekday ?? 0]}`,
                 monthly: s.day_of_month === "last" ? "on the last day of the month" : `on day ${s.day_of_month} of the month`,
               }[s.frequency ?? "daily"];
  return `Recurring, ${when} at ${(s.expected_time ?? "").slice(0, 5)} ${s.timezone}, ${s.grace_minutes} min grace`;
}

export default function ScheduleForm({ value, onChange, idPrefix = "schedule" }: {
  value: ScheduleValue; onChange: (next: ScheduleValue) => void; idPrefix?: string;
}) {
  const set = (patch: Partial<ScheduleValue>) => onChange({ ...value, ...patch });
  const grace = Number(value.grace);
  const graceBad = value.grace.trim() === "" || !Number.isInteger(grace) || grace < 0 || grace > 1440;
  return (
    <fieldset data-testid={`${idPrefix}-form`} style={{ border: "1px solid var(--amz-border, #ccc)", margin: "var(--mantine-spacing-sm) 0" }}>
      <legend>Recurring feed or one-time load?</legend>
      <div role="radiogroup" aria-label="Recurring feed or one-time load" style={{ display: "flex", gap: 16 }}>
        <label>
          <input type="radio" name={`${idPrefix}-kind`} data-testid={`${idPrefix}-kind-recurring`} checked={value.kind === "recurring"}
                 onChange={() => set({ kind: "recurring" })} /> Recurring feed
        </label>
        <label>
          <input type="radio" name={`${idPrefix}-kind`} data-testid={`${idPrefix}-kind-one_time`} checked={value.kind === "one_time"}
                 onChange={() => set({ kind: "one_time" })} /> One-time load
        </label>
      </div>
      {value.kind === "" && <p className="muted" data-testid={`${idPrefix}-required`}>Choose one to continue.</p>}
      {value.kind === "recurring" && (
        <div className="toolbar" data-testid={`${idPrefix}-fields`}>
          <NativeSelect label="How often" data-testid={`${idPrefix}-frequency`} value={value.frequency} data={FREQUENCIES}
                        onChange={(e) => set({ frequency: e.currentTarget.value as ScheduleValue["frequency"] })} />
          {value.frequency === "weekly" && (
            <NativeSelect label="On" data-testid={`${idPrefix}-weekday`} value={value.weekday}
                          data={WEEKDAYS.map((label, i) => ({ value: String(i), label }))}
                          onChange={(e) => set({ weekday: e.currentTarget.value })} />
          )}
          {value.frequency === "monthly" && (
            <NativeSelect label="On day" data-testid={`${idPrefix}-day`} value={value.day}
                          data={DAYS.map((d) => ({ value: d, label: d === "last" ? "Last day" : d }))}
                          onChange={(e) => set({ day: e.currentTarget.value })} />
          )}
          <TextInput label="Expected time" type="time" data-testid={`${idPrefix}-time`} value={value.time} w={130}
                     error={/^\d\d:\d\d/.test(value.time) ? undefined : "A time, like 07:00"}
                     onChange={(e) => set({ time: e.currentTarget.value })} />
          <TextInput label="Grace (minutes)" type="number" min={0} max={1440} data-testid={`${idPrefix}-grace`} value={value.grace} w={130}
                     error={graceBad ? "0 to 1440" : undefined} onChange={(e) => set({ grace: e.currentTarget.value })} />
          <TextInput label="Time zone" data-testid={`${idPrefix}-timezone`} value={value.timezone} w={170}
                     error={value.timezone.trim() === "" ? "A zone, like UTC or Europe/London" : undefined}
                     onChange={(e) => set({ timezone: e.currentTarget.value })} />
          <label style={{ alignSelf: "end", paddingBottom: 6 }}>
            <input type="checkbox" data-testid={`${idPrefix}-notify`} checked={value.notify}
                   onChange={(e) => set({ notify: e.currentTarget.checked })} /> Tell the owner when a delivery is missing
          </label>
        </div>
      )}
      {value.kind === "one_time" && <p className="muted">A one-time load has no expected time, so nothing is ever late or missing.</p>}
    </fieldset>
  );
}
