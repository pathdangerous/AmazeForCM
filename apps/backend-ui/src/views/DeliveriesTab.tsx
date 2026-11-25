import { NativeSelect, Table, Text, Title } from "@mantine/core";
import { useEffect, useState } from "react";
import { LoadingSkeleton } from "../../../ui-shared/phase711";
import {
  apiGet, describeError, type DeliveriesAnswer, type DeliveriesShown, type DeliveryRowT, type DeliveryStatus,
} from "../api";
import { describeSchedule } from "./ScheduleForm";

// Phase 7.21, T4: a feed's Deliveries tab. Expected against actual arrivals, from the one table the API returns: the headline
// figures, a calendar with one cell per expected delivery (a fill AND the status in words), when each arrived against its
// window, and the rows of each delivery. No chart library: SVG and CSS grid. Every colour is a token pair of theme.ts.
export const STATUS_WORDS: Record<DeliveryStatus, string> = {
  on_time: "On time", late: "Late", missing: "Missing", resent_quarantined: "Resent, quarantined", held: "Held",
  awaited: "Awaited",
};
const VAR: Record<DeliveryStatus, string> = {
  on_time: "on-time", late: "late", missing: "missing", resent_quarantined: "quarantined", held: "held", awaited: "awaited",
};
const fillOf = (s: DeliveryStatus) => ({ background: `var(--amz-dlv-${VAR[s]})`, color: `var(--amz-dlv-${VAR[s]}-text)` });
const ORDER: DeliveryStatus[] = ["on_time", "late", "missing", "held", "resent_quarantined", "awaited"];
const WEEK = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

const day = (iso: string) => iso.slice(0, 10);
const hhmm = (iso: string) => `${iso.slice(11, 16)} UTC`;
export function cellWord(r: DeliveryRowT): string {
  return r.status === "late" || (r.status === "held" && r.timing === "late") ? `${STATUS_WORDS[r.status]}${r.late_minutes != null ? ` ${r.late_minutes} min` : ""}`
    : STATUS_WORDS[r.status];
}
const pct = (v: number | null) => (v === null ? "none due yet" : `${v}%`);

function Tile({ label, value, testid }: { label: string; value: string; testid: string }) {
  return (
    <div data-testid={testid} style={{ border: "1px solid var(--amz-line)", background: "var(--amz-panel)", padding: "6px 12px", minWidth: 130 }}>
      <div className="muted" style={{ fontSize: 12 }}>{label}</div>
      <div style={{ fontSize: 20, fontWeight: 700 }}>{value}</div>
    </div>
  );
}

function Calendar({ rows }: { rows: DeliveryRowT[] }) {
  if (rows.length === 0) return null;
  const byDay = new Map(rows.map((r) => [day(r.due_at), r]));
  const first = new Date(`${day(rows[0].due_at)}T00:00:00Z`);
  const last = new Date(`${day(rows[rows.length - 1].due_at)}T00:00:00Z`);
  const lead = (first.getUTCDay() + 6) % 7; // Monday first
  const cells: (string | null)[] = Array.from({ length: lead }, () => null);
  for (let d = new Date(first); d <= last; d = new Date(d.getTime() + 86_400_000)) cells.push(d.toISOString().slice(0, 10));
  return (
    <div data-testid="dlv-calendar" role="group" aria-label="Expected deliveries by day">
      <div style={{ display: "grid", gridTemplateColumns: "repeat(7, minmax(72px, 1fr))", gap: 4, maxWidth: 720 }}>
        {WEEK.map((w) => <div key={w} className="muted" style={{ fontSize: 12, textAlign: "center" }}>{w}</div>)}
        {cells.map((d, i) => {
          const r = d ? byDay.get(d) : undefined;
          if (!d) return <div key={`pad-${i}`} aria-hidden="true" />;
          if (!r) return (
            <div key={d} aria-hidden="true" style={{ minHeight: 44, border: "1px dashed var(--amz-line)", fontSize: 11, padding: 2 }}
                 className="muted">{d.slice(8)}</div>
          );
          const word = cellWord(r);
          return (
            <div key={d} data-testid="dlv-cell" data-status={r.status} data-day={d} role="group"
                 aria-label={`${d}, expected ${hhmm(r.due_at)}: ${word}`}
                 title={`${d} ${hhmm(r.due_at)} expected${r.received_at ? `, arrived ${hhmm(r.received_at)}` : ""}`}
                 style={{ minHeight: 44, padding: 2, fontSize: 12, border: "1px solid var(--amz-line)", ...fillOf(r.status) }}>
              <div style={{ fontSize: 11 }}>{d.slice(8)}</div>
              <div style={{ fontWeight: 600 }}>{word}</div>
            </div>
          );
        })}
      </div>
      <ul data-testid="dlv-legend" style={{ display: "flex", flexWrap: "wrap", gap: 8, listStyle: "none", padding: 0, margin: "8px 0" }}>
        {ORDER.map((s) => (
          <li key={s} style={{ padding: "2px 8px", fontSize: 12, border: "1px solid var(--amz-line)", ...fillOf(s) }}>{STATUS_WORDS[s]}</li>
        ))}
      </ul>
    </div>
  );
}

