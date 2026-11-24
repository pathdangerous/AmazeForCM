import { Button, Group, Table, Text, Title } from "@mantine/core";
import { useCallback, useEffect, useState } from "react";
import {
  describeError, getReport, getReportPreview, getReports, makeReport, words, type ReportNumbers, type ReportPattern,
  type ReportRowT, type ReportT,
} from "../api";
import { formatWhen } from "../theme";

// Phase 7.15: the reports of a data owner or a data steward. The numbers are counted by code from the tables; the narrative
// (the ReportSummariserAgent, logged) is shown apart and never changes one: a narrative that stated a figure the report does
// not hold was refused, and its report is shown without it. Every report is a version; a new version is a new row.

const KIND: Record<string, string> = { daily: "Daily", monthly: "Monthly" };

function when(p: ReportPattern): string {
  const where = p.dimension === "weekday" ? `every ${p.value}` : p.dimension === "day_of_month"
    ? `on day ${p.value} of the month` : `for the provider date ${p.value}`;
  return `${where}: ${p.failing_loads} of ${p.loads_in_window} loads`;
}

function yesterday(): string {
  const d = new Date();
  d.setUTCDate(d.getUTCDate() - 1);
  return d.toISOString().slice(0, 10);
}

function Totals({ t }: { t: ReportNumbers["totals"] }) {
  const rows: [string, string | number][] = [
    ["Feeds", t.feeds], ["Loads", t.loads], ["Released", t.released], ["Held", t.held], ["Failed", t.failed],
    ["Quarantined", t.quarantined], ["Rows", t.rows], ["Flagged rows", t.flagged_rows], ["Waived rows", t.waived_rows],
    ["Average trust score", t.average_trust_score ?? "none"],
  ];
  return (
    <Table data-testid="report-totals" verticalSpacing="xs" withRowBorders>
      <Table.Tbody>{rows.map(([k, v]) => <Table.Tr key={k}><Table.Th scope="row">{k}</Table.Th><Table.Td>{v}</Table.Td></Table.Tr>)}</Table.Tbody>
    </Table>
  );
}

