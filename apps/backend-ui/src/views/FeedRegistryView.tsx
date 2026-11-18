import { Anchor, Button, Table, Text, Title } from "@mantine/core";
import { useEffect, useState } from "react";
import { apiGet, describeError, type Feed, type FeedRow } from "../api";
import { formatWhen } from "../theme";

const STAGES: { value: "" | Feed["stage"]; label: string }[] = [
  { value: "", label: "All" },
  { value: "candidate", label: "Candidate" },
  { value: "registered", label: "Registered" },
  { value: "rejected", label: "Rejected" },
];

// Converted to the phase 7.5 look (task 4). P3: the owner by name; P4: the source by its catalogue name, the raw
// reference beside it; P5: an empty state in the backend team's words.
export default function FeedRegistryView({ devUser, people }: { devUser: string; people: Record<string, string> }) {
  const [stage, setStage] = useState<"" | Feed["stage"]>("");
  const [feeds, setFeeds] = useState<FeedRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!devUser) return;
    let current = true;
    const query = stage ? `?stage=${stage}` : "";
    apiGet<FeedRow[]>(`/api/amaze/feeds${query}`, devUser)
      .then((list) => {
        if (!current) return;
        setFeeds(list);
        setError(null);
      })
      .catch((err: unknown) => current && setError(describeError(err)));
    return () => {
      current = false;
    };
  }, [devUser, stage]);

  return (
    <section>
      <Title order={2} mb="md">Feed registry</Title>
      <div className="toolbar">
        <Button.Group aria-label="Stage">{/* renders role="group" */}
          {STAGES.map((s) => (
            <Button key={s.value} size="xs" variant={stage === s.value ? "filled" : "default"}
                    aria-pressed={stage === s.value} onClick={() => setStage(s.value)}>
              {s.label}
            </Button>
          ))}
        </Button.Group>
      </div>

      {error && <Text className="bad" role="alert">{error}</Text>}

      {feeds !== null && feeds.length === 0 && (
        <div className="empty" data-testid="feeds-empty">
          {stage ? `No ${stage} feeds.` : "No feeds yet."}
          <div className="muted">Feeds appear here when a requester's source is selected.</div>
        </div>
      )}

      {feeds !== null && feeds.length > 0 && (
        <Table.ScrollContainer minWidth={720}>
          <Table data-testid="feeds-table" highlightOnHover verticalSpacing="xs">
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Code</Table.Th>
                <Table.Th>Name</Table.Th>
                <Table.Th>Stage</Table.Th>
                <Table.Th>Owner</Table.Th>
                <Table.Th>Source</Table.Th>
                <Table.Th>Created</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {feeds.map((f) => (
                <Table.Tr key={f.id}>
                  <Table.Td>
                    <Anchor href={`#/feed/${f.id}`} data-testid={`feed-link-${f.code}`}><code>{f.code}</code></Anchor>
                  </Table.Td>
                  <Table.Td>{f.name}</Table.Td>
                  <Table.Td>{f.stage}</Table.Td>
                  <Table.Td>{f.owner_id ? (people[f.owner_id] ?? f.owner_id) : <span className="muted">none</span>}</Table.Td>
                  <Table.Td>
                    {f.source_name ? (
                      <>
                        <span data-testid="source-name">{f.source_name}</span>
                        {f.source_ref && <div className="muted" data-testid="source-ref">{f.source_ref}</div>}
                      </>
                    ) : f.source_ref ?? <span className="muted">none</span>}
                  </Table.Td>
                  <Table.Td>{formatWhen(f.created_at)}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      )}
    </section>
  );
}
