import { Button, Group, NativeSelect, Table, Text, TextInput, Title } from "@mantine/core";
import { useCallback, useEffect, useState } from "react";
import {
  addExceptionFix, decideExceptionGroups, describeError, getFeedExceptions, getLoadExceptions, proposeExceptionFixes,
  rerunHeldLoad, words,
  type ExceptionGroup, type GroupFix, type LoadExceptionCounts, type LoadExceptions, type RerunResult,
} from "../api";
import { formatWhen } from "../theme";

// Phase 7.14: a load's exceptions (a flagged row and one rule), grouped by the rule and the shape of the values it looks
// at (digits 9, capitals A, small letters a). Only a held load's groups take fixes: the ExceptionFixAgent proposes one
// per group (Propose fixes), or a data steward adds one by hand; people decide each (no data changes until they do).

const ACTION: Record<string, string> = { waive: "Waive", refer: "Refer to the provider", transformation: "Transformation" };
const ROLE: Record<string, string> = { data_steward: "the data steward", data_owner: "the data owner" };

function sample(values: Record<string, unknown>, elements: string[]): string {
  const keys = elements.length > 0 ? elements : Object.keys(values);
  return keys.map((k) => `${k}=${values[k] === null || values[k] === undefined ? "" : String(values[k])}`).join(", ");
}

function FixText({ fix }: { fix: GroupFix }) {
  const what = fix.action === "transformation"
    ? `Transformation ${fix.steps.map((s) => String(s.op)).join(", ")}` : `${ACTION[fix.action] ?? fix.action}: ${fix.reason}`;
  const by = fix.origin === "manual" ? `added by ${fix.author}` : "agent";
  const next = fix.status === "pending" ? ROLE[fix.required_approver_roles[fix.approvals]] : null;
  return (
    <span data-testid="exception-fix">
      {what} ({by})
      <div className="muted">{next ? `waits for ${next}` : words(fix.status)}</div>
    </span>
  );
}

// The rerun's typed result, in words (plan, 2.6).
function rerunWords(r: RerunResult): string {
  switch (r.outcome) {
    case "rerun_released": return `Rerun released: trust ${r.trust_score} (it was ${r.previous_score ?? "-"}); ${r.waived_rows ?? 0} rows waived.`;
    case "rerun_still_held": return `Rerun still held: trust ${r.trust_score} (it was ${r.previous_score ?? "-"}); ${r.open_groups ?? 0} groups still flag rows.`;
    case "rerun_needs_go_live": return `The rerun needs the data owner's go-live first. ${r.detail ?? ""}`;
    case "nothing_to_rerun": return r.reason === "not_held" ? "Only a held load is rerun." : "Nothing was resolved since the last rerun.";
    case "rerun_in_progress": return "Another load of this feed is running; try again when it has finished.";
    default: return `The rerun was not loaded (${r.status ?? r.outcome})${r.detail ? `: ${r.detail}` : ""}.`;
  }
}

function AddFix({ groups, onAdd }: { groups: ExceptionGroup[]; onAdd: (g: string, a: string, r: string) => Promise<void> }) {
  const open = groups.filter((g) => g.state === "open");
  const [groupKey, setGroupKey] = useState(open[0]?.group_key ?? "");
  const [action, setAction] = useState("waive");
  const [reason, setReason] = useState("");
  if (open.length === 0) return <Text className="muted">Every group has a fix.</Text>;
  return (
    <Group gap="sm" align="end" mt="xs" data-testid="add-fix">
      <NativeSelect label="Group" value={groupKey} onChange={(e) => setGroupKey(e.currentTarget.value)}
                    data={open.map((g) => ({ value: g.group_key, label: `${g.rule_key} ${g.pattern} (${g.exceptions})` }))} />
      <NativeSelect label="Fix" value={action} onChange={(e) => setAction(e.currentTarget.value)}
                    data={[{ value: "waive", label: "Waive (the data owner approves too)" },
                           { value: "refer", label: "Refer to the provider" }]} />
      <TextInput label="Reason" value={reason} onChange={(e) => setReason(e.currentTarget.value)} />
      <Button disabled={!reason.trim() || !groupKey} onClick={() => void onAdd(groupKey, action, reason.trim())}>Add fix</Button>
    </Group>
  );
}

