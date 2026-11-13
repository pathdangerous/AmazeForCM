import { useEffect, useState } from "react";
import { apiGet, describeError, type Feed } from "../api";

const STAGES: { value: "" | Feed["stage"]; label: string }[] = [
  { value: "", label: "All" },
  { value: "candidate", label: "Candidate" },
  { value: "registered", label: "Registered" },
];

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
      <h2>Feed registry</h2>
      <div className="toolbar">
        <div className="filters" role="group" aria-label="Stage">
          {STAGES.map((s) => (
            <button key={s.value} type="button" aria-pressed={stage === s.value} onClick={() => setStage(s.value)}>
              {s.label}
            </button>
          ))}
        </div>
      </div>

      {error && <p className="bad" role="alert">{error}</p>}

      {feeds !== null && feeds.length === 0 && (
        <div className="empty" data-testid="feeds-empty">
          {stage ? `No ${stage} feeds.` : "No feeds yet."}
          <div className="muted">Feeds appear here when the custom data product finds a source (phase 3).</div>
        </div>
      )}

      {feeds !== null && feeds.length > 0 && (
        <div className="table-wrap">
          <table data-testid="feeds-table">
            <thead>
              <tr>
                <th>Code</th>
                <th>Name</th>
                <th>Stage</th>
                <th>Owner</th>
                <th>Source</th>
                <th>Created</th>
              </tr>
            </thead>
            <tbody>
              {feeds.map((f) => (
                <tr key={f.id}>
                  <td><code>{f.code}</code></td>
                  <td>{f.name}</td>
                  <td>{f.stage}</td>
                  <td>{f.owner_id ?? <span className="muted">none</span>}</td>
                  <td>{f.source_ref ?? <span className="muted">none</span>}</td>
                  <td>{new Date(f.created_at).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