// When each delivery arrived against its window: x is the delivery, y the minutes after its expected time, the band is
// the grace window. A point is a circle (a square when it was held): shape and words carry the status as well as the fill.
function ArrivalChart({ rows }: { rows: DeliveryRowT[] }) {
  const arrived = rows.filter((r) => r.minutes_from_due !== null);
  if (arrived.length === 0) return <Text className="muted">Nothing has arrived yet.</Text>;
  const W = 640, H = 170, L = 44, B = 18, T = 8;
  const values = arrived.map((r) => r.minutes_from_due as number);
  const lo = Math.min(0, ...values), hi = Math.max(...arrived.map((r) => Math.max(r.grace_minutes, r.minutes_from_due as number)));
  const span = Math.max(hi - lo, 1);
  const y = (m: number) => T + (H - T - B) * (1 - (m - lo) / span);
  const x = (i: number) => L + ((W - L - 8) * (i + 0.5)) / rows.length;
  return (
    <svg data-testid="dlv-arrival-chart" role="img" viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", maxWidth: 720 }}
         aria-label={`Arrival time against the expected window for ${arrived.length} deliveries; the table below has every value`}>
      <line x1={L} x2={W - 8} y1={y(0)} y2={y(0)} stroke="var(--amz-text)" strokeWidth="1" />
      <text x={4} y={y(0) + 4} fontSize="10" fill="var(--amz-text)">due</text>
      <text x={4} y={T + 8} fontSize="10" fill="var(--amz-text)">{Math.round(hi)} min</text>
      <text x={4} y={H - B} fontSize="10" fill="var(--amz-text)">{Math.round(lo)} min</text>
      {rows.map((r, i) => r.minutes_from_due === null ? null : (
        <g key={r.due_at}>
          <rect x={x(i) - 5} width={10} y={y(r.grace_minutes)} height={Math.max(y(0) - y(r.grace_minutes), 0)}
                fill="var(--amz-dlv-on-time)" opacity={0.6} />
          {r.status === "held"
            ? <rect data-testid="dlv-point" data-status={r.status} x={x(i) - 4} y={y(r.minutes_from_due) - 4} width={8} height={8}
                    fill="var(--amz-text)"><title>{`${day(r.due_at)}: ${cellWord(r)}, ${r.minutes_from_due} min from due`}</title></rect>
            : <circle data-testid="dlv-point" data-status={r.status} cx={x(i)} cy={y(r.minutes_from_due)} r={4}
                      fill="var(--amz-text)"><title>{`${day(r.due_at)}: ${cellWord(r)}, ${r.minutes_from_due} min from due`}</title></circle>}
        </g>
      ))}
    </svg>
  );
}