// The figures of a report or of a preview: the same table, feeds, patterns and referred groups.
function NumbersBody({ n }: { n: ReportNumbers }) {
  return (
    <>
      <Title order={4}>Numbers</Title>
      <Totals t={n.totals} />
      <Title order={4} mt="md">Feeds</Title>
      <Table.ScrollContainer minWidth={800}>
        <Table data-testid="report-feeds" verticalSpacing="xs">
          <Table.Thead><Table.Tr><Table.Th>Feed</Table.Th><Table.Th>Loads</Table.Th><Table.Th>Held</Table.Th>
            <Table.Th>Trust</Table.Th><Table.Th>Open groups</Table.Th><Table.Th>Waivers</Table.Th><Table.Th>Reruns</Table.Th>
            <Table.Th>Drift</Table.Th><Table.Th>Patterns</Table.Th></Table.Tr></Table.Thead>
          <Table.Tbody>{n.feeds.map((f) => (
            <Table.Tr key={f.feed_id}>
              <Table.Td><a href={`#/feed/${f.feed_id}`}>{f.feed_code}</a></Table.Td><Table.Td>{f.loads}</Table.Td><Table.Td>{f.held}</Table.Td>
              <Table.Td>{f.average_trust_score ?? "-"}</Table.Td><Table.Td>{f.open_now}</Table.Td><Table.Td>{f.waivers}</Table.Td>
              <Table.Td>{f.reruns_released} released, {f.reruns_still_held} held</Table.Td><Table.Td>{f.drift_events}</Table.Td>
              <Table.Td>{f.patterns}</Table.Td>
            </Table.Tr>))}</Table.Tbody>
        </Table>
      </Table.ScrollContainer>
      <Text mt="sm" data-testid="report-exceptions">
        Exception groups: {n.exceptions.open_now} open, {n.exceptions.waiting_for_owner} waiting for the data owner;
        in the period {n.exceptions.resolved_by_agent} resolved by agent proposals, {n.exceptions.resolved_by_hand} by hand.
        Waivers {n.waivers}; reruns {n.reruns.released} released, {n.reruns.still_held} still held; drift events {n.drift_events}.
      </Text>
      {n.decisions && (
        <Text data-testid="report-decisions">
          Your decisions: {n.decisions.approved} approved, {n.decisions.rejected} rejected
          ({Object.entries(n.decisions.by_kind).map(([k, v]) => `${words(k)} ${v}`).join(", ") || "none approved"}).
        </Text>
      )}
      <Title order={4} mt="md">Recurring failure patterns</Title>
      {n.patterns.length === 0 ? <Text className="muted">None in this window.</Text> : (
        <ul data-testid="report-patterns">{n.patterns.map((p) => (
          <li key={`${p.feed_id}${p.rule_key}${p.dimension}${p.value}`}>
            <code>{p.rule_key}</code> in {p.feed_code}: {when(p)}
            <div className="muted">{p.evidence.map((e) => e.received_on).join(", ")}</div>
          </li>))}
        </ul>
      )}
      <Title order={4} mt="md">Fails on every load</Title>
      {(n.always_failing ?? []).length === 0 ? <Text className="muted" data-testid="report-always-none">None in this window.</Text> : (
        <ul data-testid="report-always-failing">{(n.always_failing ?? []).map((a) => (
          <li key={`${a.feed_id}${a.rule_key}`}>
            <code>{a.rule_key}</code> in {a.feed_code} fails on every load: {a.failing_loads} loads, {a.first_on} to {a.last_on}.
            <div className="muted">A standing defect of the data or the rule, not a pattern of a day.</div>
          </li>))}
        </ul>
      )}
      <Title order={4} mt="md">Referred to the provider</Title>
      {n.referred_groups.length === 0 ? <Text className="muted">None open.</Text> : (
        <ul>{n.referred_groups.map((g) => (
          <li key={`${g.feed_id}${g.rule_key}${g.pattern}`} data-testid="report-referred">
            <code>{g.rule_key}</code> ({g.pattern}) in {g.feed_code}: {g.reason} ({g.decided_by})
          </li>))}
        </ul>
      )}
    </>
  );
}