export default function ExceptionsSection({ feedId, devUser, roles = [] }: { feedId: string; devUser: string; roles?: string[] }) {
  const [loads, setLoads] = useState<LoadExceptionCounts[] | null>(null);
  const [chosen, setChosen] = useState<string | null>(null);
  const [view, setView] = useState<LoadExceptions | null>(null);
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [adding, setAdding] = useState(false);
  const [picked, setPicked] = useState<string[]>([]);
  const [rejecting, setRejecting] = useState(false);
  const [note, setNote] = useState("");
  const [why, setWhy] = useState("");
  const [rerun, setRerun] = useState<RerunResult | null>(null);
  const steward = roles.includes("data_steward");
  const decider = steward || roles.includes("data_owner");

  useEffect(() => {
    getFeedExceptions(feedId, devUser).then((all) => {
      const flagged = all.filter((l) => l.exceptions > 0);
      setLoads(flagged);
      setChosen((c) => c ?? flagged.find((l) => l.status === "held")?.load_id ?? flagged[0]?.load_id ?? null);
      setError(null);
    }).catch((err: unknown) => setError(describeError(err)));
  }, [feedId, devUser]);

  const load = useCallback(() => {
    if (!chosen) return;
    getLoadExceptions(chosen, devUser, offset).then((v) => { setView(v); setError(null); })
      .catch((err: unknown) => setError(describeError(err)));
  }, [chosen, devUser, offset]);
  useEffect(() => { load(); }, [load]);

  async function act(work: () => Promise<LoadExceptions>, done: string) {
    setBusy(true);
    try {
      setView(await work());
      setMessage(done);
      setError(null);
    } catch (err: unknown) {
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  }

  if (loads === null || loads.length === 0) return error ? <Text role="alert" className="bad">{error}</Text> : null;
  return (
    <div data-testid="section-exceptions">
      <Title order={3} mt="xl" mb="xs">Exceptions</Title>
      {error && <Text role="alert" className="bad">{error}</Text>}
      {message && <Text data-testid="exceptions-message">{message}</Text>}
      <NativeSelect label="Load" data-testid="exceptions-load" value={chosen ?? ""}
                    onChange={(e) => { setChosen(e.currentTarget.value); setOffset(0); }}
                    data={loads.map((l) => ({ value: l.load_id,
                      label: `${formatWhen(l.received_at)}: ${words(l.status)}, ${l.exceptions} exceptions in ${l.flagged_rows} of ${l.rows} rows` }))} />
      {view && (
        <>
          <Text mt="xs" data-testid="exceptions-summary">
            {view.exceptions} exceptions in {view.flagged_rows} of {view.rows} rows, {view.total_groups} groups.{" "}
            {view.can_fix ? "The load is held: each group can take a fix." : "The load is not held: shown for information only."}
          </Text>
          {view.can_fix && steward && (
            <Group gap="sm" mt="xs">
              <Button disabled={busy} onClick={() => void act(async () => {
                const r = await proposeExceptionFixes(view.load_id, devUser);
                return r.exceptions;
              }, "The agent proposed its fixes. Nothing changes until people decide them.")}>Propose fixes</Button>
              <Button variant="default" disabled={busy} onClick={() => setAdding(!adding)}>Add a fix</Button>
            </Group>
          )}
          {view.can_fix && decider && (
            <Group gap="sm" mt="xs" data-testid="bulk-decision">
              <Button disabled={busy || picked.length === 0} onClick={() => void act(async () => {
                const r = await decideExceptionGroups(view.load_id, { group_keys: picked, decision: "approved" }, devUser);
                setPicked([]);
                return r.exceptions;
              }, `Approved for ${picked.length} group(s).`)}>Approve selected</Button>
              <Button variant="default" disabled={busy || picked.length === 0} onClick={() => setRejecting(!rejecting)}>
                Reject selected</Button>
              {rejecting && (
                <>
                  <TextInput aria-label="Why it is rejected" placeholder="Why" value={note}
                             onChange={(e) => setNote(e.currentTarget.value)} />
                  <Button disabled={busy || !note.trim()} onClick={() => void act(async () => {
                    const r = await decideExceptionGroups(view.load_id, { group_keys: picked, decision: "rejected", note: note.trim() }, devUser);
                    setPicked([]); setRejecting(false); setNote("");
                    return r.exceptions;
                  }, `Rejected for ${picked.length} group(s).`)}>Confirm rejection</Button>
                </>
              )}
            </Group>
          )}
          {view.can_fix && steward && (
            <Group gap="sm" align="end" mt="xs" data-testid="rerun">
              <TextInput label="Reason for the rerun" value={why} onChange={(e) => setWhy(e.currentTarget.value)} />
              <Button disabled={busy || !why.trim()} onClick={async () => {
                setBusy(true);
                try {
                  setRerun((await rerunHeldLoad(view.load_id, why.trim(), devUser)).result);
                  setError(null);
                  load();
                } catch (err: unknown) {
                  setError(describeError(err));
                } finally {
                  setBusy(false);
                }
              }}>Rerun this load</Button>
              <span className="muted">a new load of the same file, under the current configuration; this load stays held</span>
            </Group>
          )}
          {rerun && <Text mt="xs" data-testid="rerun-result">{rerunWords(rerun)}</Text>}
          {adding && view.can_fix && steward && (
            <AddFix groups={view.groups} onAdd={(group_key, action, reason) => act(async () => {
              const r = await addExceptionFix(view.load_id, { group_key, action, reason }, devUser);
              setAdding(false);
              return r.exceptions;
            }, "Your fix was added. It waits for its decision.")} />
          )}
          <Table.ScrollContainer minWidth={900}>
            <Table data-testid="exception-groups" verticalSpacing="xs">
              <Table.Thead>
                <Table.Tr><Table.Th></Table.Th><Table.Th>Rule</Table.Th><Table.Th>Pattern</Table.Th><Table.Th>Exceptions</Table.Th>
                  <Table.Th>Sample rows</Table.Th><Table.Th>State</Table.Th><Table.Th>Fix</Table.Th></Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {view.groups.map((g) => (
                  <Table.Tr key={g.group_key} data-testid={`exception-group-${g.group_key}`}>
                    <Table.Td>{view.can_fix && g.fix?.status === "pending" && (
                      <input type="checkbox" aria-label={`Select group ${g.group_key}`} checked={picked.includes(g.group_key)}
                             onChange={(e) => setPicked(e.currentTarget.checked ? [...picked, g.group_key]
                               : picked.filter((k) => k !== g.group_key))} />)}</Table.Td>
                    <Table.Td><code>{g.rule_key}</code>{g.description && <div className="muted">{g.description}</div>}</Table.Td>
                    <Table.Td><code>{g.pattern}</code>
                      {g.elements.length === 0 && <div className="muted">the rule names no column: one group</div>}</Table.Td>
                    <Table.Td>{g.exceptions}</Table.Td>
                    <Table.Td><ul style={{ margin: 0, paddingLeft: 16 }}>
                      {g.samples.map((s) => <li key={s.row_number}>row {s.row_number}: {sample(s.values, g.elements)}</li>)}
                    </ul></Table.Td>
                    <Table.Td>{words(g.state)}</Table.Td>
                    <Table.Td>{g.fix ? <FixText fix={g.fix} /> : <span className="muted">-</span>}</Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
          {view.total_groups > view.limit && (
            <Group gap="sm" mt="xs">
              <Button variant="default" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - view.limit))}>Previous</Button>
              <Button variant="default" disabled={offset + view.limit >= view.total_groups}
                      onClick={() => setOffset(offset + view.limit)}>Next</Button>
            </Group>
          )}
        </>
      )}
    </div>
  );
}
