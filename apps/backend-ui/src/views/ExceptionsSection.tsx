import { Button, Group, NativeSelect, Table, Text, Title } from "@mantine/core";
import { useCallback, useEffect, useState } from "react";
import {
  describeError, getFeedExceptions, getLoadExceptions, words, type LoadExceptionCounts, type LoadExceptions,
} from "../api";
import { formatWhen } from "../theme";

// Phase 7.14: a load's exceptions (a flagged row and one rule), grouped by the rule and the shape of the values it looks
// at (digits 9, capitals A, small letters a). T1: read only. Only a held load's groups take fixes.

function sample(values: Record<string, unknown>, elements: string[]): string {
  const keys = elements.length > 0 ? elements : Object.keys(values);
  return keys.map((k) => `${k}=${values[k] === null || values[k] === undefined ? "" : String(values[k])}`).join(", ");
}

export default function ExceptionsSection({ feedId, devUser }: { feedId: string; devUser: string }) {
  const [loads, setLoads] = useState<LoadExceptionCounts[] | null>(null);
  const [chosen, setChosen] = useState<string | null>(null);
  const [view, setView] = useState<LoadExceptions | null>(null);
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState<string | null>(null);

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

  if (loads === null || loads.length === 0) return error ? <Text role="alert" className="bad">{error}</Text> : null;
  return (
    <div data-testid="section-exceptions">
      <Title order={3} mt="xl" mb="xs">Exceptions</Title>
      {error && <Text role="alert" className="bad">{error}</Text>}
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
          <Table.ScrollContainer minWidth={800}>
            <Table data-testid="exception-groups" verticalSpacing="xs">
              <Table.Thead>
                <Table.Tr><Table.Th>Rule</Table.Th><Table.Th>Pattern</Table.Th><Table.Th>Exceptions</Table.Th>
                  <Table.Th>Sample rows</Table.Th><Table.Th>State</Table.Th></Table.Tr>
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
