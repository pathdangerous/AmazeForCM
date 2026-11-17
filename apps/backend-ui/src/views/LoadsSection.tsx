import { useState } from "react";
import { apiPost, describeError, words, type FeedPage, type LoadOnPage } from "../api";

// Step 13 on a registered feed (phase 6 plan, section 10): the latest loads with their four stages and counts, and
// Load now for operations (the job's own fetch and load of this one feed; it needs a go-live).

function stageText(load: LoadOnPage): string {
  return load.stages.map((s) =>
    s.outcome === "stage_completed" ? `${s.stage} ${s.rows_in}→${s.rows_out}` : `${s.stage} FAILED`).join(" · ");
}

// Phase 7: what a person reads for a load's purpose, step 12's arrival (and why) and step 15's certification.
const PURPOSE: Record<string, string> = { go_live_test: "test load for go-live", scheduled: "scheduled load" };

function Arrival({ load }: { load: LoadOnPage }) {
  if (!load.arrival_outcome) return <span className="muted">{load.purpose === "scheduled" ? "not checked" : "-"}</span>;
  const d = load.arrival_detail ?? {};
  return (
    <>
      <span data-testid="arrival">{words(load.arrival_outcome)}</span>
      {d.missing && d.missing.length > 0 && <div>missing: <code>{d.missing.join(", ")}</code></div>}
      {d.added && d.added.length > 0 && (
        <div>{load.arrival_outcome === "additive_parked" ? "parked" : "not parked"}: <code>{d.added.join(", ")}</code></div>
      )}
      {load.arrival_outcome === "breaking_held" && d.reason && <div className="muted">{d.reason}</div>}
      {load.arrival_outcome === "resent_quarantined" && <div className="muted">the same file as an earlier delivery</div>}
    </>
  );
}

function Certification({ load }: { load: LoadOnPage }) {
  const c = load.certification;
  if (!c) return <span className="muted">-</span>;
  return (
    <span data-testid="certification" className={c.outcome === "released" ? "ok" : "bad"}>
      trust {c.trust_score} (threshold {c.threshold}): {words(c.outcome)}
      <div className="muted">{c.flagged_rows} of {c.rows} rows flagged</div>
    </span>
  );
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
            <thead>
              <tr><th>When</th><th>Purpose</th><th>Arrival</th><th>Status</th><th>Certification</th>
                  <th>Stages (rows in→out)</th></tr>
            </thead>
            <tbody>
              {page.loads.map((l) => (
                <tr key={l.id} data-testid="load-row" data-purpose={l.purpose}>
                  <td>{new Date(l.received_at).toLocaleString()}</td>
                  <td>{PURPOSE[l.purpose] ?? words(l.purpose)}</td>
                  <td><Arrival load={l} /></td>
                  <td className={l.status === "loaded" || l.status === "released" ? "ok"
                    : l.status === "received" ? "muted" : "bad"}>
                    {words(l.status)}{l.failure && <div>{l.failure}</div>}
                  </td>
                  <td><Certification load={l} /></td>
                  <td data-testid="stages">{stageText(l)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="muted" data-testid="loads-legend">
            Stages show rows in→out, in the order raw, curated, validated, standard. A held, quarantined or
            abandoned load has no stage: it never reached the engine.
          </p>
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
