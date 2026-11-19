import { Text } from "@mantine/core";
import ItemCard, { type Choice } from "./ItemCard";
import { asList, asText, type PanelProps } from "./shared";

// The mapping step (task 7.3b): per column the element chosen and its score; a tie offers its candidates in a select.
export default function MappingPanel({ items, actions }: PanelProps) {
  if (items.length === 0) return <Text>No columns to map.</Text>;
  return (
    <ul style={{ padding: 0, margin: 0 }}>
      {items.map((item) => {
        const chosen = asText(item.payload.recommended);
        const candidates = asList(item.payload.candidates);
        const choices: Choice[] | undefined = chosen ? undefined : candidates.map((c) => ({ value: c, label: c }));
        return (
          <ItemCard key={item.proposal_id} item={item} actions={actions} choices={choices}>
            {chosen ? (
              <>
                <Text mt="xs">Element chosen: <strong>{chosen}</strong></Text>
                {item.confidence !== null && <Text size="sm" c="dimmed">{`Score ${item.confidence.toFixed(2)}`}</Text>}
              </>
            ) : (
              <Text mt="xs">{`Tie between ${candidates.join(", ")}`}</Text>
            )}
          </ItemCard>
        );
      })}
    </ul>
  );
}
