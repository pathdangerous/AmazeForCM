import { Text } from "@mantine/core";
import type { StepState, Workspace } from "../../api";

const FIGURES = ["Rows loaded", "Rows flagged", "Trust score"];

// The first load: the outcome (the certification's, when the load has one), the figures (rows loaded, rows flagged,
// trust score) and the other facts the API gives. A held load shows the warning fill and its detail.
export default function FirstLoadPanel({ ws, state, detail }: { ws: Workspace; state: StepState; detail: string | null }) {
  const rows = ws.inputs?.row_count;
  const facts = ws.inputs?.facts ?? [];
  const fact = (label: string) => facts.find((f) => f.label === label)?.value;
  const figures = FIGURES.filter((label) => fact(label) !== undefined);
  const rest = facts.filter((f) => !FIGURES.includes(f.label));
  const held = fact("Outcome")?.startsWith("held") === true || fact("Load") === "held";
  const outcome = held ? "Held: the trust score is below the release threshold; consumers are warned."
    : state === "done" ? "Released: the first load passed and the product was created."
    : state === "failed" ? "Held or failed"
    : "Ready to load: Approve & next runs the first load.";
  return (
    <div>
      <Text data-testid="first-load-outcome" fw={600}
            style={held ? { background: "var(--amz-warning)", color: "var(--amz-on-bright)", padding: "4px 10px", borderRadius: 4 } : undefined}>
        {outcome}
      </Text>
      {(held || state === "failed") && detail && <Text mt="xs" data-testid="first-load-detail">{detail}</Text>}
      {figures.length > 0 && (
        <div data-testid="first-load-figures" style={{ display: "flex", gap: 24, margin: "12px 0", flexWrap: "wrap" }}>
          {figures.map((label) => (
            <div key={label}>
              <Text size="xl" fw={700}>{fact(label)}</Text>
              <Text size="sm">{label}</Text>
            </div>
          ))}
        </div>
      )}
      <ul style={{ margin: "8px 0" }}>
        {rows ?<li>{`Rows in the file: ${rows}`}</li> : null}
        {rest.map((f) => <li key={f.label}>{`${f.label}: ${f.value}`}</li>)}
      </ul>
    </div>
  );
}
