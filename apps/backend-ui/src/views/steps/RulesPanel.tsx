import { Text } from "@mantine/core";
import ItemCard from "./ItemCard";
import { asRecord, asText, type PanelProps } from "./shared";

// The back-test delivery that is the feed's own file, when it was read (rows above zero: never "x of 0").
function flagsLine(payload: Record<string, unknown>): string {
  const deliveries = Array.isArray(asRecord(payload.backtest).deliveries) ? (asRecord(payload.backtest).deliveries as unknown[]) : [];
  const own = deliveries.map(asRecord).find((d) => d.label === "current file" && !d.excluded && Number(d.rows) > 0);
  if (!own) return "Not back-tested on this file";
  return Number(own.flagged) > 0 ? `Flags ${Number(own.flagged)} of ${Number(own.rows)} rows on this file`
    : `Flags nothing on this file (${Number(own.rows)} rows)`;
}

// The rules step (task 7.3b): rule key, description, the SQL in a monospace block and the flags on this file.
export default function RulesPanel({ items, actions }: PanelProps) {
  if (items.length === 0) return <Text>No rules were proposed.</Text>;
  return (
    <ul style={{ padding: 0, margin: 0 }}>
      {items.map((item) => (
        <ItemCard key={item.proposal_id} item={item} actions={actions}>
          <Text mt="xs">{asText(item.payload.description)}</Text>
          <pre data-testid="rule-sql" style={{
            fontFamily: "monospace", background: "var(--amz-subtle)", color: "var(--amz-text)", padding: 8, borderRadius: 4,
            overflowX: "auto", whiteSpace: "pre-wrap", margin: "6px 0",
          }}>{asText(item.payload.sql)}</pre>
          <Text size="sm">{flagsLine(item.payload)}</Text>
        </ItemCard>
      ))}
    </ul>
  );
}