function RowsChart({ rows }: { rows: DeliveryRowT[] }) {
  const counted = rows.filter((r) => r.rows !== null);
  if (counted.length === 0) return <Text className="muted">No delivery has been certified yet, so there are no row counts.</Text>;
  const W = 640, H = 140, L = 44, B = 18, T = 14;
  const top = Math.max(...counted.map((r) => r.rows as number), 1);
  const slot = (W - L - 8) / rows.length;
  return (
    <svg data-testid="dlv-rows-chart" role="img" viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", maxWidth: 720 }}
         aria-label={`Rows in each delivery over time, up to ${top}; the table below has every value`}>
      <line x1={L} x2={W - 8} y1={H - B} y2={H - B} stroke="var(--amz-text)" strokeWidth="1" />
      <text x={4} y={T + 4} fontSize="10" fill="var(--amz-text)">{top}</text>
      <text x={4} y={H - B} fontSize="10" fill="var(--amz-text)">0</text>
      {rows.map((r, i) => r.rows === null ? null : (
        <rect key={r.due_at} data-testid="dlv-bar" x={L + slot * i + 1} width={Math.max(slot - 2, 1)}
              y={H - B - ((H - B - T) * r.rows) / top} height={((H - B - T) * r.rows) / top} fill="var(--amz-marker)"
              stroke="var(--amz-text)" strokeWidth="0.5"><title>{`${day(r.due_at)}: ${r.rows} rows`}</title></rect>
      ))}
    </svg>
  );
}

