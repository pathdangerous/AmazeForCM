import { Anchor, Button, Text, Title } from "@mantine/core";
import { useEffect, useState } from "react";
import DataGrid, { type DataGridColumn } from "../../../ui-shared/DataGrid";
import { LoadingSkeleton, StatusChip } from "../../../ui-shared/phase711";
import { apiGet, describeError, type Feed, type FeedRow, type User } from "../api";
import { formatWhen } from "../theme";
import FeedLifecycle from "./FeedLifecycle";

const STAGES: { value: "" | Feed["stage"]; label: string }[] = [
  { value: "", label: "All" },
  { value: "candidate", label: "Candidate" },
  { value: "registered", label: "Registered" },
  { value: "paused", label: "Paused" },
  { value: "rejected", label: "Rejected" },
];

// Converted to the phase 7.5 look (task 4). P3: the owner by name; P4: the source by its catalogue name, the raw
// reference beside it; P5: an empty state in the backend team's words.
export default function FeedRegistryView({ devUser, people, me = null, search = "" }: {
  devUser: string; people: Record<string, string>; me?: User | null; search?: string;
}) {
  const [stage, setStage] = useState<"" | Feed["stage"]>("");
  const [feeds, setFeeds] = useState<FeedRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0); // phase 7.9: a pause, a resume or a delete reads the list again

  const columns: DataGridColumn<FeedRow>[] = [
    { key: "code", label: "Code", value: (f) => f.code, render: (f) => <Anchor href={`#/feed/${f.id}`} data-testid={`feed-link-${f.code}`}><code>{f.code}</code></Anchor> },
    { key: "name", label: "Name", value: (f) => f.name },
    { key: "stage", label: "Stage", value: (f) => f.stage, render: (f) => <StatusChip tone={f.stage === "registered" ? "good" : f.stage === "paused" || f.stage === "rejected" ? "attention" : "neutral"}>{f.stage}</StatusChip> },
    { key: "owner", label: "Owner", value: (f) => f.owner_id ? people[f.owner_id] ?? f.owner_id : "none" },
    { key: "source", label: "Source", value: (f) => f.source_name ?? f.source_ref ?? "none", render: (f) => f.source_name ? <>
      <span data-testid="source-name">{f.source_name}</span>{f.source_ref && <div className="muted" data-testid="source-ref">{f.source_ref}</div>}
    </> : f.source_ref ?? <span className="muted">none</span> },
    { key: "created", label: "Created", value: (f) => f.created_at, render: (f) => formatWhen(f.created_at) },
    ...(me ? [{ key: "actions", label: "Actions", value: () => "", sortable: false, filterable: false,
      render: (f: FeedRow) => <FeedLifecycle feed={f} me={me} devUser={devUser}
        onChanged={() => setReload((n) => n + 1)} onDeleted={() => setReload((n) => n + 1)} /> }] : []),
  ];
  const matched = feeds?.filter((feed) => [feed.code, feed.name, feed.source_name, feed.source_ref,
    feed.owner_id ? people[feed.owner_id] ?? feed.owner_id : ""].some((value) => value?.toLowerCase().includes(search.trim().toLowerCase()))) ?? [];

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
  }, [devUser, stage, reload]);

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

      {error && <div><Text className="bad" role="alert">{error}</Text>
        <Button variant="default" onClick={() => setReload((n) => n + 1)}>Try again</Button></div>}
      {feeds === null && !error && <LoadingSkeleton label="Loading feed registry" />}

      {feeds !== null && feeds.length === 0 && (
        <div className="empty" data-testid="feeds-empty">
          {stage ? `No ${stage} feeds.` : "No feeds yet."}
          <div className="muted">Feeds appear here when a requester's source is selected.</div>
        </div>
      )}

      {feeds !== null && feeds.length > 0 && (
        <DataGrid rows={matched} columns={columns} rowKey={(feed) => feed.id} pageSize={10}
          aria-label="Feed registry" data-testid="feeds-table" />
      )}
    </section>
  );
}
