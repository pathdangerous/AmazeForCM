import { Anchor, Button, Table, Text, Title } from "@mantine/core";
import { useEffect, useState } from "react";
import { apiGet, describeError, type Feed } from "../api";

const STAGES: { value: "" | Feed["stage"]; label: string }[] = [
  { value: "", label: "All" },
  { value: "candidate", label: "Candidate" },
  { value: "registered", label: "Registered" },
  { value: "rejected", label: "Rejected" },
];

// Converted to the phase 7.5 look (task 4); its polish lines P3 to P5 are task 13.
export default function FeedRegistryView({ devUser }: { devUser: string }) {
  const [stage, setStage] = useState<"" | Feed["stage"]>("");
  const [feeds, setFeeds] = useState<Feed[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!devUser) return;
    let current = true;
    const query = stage ? `?stage=${stage}` : "";
    apiGet<Feed[]>(`/api/amaze/feeds${query}`, devUser)
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
          <div className="muted">Feeds appear here when the custom data product finds a source (phase 3).</div>
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
                  <Table.Td>{f.owner_id ?? <span className="muted">none</span>}</Table.Td>
                  <Table.Td>{f.source_ref ?? <span className="muted">none</span>}</Table.Td>
                  <Table.Td>{new Date(f.created_at).toLocaleString()}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      )}
    </section>
  );
}