function DeliveryTable({ rows }: { rows: DeliveryRowT[] }) {
  return (
    <Table.ScrollContainer minWidth={640}>
      <Table data-testid="dlv-table" verticalSpacing={4}>
        <Table.Thead>
          <Table.Tr><Table.Th>Expected</Table.Th><Table.Th>Arrived</Table.Th><Table.Th>Minutes from expected</Table.Th>
            <Table.Th>Status</Table.Th><Table.Th>Result</Table.Th><Table.Th>Rows</Table.Th><Table.Th>Schedule</Table.Th></Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {[...rows].reverse().map((r) => (
            <Table.Tr key={r.due_at} data-testid="dlv-row" data-status={r.status}>
              <Table.Td>{day(r.due_at)} {hhmm(r.due_at)}</Table.Td>
              <Table.Td>{r.received_at ? `${day(r.received_at)} ${hhmm(r.received_at)}` : <span className="muted">none</span>}</Table.Td>
              <Table.Td>{r.minutes_from_due ?? ""}</Table.Td>
              <Table.Td><span style={{ ...fillOf(r.status), padding: "1px 6px" }}>{cellWord(r)}</span></Table.Td>
              <Table.Td>{r.result ?? ""}</Table.Td>
              <Table.Td>{r.rows ?? ""}</Table.Td>
              <Table.Td>v{r.schedule_version}</Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  );
}

function Recurring({ shown }: { shown: DeliveriesShown }) {
  const f = shown.figures;
  return (
    <>
      <Text data-testid="dlv-schedule" mb="xs">{describeSchedule(shown.schedule)}</Text>
      {shown.live_since === null && <Text className="muted" data-testid="dlv-not-live">
        This feed has not loaded yet, so no delivery is expected.</Text>}
      <div style={{ display: "flex", flexWrap: "wrap", gap: 8, margin: "8px 0" }}>
        <Tile testid="dlv-on-time-pct" label="On time" value={pct(f.on_time_pct)} />
        <Tile testid="dlv-late" label="Late" value={String(f.late)} />
        <Tile testid="dlv-missing" label="Missing" value={String(f.missing)} />
        <Tile testid="dlv-mean-late" label="Mean lateness (late ones)" value={f.mean_late_minutes === null ? "none" : `${f.mean_late_minutes} min`} />
        <Tile testid="dlv-streak" label="On-time streak" value={String(f.streak)} />
        <Tile testid="dlv-next" label="Next expected" value={f.next_expected ? `${day(f.next_expected)} ${hhmm(f.next_expected)}` : "none"} />
      </div>
      {shown.rows.length > 0 && (
        <>
          <Title order={4} mt="md" mb="xs">Calendar</Title>
          <Calendar rows={shown.rows} />
          <Title order={4} mt="md" mb="xs">Arrival against the expected window</Title>
          <ArrivalChart rows={shown.rows} />
          <Title order={4} mt="md" mb="xs">Rows in each delivery</Title>
          <RowsChart rows={shown.rows} />
          <Title order={4} mt="md" mb="xs">Every expected delivery</Title>
          <DeliveryTable rows={shown.rows} />
        </>
      )}
      {shown.unexpected.length > 0 && (
        <>
          <Title order={4} mt="md" mb="xs">Unexpected deliveries ({shown.unexpected.length})</Title>
          <ul data-testid="dlv-unexpected" style={{ margin: 0, paddingLeft: 18 }}>
            {shown.unexpected.map((u) => (
              <li key={u.load_id}>
                {day(u.received_at)} {hhmm(u.received_at)}: {u.reason === "no_delivery_was_due" ? "no delivery was due then"
                  : "a second delivery for one that was already received"} ({u.status})
              </li>
            ))}
          </ul>
        </>
      )}
    </>
  );
}

export default function DeliveriesTab({ feedId, devUser, changed, onOpenSchedule }: {
  feedId: string; devUser: string; changed: number; onOpenSchedule: () => void;
}) {
  const [windowDays, setWindowDays] = useState<"30" | "90" | "all">("30");
  const [answer, setAnswer] = useState<DeliveriesAnswer | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!devUser) return;
    let current = true;
    apiGet<DeliveriesAnswer>(`/api/amaze/feeds/${feedId}/deliveries?window=${windowDays}`, devUser)
      .then((a) => current && (setAnswer(a), setError(null)))
      .catch((err: unknown) => current && setError(describeError(err)));
    return () => { current = false; };
  }, [feedId, devUser, windowDays, changed]);

  return (
    <section data-testid="deliveries-tab" aria-label="Deliveries">
      {error && <Text className="bad" role="alert">{error}</Text>}
      {!answer && !error && <LoadingSkeleton label="Loading deliveries…" />}
      {answer?.outcome === "schedule_not_set" && (
        <div data-testid="dlv-not-set">
          <Text className="muted">Schedule not set: nothing can be expected, so nothing can be late or missing.</Text>
          <button type="button" onClick={onOpenSchedule} data-testid="dlv-open-schedule">Set the schedule on the Overview tab</button>
        </div>
      )}
      {answer?.outcome === "one_time_shown" && (
        <div data-testid="dlv-one-time">
          <Text fw={700} data-testid="dlv-one-time-label">{answer.label}</Text>
          <Text className="muted">A one-time load has no expected time, so nothing is late or missing.</Text>
          {answer.loads.length === 0 ? <Text className="muted">Nothing has loaded yet.</Text> : (
            <Table data-testid="dlv-one-time-loads" verticalSpacing={4} mt="xs">
              <Table.Thead><Table.Tr><Table.Th>Received</Table.Th><Table.Th>Purpose</Table.Th><Table.Th>Status</Table.Th><Table.Th>Rows</Table.Th></Table.Tr></Table.Thead>
              <Table.Tbody>
                {[...answer.loads].reverse().map((l) => (
                  <Table.Tr key={l.load_id}>
                    <Table.Td>{day(l.received_at)} {hhmm(l.received_at)}</Table.Td><Table.Td>{l.purpose.replaceAll("_", " ")}</Table.Td>
                    <Table.Td>{l.status.replaceAll("_", " ")}</Table.Td><Table.Td>{l.rows ?? ""}</Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          )}
        </div>
      )}
      {answer?.outcome === "deliveries_shown" && (
        <>
          <NativeSelect label="Window" data-testid="dlv-window" value={windowDays} w={220}
                        data={[{ value: "30", label: "Last 30 days" }, { value: "90", label: "Last 90 days" },
                               { value: "all", label: "Since the feed began loading" }]}
                        onChange={(e) => setWindowDays(e.currentTarget.value as "30" | "90" | "all")} />
          <Recurring shown={answer} />
        </>
      )}
    </section>
  );
}
