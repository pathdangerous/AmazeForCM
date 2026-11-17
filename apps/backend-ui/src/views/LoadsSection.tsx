import { useState } from "react";
import { apiPost, describeError, words, type FeedPage, type LoadOnPage } from "../api";

// Step 13 on a registered feed (phase 6 plan, section 10): the latest loads with their four stages and counts, and
// Load now for operations (the job's own fetch and load of this one feed; it needs a go-live).

function stageText(load: LoadOnPage): string {
  return load.stages.map((s) =>
    s.outcome === "stage_completed" ? `${s.stage} ${s.rows_in}→${s.rows_out}` : `${s.stage} FAILED`).join(" · ");
}

export default function LoadsSection({ page, devUser, roles, onChange, onMessage, onError }: {
  page: FeedPage; devUser: string; roles: string[]; onChange: () => void; onMessage: (m: string) => void;
  onError: (m: string | null) => void;
}) {
  const [busy, setBusy] = useState(false);
  const canLoad = roles.includes("operations") && page.go_live.latest !== null;

  async function loadNow() {
    setBusy(true);
    try {
      const r = await apiPost<{ result: { fetch: string; load: string | null } }>(
        `/api/amaze/feeds/${page.feed.id}/loads`, {}, devUser);
      onError(null);
      onMessage(r.result.load ? `Loaded: ${words(r.result.load)}.` : `Not loaded: the fetch ${words(r.result.fetch)}.`);
      onChange();
    } catch (err: unknown) {
      onError(describeError(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <h3>Loads</h3>
      {page.loads.length === 0 ? <p className="muted">No load yet.</p> : (
        <div className="table-wrap">
          <table data-testid="loads-table">
            <thead><tr><th>When</th><th>Purpose</th><th>Status</th><th>Stages (rows in→out)</th></tr></thead>
            <tbody>
              {page.loads.map((l) => (
                <tr key={l.id} data-testid="load-row" data-purpose={l.purpose}>
                  <td>{new Date(l.received_at).toLocaleString()}</td>
                  <td>{words(l.purpose)}</td>
                  <td className={l.status === "loaded" ? "ok" : l.status === "received" ? "muted" : "bad"}>
                    {words(l.status)}{l.failure && <div>{l.failure}</div>}
                  </td>
                  <td>{stageText(l)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {canLoad ? (
        <p>
          <button type="button" disabled={busy} data-testid="load-now" onClick={loadNow}>Load now</button>{" "}
          <span className="muted">fetch the source and load it, as the daily job does</span>
        </p>
      ) : roles.includes("operations") ? <p className="muted">Load now needs a go-live first.</p>
        : <p className="muted">Only operations loads a feed by hand.</p>}
    </>
  );
}
