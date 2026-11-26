import { Text } from "@mantine/core";
import { useState } from "react";
import DataGrid, { type DataGridColumn } from "../../../../ui-shared/DataGrid";
import { ConfidenceBar, StatusChip } from "../../../../ui-shared/phase711";
import type { OnboardingItem } from "../../api";
import CannotMapList from "./CannotMapList";
import ItemCard, { type Choice } from "./ItemCard";
import { asList, asText, type PanelProps } from "./shared";

// The mapping step (task 7.3b): per column the element chosen and its score; a tie offers its candidates in a select.
export default function MappingPanel({ ws, items, actions }: PanelProps) {
  const [needsOnly, setNeedsOnly] = useState(false);
  const cannotMap = <CannotMapList columns={ws.cannot_map} actions={actions} />;
  if (items.length === 0) return <>{cannotMap}{ws.cannot_map.length === 0 && <Text>No columns to map.</Text>}</>;
  const shown = needsOnly ? items.filter((item) => item.status === "pending" && item.needs_decision) : items;
  const columns: DataGridColumn<OnboardingItem>[] = [
    { key: "column", label: "Source column", value: (item) => item.column ?? item.kind, render: (item) => <code><strong>{item.column ?? item.kind}</strong></code> },
    { key: "profile", label: "Profile findings", value: (item) => ws.inputs?.issues.filter((issue) => item.column && issue.columns.includes(item.column)).map((issue) => issue.detail).join(" · ") || null },
    { key: "element", label: "Proposed element", value: (item) => asText(item.payload.recommended) || asList(item.payload.candidates).join(", "),
      render: (item) => <><strong>{asText(item.payload.recommended) || "Decision needed"}</strong>
        {!asText(item.payload.recommended) && <div className="muted">{asList(item.payload.candidates).join(", ")}</div>}</> },
    { key: "type", label: "Type", value: (item) => asText(item.payload.column_type) || null },
    { key: "confidence", label: "Confidence", value: (item) => item.confidence,
      render: (item) => <ConfidenceBar value={item.confidence} label={`Confidence for ${item.column ?? item.kind}`} /> },
    { key: "decision", label: "Decision", value: (item) => item.status, render: (item) => {
      const chosen = asText(item.payload.recommended);
      const candidates = asList(item.payload.candidates);
      const choices: Choice[] | undefined = chosen ? undefined : candidates.map((candidate) => ({ value: candidate, label: candidate }));
      return <ul className="mapping-decision" style={{ listStyle: "none", margin: 0, padding: 0 }}>
        <ItemCard item={item} actions={actions} choices={choices}>
          {chosen ? <><Text size="xs">Element chosen: <strong>{chosen}</strong></Text>
            {item.confidence !== null && <Text size="xs" c="dimmed">{`Score ${item.confidence.toFixed(2)}`}</Text>}</>
            : <Text size="xs">{`Tie between ${candidates.join(", ")}`}</Text>}
        </ItemCard>
      </ul>;
    } },
  ];
  return (
    <div>
      {cannotMap}
      <div className="mapping-grid-tools">
        <div className="product-tags">
          <StatusChip tone="good">{items.filter((item) => item.status === "pending" && item.pre_accepted).length} pre-accepted</StatusChip>
          <StatusChip tone="attention">{items.filter((item) => item.status === "pending" && item.needs_decision).length} need you</StatusChip>
        </div>
        <label><input type="checkbox" checked={needsOnly} onChange={(event) => setNeedsOnly(event.currentTarget.checked)} /> Show only items that need me</label>
      </div>
      <DataGrid rows={shown} columns={columns} rowKey={(item) => item.proposal_id} pageSize={10}
        filters={false} sortable={false} showPagination={shown.length > 10} aria-label="Source column mapping"
        rowProps={(item) => ({ style: item.status === "pending" && item.needs_decision ? { background: "#FFF8F1" } : undefined })} />
    </div>
  );
}
