import { Button, Group, NativeSelect, Table, Text, TextInput, Title } from "@mantine/core";
import { useCallback, useEffect, useState } from "react";
import {
  addExceptionFix, describeError, getFeedExceptions, getLoadExceptions, proposeExceptionFixes, words,
  type ExceptionGroup, type GroupFix, type LoadExceptionCounts, type LoadExceptions,
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
  const steward = roles.includes("data_steward");

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
                <Table.Tr><Table.Th>Rule</Table.Th><Table.Th>Pattern</Table.Th><Table.Th>Exceptions</Table.Th>
                  <Table.Th>Sample rows</Table.Th><Table.Th>State</Table.Th><Table.Th>Fix</Table.Th></Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {view.groups.map((g) => (
                  <Table.Tr key={g.group_key} data-testid={`exception-group-${g.group_key}`}>
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
