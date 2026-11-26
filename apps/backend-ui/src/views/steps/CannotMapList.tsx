import { Button, Text } from "@mantine/core";
import { StatusChip } from "../../../../ui-shared/phase711";
import type { CannotMapColumn } from "../../api";
import type { ItemActions } from "./shared";

// Phase 7.22: the columns no glossary element fits. A person chooses per column (Ignore: left out, not loaded). The state
// is a word, never only a colour.
const REASONS: Record<string, string> = {
  no_candidate: "No element is close enough to this column.",
  type_does_not_fit: "The type does not fit any element that is close.",
  every_candidate_rejected: "Every close element was rejected before.",
};
const STATE_WORDS: Record<CannotMapColumn["state"], string> = {
  undecided: "Undecided", ignored: "Ignored: left out, not loaded", requested: "Glossary element requested",
};

export default function CannotMapList({ columns, actions }: { columns: CannotMapColumn[]; actions: ItemActions }) {
  if (columns.length === 0) return null;
  const undecided = columns.filter((c) => c.state === "undecided");
  const can = !actions.readOnly && !actions.busy && actions.cannotMap !== undefined;
  return (
    <section data-testid="cannot-map" aria-label="Cannot map" style={{ marginBottom: 16 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 6 }}>
        <Text fw={600}>Cannot map: no glossary element fits</Text>
        <StatusChip tone={undecided.length ? "attention" : "good"}>{`${undecided.length} undecided`}</StatusChip>
        {can && undecided.length > 1 && (
          <Button size="xs" variant="default" onClick={() => actions.cannotMap?.("ignore", undecided.map((c) => c.column))}>
            Ignore all undecided
          </Button>
        )}
      </div>
      <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
        {columns.map((c) => (
          <li key={c.column} data-testid={`cannot-map-${c.column}`}
              style={{ display: "flex", alignItems: "center", gap: 12, padding: "4px 0" }}>
            <code><strong>{c.column}</strong></code>
            <Text size="sm" c="dimmed" style={{ flex: 1 }}>{REASONS[c.reason] ?? c.reason}</Text>
            <span>{STATE_WORDS[c.state]}</span>
            {c.state === "undecided" && can && (
              <Button size="xs" variant="default" aria-label={`Ignore ${c.column}`}
                      onClick={() => actions.cannotMap?.("ignore", [c.column])}>Ignore</Button>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
