import { Alert, Button, Text } from "@mantine/core";
import { useState } from "react";
import { apiPost, describeError, type FeedScheduleT, type ScheduleAnswer, type User } from "../api";
import { formatWhen } from "../theme";
import ScheduleForm, { describeSchedule, scheduleBody, scheduleValid, valueOf, type ScheduleValue } from "./ScheduleForm";

// Phase 7.21: the schedule in force on the feed page, "Change schedule" (a new version; who and when are recorded), and every
// version. A feed made before the question existed shows "Schedule not set" and a button to set it.
export default function ScheduleSection({ feedId, schedule, schedules, me, devUser, onChanged }: {
  feedId: string; schedule: FeedScheduleT | null; schedules: FeedScheduleT[]; me: User | null; devUser: string; onChanged: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState<ScheduleValue>(valueOf(schedule));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const mayChange = !!me && me.roles.includes("backend_team");

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      const answer = await apiPost<ScheduleAnswer>(`/api/amaze/feeds/${feedId}/schedule`, scheduleBody(value), devUser);
      setNote(answer.outcome === "schedule_unchanged" ? "Nothing changed: this is the schedule already in force."
                                                      : `Saved as version ${answer.schedule.version}.`);
      setEditing(false);
      onChanged();
    } catch (err: unknown) {
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div data-testid="feed-schedule">
      {schedule ? (
        <Text data-testid="schedule-current">
          {describeSchedule(schedule)}
          {schedule.kind === "recurring" && (schedule.notify_owner ? "; the owner is told when a delivery is missing" : "; the owner is not told")}
        </Text>
      ) : (
        <Text className="muted" data-testid="schedule-not-set">Schedule not set</Text>
      )}
      {schedule && (
        <p className="muted" data-testid="schedule-who">
          Version {schedule.version}, set by {schedule.decided_by} on {formatWhen(schedule.created_at)}
        </p>
      )}
      {note && <Text role="status" data-testid="schedule-note">{note}</Text>}
      {mayChange && !editing && (
        <Button variant="default" size="xs" data-testid="schedule-change"
                onClick={() => { setValue(valueOf(schedule)); setError(null); setNote(null); setEditing(true); }}>
          {schedule ? "Change schedule" : "Set schedule"}
        </Button>
      )}
      {editing && (
        <>
          <ScheduleForm value={value} onChange={setValue} idPrefix="feed-schedule" />
          {error && <Alert color="red" variant="light" role="alert" data-testid="schedule-error" mb="xs">{error}</Alert>}
          <Button data-testid="schedule-save" disabled={!scheduleValid(value) || busy} onClick={save} mr="xs">Save schedule</Button>
          <Button variant="default" onClick={() => setEditing(false)}>Cancel</Button>
        </>
      )}
      {schedules.length > 1 && (
        <details style={{ marginTop: 8 }}>
          <summary>Earlier versions ({schedules.length - 1})</summary>
          <ul data-testid="schedule-versions" style={{ margin: 0, paddingLeft: 18 }}>
            {[...schedules].reverse().map((s) => (
              <li key={s.id}>
                Version {s.version}: {describeSchedule(s)}; {s.decided_by}, {formatWhen(s.created_at)}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
