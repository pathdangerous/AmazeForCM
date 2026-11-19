import { Table, Text } from "@mantine/core";
import type { ProfileIssue } from "../../api";
import ItemCard from "./ItemCard";
import { asList, asRecord, asText, type PanelProps } from "./shared";

const ISSUE_WORDS: Record<ProfileIssue["kind"], string> = {
  duplicate_key: "Repeated key", placeholder_only: "Only placeholders", placeholders: "Placeholder values",
  date_as_text: "Dates stored as text", all_empty: "Empty column", constant: "Constant column",
};
const OUTCOME_WORDS: Record<string, string> = { meets: "Meets", meets_with_gaps: "Meets with gaps", rejected: "Rejected" };

function Figure({ label, value }: { label: string; value: number }) {
  return (
    <li style={{ listStyle: "none", border: "1px solid var(--amz-line)", borderRadius: 6, padding: "6px 14px", minWidth: 110 }}>
      <Text size="sm" c="dimmed" component="span" display="block">{label}</Text>
      <Text fw={700} size="xl" component="strong" display="block">{String(value)}</Text>
    </li>
  );
}

// The profile step (task 7.3b, item 10): figures, the proposed key, the issues, then the summary and its outcome.
export default function ProfilePanel({ ws, items, actions }: PanelProps) {
  const inputs = ws.inputs;
  const summary = items.find((i) => i.kind === "profile_summary");
  const p = asRecord(summary?.payload);
  const keys = asList(p.key_columns).length > 0 ? asList(p.key_columns) : (inputs?.key_columns ?? []);
  const outcome = asText(p.recommended_outcome);
  const gaps = asList(p.gaps);
  const reasons = asList(p.reasons);
  const figures: [string, number | null | undefined][] = [
    ["Rows", inputs?.row_count], ["Columns", inputs?.column_count], ["Duplicate keys", inputs?.duplicate_key_count]];

  return (
    <div>
      <ul aria-label="Profile figures" style={{ display: "flex", gap: 12, flexWrap: "wrap", padding: 0, margin: "0 0 12px" }}>
        {figures.filter((f): f is [string, number] => typeof f[1] === "number").map(([label, value]) => (
          <Figure key={label} label={label} value={value} />
        ))}
      </ul>
      {summary && (
        <div data-testid="profile-key" style={{ marginBottom: 12 }}>
          <strong>Proposed key: </strong>{keys.length > 0 ? keys.join(", ") : "none proposed"}
          {asText(p.key_reason) && <Text size="sm" c="dimmed">{asText(p.key_reason)}</Text>}
        </div>
      )}
      {inputs && inputs.issues.length > 0 ? (
        <Table.ScrollContainer minWidth={420} mb="md">
          <Table aria-label="Issues found in the file" verticalSpacing="xs">
            <Table.Thead>
              <Table.Tr><Table.Th>Issue</Table.Th><Table.Th>Columns</Table.Th><Table.Th>Rows affected</Table.Th><Table.Th>Detail</Table.Th></Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {inputs.issues.map((issue, i) => (
                <Table.Tr key={`${issue.kind}-${i}`}>
                  <Table.Td>{ISSUE_WORDS[issue.kind] ?? issue.kind}</Table.Td>
                  <Table.Td>{issue.columns.join(", ")}</Table.Td>
                  <Table.Td>{issue.count}</Table.Td>
                  <Table.Td>{issue.detail}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      ) : inputs ? <Text mb="md">No issues found in the file.</Text> : null}
      <ul style={{ padding: 0, margin: 0 }}>
        {items.map((item) => (
          <ItemCard key={item.proposal_id} item={item} actions={actions}>
            {item.kind === "profile_summary" && (
              <>
                {asText(p.summary) && <Text mt="xs">{asText(p.summary)}</Text>}
                {outcome && (
                  <div data-testid="profile-outcome" style={{ marginTop: 8 }}>
                    <strong>Recommended outcome: </strong>{OUTCOME_WORDS[outcome] ?? outcome}
                    {gaps.length > 0 && (
                      <ul aria-label="Gaps" style={{ margin: "4px 0", paddingLeft: 18 }}>{gaps.map((g) => <li key={g}>{g}</li>)}</ul>
                    )}
                    {reasons.length > 0 && (
                      <ul aria-label="Reasons" style={{ margin: "4px 0", paddingLeft: 18 }}>{reasons.map((r) => <li key={r}>{r}</li>)}</ul>
                    )}
                  </div>
                )}
              </>
            )}
          </ItemCard>
        ))}
      </ul>
    </div>
  );
}
