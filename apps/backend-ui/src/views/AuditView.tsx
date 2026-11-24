import { Button, Group, NativeSelect, Table, Text, TextInput, Title } from "@mantine/core";
import { useEffect, useState } from "react";
import { apiGet, describeError, getAudit, getAuditCsv, words, type AuditRowT, type AuditViewT, type FeedRow } from "../api";
import { formatWhen } from "../theme";

// Phase 7.15: the monthly audit view. One row per decision of the month (who, what, who made the item, the code version in
// force, a waiver) and per rerun (who started it, why). Filter by feed and person; Download CSV is the same rows.

function months(): string[] {
  const now = new Date();
  return Array.from({ length: 13 }, (_, i) => {
    const d = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() - i, 1));
    return d.toISOString().slice(0, 7);
  });
}

function by(r: AuditRowT): string {
  return r.by === "agent" && r.agent ? `agent (${r.agent})` : r.by;
}

function what(r: AuditRowT): string {
  if (r.kind === "rerun") return `rerun of load ${(r.rerun_of ?? "").slice(0, 8)}`;
  return `${words(r.decision)} ${words(r.item)}${r.waiver ? " (waiver)" : ""}`;
}

export default function AuditView({ devUser }: { devUser: string }) {
  const [month, setMonth] = useState(months()[0]);
  const [feed, setFeed] = useState("");
  const [person, setPerson] = useState("");
  const [feeds, setFeeds] = useState<FeedRow[]>([]);
  const [view, setView] = useState<AuditViewT | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (devUser) apiGet<FeedRow[]>("/api/amaze/feeds", devUser).then(setFeeds).catch(() => setFeeds([]));
  }, [devUser]);
  useEffect(() => {
    if (!devUser) return;
    getAudit(month, feed, person.trim(), devUser).then((v) => { setView(v); setError(null); })
      .catch((e: unknown) => setError(describeError(e)));
  }, [devUser, month, feed, person]);

  async function download() {
    try {
      const text = await getAuditCsv(month, feed, person.trim(), devUser);
      const url = URL.createObjectURL(new Blob([text], { type: "text/csv;charset=utf-8" }));
      const link = document.createElement("a");
      link.href = url;
      link.download = `audit-${month}.csv`;
      link.click();
      URL.revokeObjectURL(url);
      setError(null);
    } catch (e: unknown) {
      setError(describeError(e));
    }
  }

  return (
    <div data-testid="audit">
      <Title order={2}>Audit</Title>
      <Text className="muted">Every decision of a month: who decided, what, who made the item (an agent, a person, rules or
        the system), the code version in force, and every rerun. Nothing here can be edited.</Text>
      {error && <Text role="alert" className="bad">{error}</Text>}
      <Group gap="sm" align="end" my="sm">
        <NativeSelect label="Month" value={month} onChange={(e) => setMonth(e.currentTarget.value)} data={months()} />
        <NativeSelect label="Feed" value={feed} onChange={(e) => setFeed(e.currentTarget.value)}
                      data={[{ value: "", label: "All feeds" }, ...feeds.map((f) => ({ value: f.id, label: f.code }))]} />
        <TextInput label="Person" placeholder="user id, e.g. dev-data-steward" value={person}
                   onChange={(e) => setPerson(e.currentTarget.value)} />
        <Button variant="default" onClick={() => void download()}>Download CSV</Button>
      </Group>
      {view && (
        <Text data-testid="audit-count">
          {view.count} {view.count === 1 ? "row" : "rows"}{view.truncated ? `; the first ${view.rows.length} are shown, the CSV has all` : ""}
        </Text>
      )}
      {view && view.rows.length === 0 && <Text className="muted">No decision in this month for these filters.</Text>}
      <Table.ScrollContainer minWidth={900}>
        <Table verticalSpacing="xs">
          <Table.Tbody>{(view?.rows ?? []).map((r, i) => (
            <Table.Tr key={`${r.proposal_id ?? r.load_id}${i}`} data-testid="audit-row">
              <Table.Td>{formatWhen(r.at)}</Table.Td>
              <Table.Td>{r.who ?? "not recorded"}{r.role ? <div className="muted">{words(r.role)}</div> : null}</Table.Td>
              <Table.Td>{what(r)}<div className="muted">{r.summary}{r.note ? ` (${r.note})` : ""}</div></Table.Td>
              <Table.Td>{r.feed_code ?? "-"}</Table.Td>
              <Table.Td>{by(r)}</Table.Td>
              <Table.Td>{r.code_version === null ? "-" : `version ${r.code_version}`}</Table.Td>
            </Table.Tr>))}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
    </div>
  );
}