function ReportDetail({ id, devUser, roles }: { id: string; devUser: string; roles: string[] }) {
  const [report, setReport] = useState<ReportT | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [made, setMade] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    getReport(id, devUser).then((r) => { setReport(r); setError(null); }).catch((e: unknown) => setError(describeError(e)));
  }, [id, devUser]);
  if (!report) return error ? <Text role="alert" className="bad">{error}</Text> : null;
  const n = report.numbers;
  const mine = report.subject_id === devUser || roles.includes("operations");

  async function again() {
    setBusy(true);
    try {
      const r = await makeReport({ kind: report!.kind, period_start: report!.period_start, role: report!.subject_role,
        subject_id: report!.subject_id }, devUser);
      setMade(`A new version was made: version ${r.result.version}.`);
      setError(null);
    } catch (e: unknown) {
      setError(describeError(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div data-testid="report">
      <a href="#/reports">All reports</a>
      <Title order={2} mt="xs">{`${KIND[report.kind]} report ${report.period_start}${report.kind === "monthly" ? " (month)" : ""}`}</Title>
      <Text className="muted">
        For {report.subject_id} ({words(report.subject_role)}) · version {report.version} · made {formatWhen(report.created_at)} by {report.generated_by}
      </Text>
      {error && <Text role="alert" className="bad">{error}</Text>}
      {made && <Text data-testid="report-made">{made}</Text>}
      <div data-testid="report-narrative" style={{ margin: "12px 0" }}>
        <Title order={4}>Narrative</Title>
        <Text className="muted" size="sm">
          Written by the ReportSummariserAgent, separate from the numbers below. It changes none of them.
        </Text>
        {report.narrative_status === "written" && <Text>{report.narrative}</Text>}
        {report.narrative_status === "refused" && <Text className="bad">{report.narrative_note}</Text>}
        {report.narrative_status === "none" && <Text className="muted">No narrative for this version.</Text>}
      </div>
      <NumbersBody n={n} />
      <Text className="muted" size="sm" mt="md">Numbers fingerprint {report.numbers_sha256.slice(0, 12)} (set by the database).</Text>
      {mine && <Button mt="sm" disabled={busy} onClick={() => void again()}>New version</Button>}
    </div>
  );
}

export default function ReportsView({ devUser, roles, reportId }: { devUser: string; roles: string[]; reportId: string | null }) {
  const [rows, setRows] = useState<ReportRowT[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [preview, setPreview] = useState<ReportNumbers | null>(null);
  const load = useCallback(() => {
    if (!devUser) return;
    getReports(devUser).then((r) => { setRows(r); setError(null); }).catch((e: unknown) => setError(describeError(e)));
  }, [devUser]);
  useEffect(() => { load(); }, [load]);
  if (reportId) return <ReportDetail id={reportId} devUser={devUser} roles={roles} />;

  async function show(role: "data_owner" | "data_steward") {
    try {
      setPreview(await getReportPreview(role, devUser));
      setError(null);
    } catch (e: unknown) {
      setError(describeError(e));
    }
  }

  async function make(role: "data_owner" | "data_steward") {
    try {
      const r = await makeReport({ kind: "daily", period_start: yesterday(), role }, devUser);
      setMessage(`Yesterday's report was made: version ${r.result.version}.`);
      load();
    } catch (e: unknown) {
      setError(describeError(e));
    }
  }

  return (
    <div data-testid="reports">
      <Title order={2}>Reports</Title>
      <Text className="muted">
        A daily and a monthly report for each data owner and data steward, made every morning. Numbers are counted by code; a
        narrative may sit beside them.
      </Text>
      {error && <Text role="alert" className="bad">{error}</Text>}
      {message && <Text>{message}</Text>}
      <Group gap="sm" my="sm">
        {roles.includes("data_owner") && <Button variant="default" onClick={() => void make("data_owner")}>Make yesterday's report (data owner)</Button>}
        {roles.includes("data_steward") && <Button variant="default" onClick={() => void make("data_steward")}>Make yesterday's report (data steward)</Button>}
      </Group>
      <Group gap="sm" mb="sm">
        {roles.includes("data_owner") && <Button variant="default" onClick={() => void show("data_owner")}>Preview today so far (data owner)</Button>}
        {roles.includes("data_steward") && <Button variant="default" onClick={() => void show("data_steward")}>Preview today so far (data steward)</Button>}
      </Group>
      {preview && (
        <div data-testid="report-preview" style={{ margin: "12px 0" }}>
          <Title order={3}>Preview of {preview.period.start} so far</Title>
          <Text className="muted" size="sm">
            Preview: not stored. Counted like a report from the tables right now; no report, notice or narrative is made.
            Stored reports are of past periods.
          </Text>
          <NumbersBody n={preview} />
        </div>
      )}
      {rows !== null && rows.length === 0 && <Text className="muted">No report yet.</Text>}
      <Table.ScrollContainer minWidth={600}>
        <Table verticalSpacing="xs">
          <Table.Tbody>{(rows ?? []).map((r) => (
            <Table.Tr key={r.id} data-testid="report-row">
              <Table.Td><a href={`#/reports/${r.id}`}>{r.period_start}</a></Table.Td>
              <Table.Td>{KIND[r.kind]}</Table.Td>
              <Table.Td>{words(r.subject_role)}</Table.Td>
              <Table.Td>version {r.version}</Table.Td>
              <Table.Td>{r.narrative_status === "written" ? "with narrative" : r.narrative_status === "refused" ? "narrative refused" : "no narrative"}</Table.Td>
              <Table.Td>{r.generated_by}</Table.Td>
            </Table.Tr>))}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
    </div>
  );
}
