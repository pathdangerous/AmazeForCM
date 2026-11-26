import { Text } from "@mantine/core";
import type { OnboardingItem } from "../../api";
import ItemCard, { type Choice } from "./ItemCard";
import { asList, asRecord, asText, Facts, type PanelProps } from "./shared";

function optionLabel(raw: unknown): string {
  const o = asRecord(raw);
  return o.kind === "alias" ? `Alias ${asText(o.alias)} of ${asText(o.key)}` : `New element ${asText(o.key)}`;
}

function Body({ item }: { item: OnboardingItem }) {
  const p = item.payload;
  const options = Array.isArray(p.options) ? p.options : [];
  if (p.outcome === "conflict_to_owner") {
    return <Text mt="xs">{`Clashes with: ${asList(p.clashes).join(", ")}. The glossary owner chooses one option.`}</Text>;
  }
  const option = asRecord(options[typeof p.recommended === "number" ? p.recommended : 0]);
  if (option.kind === "alias") return <Text mt="xs">{optionLabel(option)}</Text>;
  const e = asRecord(option.element);
  return (
    <Facts rows={[["Proposed new element", asText(e.key)], ["Name", asText(e.name)], ["Domain", asText(e.domain)],
                  ["Type", asText(e.data_type)], ["Unit", asText(e.unit)], ["Definition", asText(e.definition)]]} />
  );
}

// The glossary step (task 7.3b): per column a proposed new element, an alias of an element, or a conflict with its options.
export default function GlossaryPanel({ items, actions }: PanelProps) {
  if (items.length === 0) return <Text>No glossary element was requested. A column no element fits is asked for in the Mapping step.</Text>;
  return (
    <ul style={{ padding: 0, margin: 0 }}>
      {items.map((item) => {
        const conflict = item.payload.outcome === "conflict_to_owner";
        const choices: Choice[] | undefined = conflict
          ? (Array.isArray(item.payload.options) ? item.payload.options : []).map((o, i) => ({ value: i, label: optionLabel(o) }))
          : undefined;
        return <ItemCard key={item.proposal_id} item={item} actions={actions} choices={choices}><Body item={item} /></ItemCard>;
      })}
    </ul>
  );
}
