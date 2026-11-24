import { Button, Group, Table, Text, Title } from "@mantine/core";
import { useState, type ReactNode } from "react";
import { StatusChip } from "../../../ui-shared/phase711";
import { apiPost, describeError, uploadFeedFile, words, type FeedPage, type LoadOnPage } from "../api";
import { formatWhen } from "../theme";

// Step 13 on a registered feed (phase 6 plan, section 10): the latest loads with their four stages and counts, and
// Load now for operations (the job's own fetch and load of this one feed; it needs a go-live).

function stageText(load: LoadOnPage): string {
  return load.stages.map((s) =>
    s.outcome === "stage_completed" ? `${s.stage} ${s.rows_in}→${s.rows_out}` : `${s.stage} FAILED`).join(" · ");
}

// Phase 7: what a person reads for a load's purpose, step 12's arrival (and why) and step 15's certification.
const PURPOSE: Record<string, string> = { go_live_test: "test load for go-live", scheduled: "scheduled load" };

// Phase 7.5: a load's status as a badge; its words stay the status's own (the story reads them).
function StatusBadge({ status }: { status: string }) {
  const good = status === "loaded" || status === "released";
  return <StatusChip tone={good ? "good" : status === "received" ? "neutral" : "attention"}>{words(status)}</StatusChip>;
}

function Arrival({ load }: { load: LoadOnPage }) {
  if (!load.arrival_outcome) return <span className="muted">{load.purpose === "scheduled" ? "not checked" : "-"}</span>;
  const d = load.arrival_detail ?? {};
  return (
    <>
      <span data-testid="arrival">{words(load.arrival_outcome)}</span>
      {d.missing && d.missing.length > 0 && <div>missing: <code>{d.missing.join(", ")}</code></div>}
      {d.added && d.added.length > 0 && (
        <div>{load.arrival_outcome === "additive_parked" ? "parked" : "not parked"}: <code>{d.added.join(", ")}</code></div>
      )}
      {load.arrival_outcome === "breaking_held" && d.reason && <div className="muted">{d.reason}</div>}
      {load.arrival_outcome === "resent_quarantined" && <div className="muted">the same file as an earlier delivery</div>}
    </>
  );
}

function Certification({ load }: { load: LoadOnPage }) {
  const c = load.certification;
  if (!c) return <span className="muted">-</span>;
  return (
    <span data-testid="certification" className={c.outcome === "released" ? "ok" : "bad"}>
      trust {c.trust_score} (threshold {c.threshold}): {words(c.outcome)}
      <div className="muted">{c.flagged_rows} of {c.rows} rows flagged
        {(c.waived_rows ?? 0) > 0 && `, ${c.waived_rows} more waived (counted as unflagged)`}</div>
    </span>
  );
}

export default function LoadsSection({ page, devUser, roles, onChange, onMessage, onError, notice }: {
  page: FeedPage; devUser: string; roles: string[]; onChange: () => void; onMessage: (m: string) => void;
  onError: (m: string | null) => void; notice?: ReactNode; // phase 7.5: the page's result line, when it is ours
}) {
  const [busy, setBusy] = useState(false);
  const canLoad = roles.includes("operations") && page.go_live.latest !== null;

  async function loadNow() {
    setBusy(true);
    try {
      const r = await apiPost<{ result: { fetch: string; load: string | null } }>(
        `/api/amaze/feeds/${page.feed.id}/loads`, {}, devUser);
      onError(null);
      onMessage(r.result.load ? `Loaded: ${words(r.result.load)}.` : `Not loaded: the fetch ${words(r.result.fetch)}.`);
      onChange();
    } catch (err: unknown) {
      onError(describeError(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div data-testid="section-loads">
      <Title order={3} mt="xl" mb="xs">Loads</Title>
      {notice}
      {page.loads.length === 0 ? <Text className="muted">No load yet.</Text> : (
        <>
          <Table.ScrollContainer minWidth={900}>
            <Table data-testid="loads-table" verticalSpacing="xs">
              <Table.Thead>
                <Table.Tr><Table.Th>When</Table.Th><Table.Th>Purpose</Table.Th><Table.Th>Arrival</Table.Th>
                  <Table.Th>Status</Table.Th><Table.Th>Certification</Table.Th><Table.Th>Stages (rows in→out)</Table.Th></Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {page.loads.map((l) => (
                  <Table.Tr key={l.id} data-testid="load-row" data-purpose={l.purpose}>
                    <Table.Td>{formatWhen(l.received_at)}</Table.Td>
                    <Table.Td>{PURPOSE[l.purpose] ?? words(l.purpose)}
                      {l.rerun_of_load_id && (
                        <div className="muted" data-testid="rerun-of">
                          Rerun of load {l.rerun_of_load_id.slice(0, 8)}: {l.rerun_reason}
                        </div>)}</Table.Td>
                    <Table.Td><Arrival load={l} /></Table.Td>
                    <Table.Td>
                      <StatusBadge status={l.status} />
                      {l.failure && <div className="bad">{l.failure}</div>}
                    </Table.Td>
                    <Table.Td><Certification load={l} /></Table.Td>
                    <Table.Td data-testid="stages">{stageText(l)}</Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
          <Text className="muted" size="xs" mt="xs" data-testid="loads-legend">
            Stages show rows in→out, in the order raw, curated, validated, standard. A held, quarantined or
            abandoned load has no stage: it never reached the engine.
          </Text>
        </>
      )}
      {canLoad ? (
        <Group gap="sm" mt="sm">
          <Button disabled={busy} data-testid="load-now" onClick={loadNow}>Load now</Button>
          <span className="muted">fetch the source and load it, as the daily job does</span>
          <label className="muted">A new file for this feed (an uploaded feed): {" "}
            <input type="file" data-testid="reupload-file" aria-label="New file for this feed" disabled={busy}
                   onChange={async (e) => {
                     const file = e.currentTarget.files?.[0];
                     if (!file) return;
                     setBusy(true);
                     try {
                       await uploadFeedFile(page.feed.id, file, devUser);
                       onError(null);
                       onMessage(`Stored ${file.name}. Press Load now to load it.`);
                     } catch (err: unknown) {
                       onError(describeError(err));
                     } finally {
                       setBusy(false);
                     }
                   }} />
          </label>
        </Group>
      ) : roles.includes("operations") ? <Text className="muted" mt="sm">Load now needs a go-live first.</Text>
        : <Text className="muted" mt="sm">Only operations loads a feed by hand.</Text>}
    </div>
  );
}
