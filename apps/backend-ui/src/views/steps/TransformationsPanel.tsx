import { Alert, Button, NativeSelect, Text, TextInput, Title } from "@mantine/core";
import { useState } from "react";
import type { KeyPolicy } from "../../api";
import ItemCard from "./ItemCard";
import { asList, asRecord, asText, type PanelProps } from "./shared";

function stepText(raw: unknown): string {
  const s = asRecord(raw);
  const op = asText(s.op).replaceAll("_", " ");
  const arg = asList(s.values).join(", ") || asText(s.format) || asText(s.type) || asText(s.to);
  return arg ? `${op}: ${arg}` : op;
}

function KeyPolicyForm({ policy, fallbackColumns, error, onSave }: {
  policy: KeyPolicy | null; fallbackColumns: string[]; error: string | null; onSave: (policy: KeyPolicy) => void;
}) {
  const [name, setName] = useState<KeyPolicy["policy"]>(policy?.policy ?? "hold");
  const [columns, setColumns] = useState((policy?.key_columns ?? fallbackColumns).join(", "));
  const [reason, setReason] = useState(policy?.reason ?? "");
  const [problems, setProblems] = useState<{ columns?: string; reason?: string }>({});

  const save = () => {
    const list = columns.split(",").map((c) => c.trim()).filter((c) => c !== "");
    const found: { columns?: string; reason?: string } = {};
    if (list.length === 0) found.columns = "Name at least one key column";
    else if (list.length > 5) found.columns = "Name at most 5 key columns";
    else if (new Set(list).size !== list.length) found.columns = "Each key column once";
    if (reason.trim() === "") found.reason = "A reason is needed";
    setProblems(found);
    if (!found.columns && !found.reason) onSave({ policy: name, key_columns: list, reason: reason.trim() });
  };
  return (
    <div>
      {error && <Alert role="alert" color="red" mb="xs">{error}</Alert>}
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "flex-start" }}>
        <NativeSelect label="Duplicate-key policy" value={name} w={200} onChange={(e) => setName(e.currentTarget.value as KeyPolicy["policy"])}
                      data={[{ value: "hold", label: "Hold the file" }, { value: "keep_last", label: "Keep the last row" },
                             { value: "flag", label: "Flag the rows" }]} />
        <TextInput label="Key columns" description="Comma-separated" value={columns} w={260} error={problems.columns}
                   onChange={(e) => setColumns(e.currentTarget.value)} />
        <TextInput label="Reason" value={reason} w={280} error={problems.reason} onChange={(e) => setReason(e.currentTarget.value)} />
      </div>
      <Button size="xs" mt="xs" onClick={save}>Save key policy</Button>
    </div>
  );
}

// The transformations step (task 7.3b): per column the ordered steps; below them the duplicate-key policy.
export default function TransformationsPanel({ ws, items, actions, onKeyPolicy, keyPolicyError = null }: PanelProps & {
  onKeyPolicy?: (policy: KeyPolicy) => void; keyPolicyError?: string | null;
}) {
  const policy = ws.key_policy;
  return (
    <div>
      {items.length === 0 && <Text>No columns need cleaning.</Text>}
      <ul style={{ padding: 0, margin: 0 }}>
        {items.map((item) => {
          const steps = Array.isArray(item.payload.steps) ? item.payload.steps : [];
          return (
            <ItemCard key={item.proposal_id} item={item} actions={actions}>
              {steps.length === 0 ? <Text mt="xs">No steps.</Text> : (
                <ol style={{ padding: 0, margin: "4px 0" }}>
                  {steps.map((s, i) => <li key={i} style={{ listStyle: "none" }}>{`${i + 1}. ${stepText(s)}`}</li>)}
                </ol>
              )}
            </ItemCard>
          );
        })}
      </ul>
      <Title order={4} size="h5" mt="md" mb="xs">Duplicate keys</Title>
      {actions.readOnly || !onKeyPolicy ? (
        <Text>{policy
          ? `${policy.policy.replaceAll("_", " ")} on ${policy.key_columns.join(", ")}. ${policy.reason}`
          : "No duplicate-key policy was set."}</Text>
      ) : (
        <KeyPolicyForm policy={policy} fallbackColumns={ws.inputs?.key_columns ?? []} error={keyPolicyError} onSave={onKeyPolicy} />
      )}
    </div>
  );
}
