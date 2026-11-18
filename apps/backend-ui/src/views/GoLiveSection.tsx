import { useState } from "react";
import { apiPost, describeError, type FeedPage } from "../api";

// Step 11 on a registered feed (phase 6 plan, section 10). Prepare (backend team): a test load of the latest file,
// then the ReviewSummariserAgent's draft. The draft is the agent's: it decides nothing. The feed's named owner, and
// only that person, approves it (the daily job then loads the feed; hypercare starts) or rejects it with a note and
// the step the feed goes back to.

const STEPS = [
  { value: "05", label: "05 Glossary" },
  { value: "06", label: "06 Mapping" },
  { value: "09", label: "09 Rules" },
] as const;

export default function GoLiveSection({ page, devUser, userId, roles, onChange, onMessage, onError }: {
  page: FeedPage; devUser: string; userId: string; roles: string[]; onChange: () => void;
  onMessage: (m: string) => void; onError: (m: string | null) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [step, setStep] = useState<"05" | "06" | "09" | "">("");
  const [held, setHeld] = useState<string | null>(null);
  const { feed, go_live: live } = page;
  const summary = live.pending_summary;
  const owner = page.registration?.owner_id ?? null;
  const canPrepare = roles.includes("backend_team");
  const isOwner = roles.includes("data_owner") && owner === userId;

  async function run(work: () => Promise<string>) {
    setBusy(true);
    try {
      const message = await work();
      onError(null);
      onMessage(message);
      onChange();
    } catch (err: unknown) {
      onError(describeError(err));
    } finally {
      setBusy(false);
    }
  }

  const prepare = () => run(async () => {
    setHeld(null);
    const r = await apiPost<{ result: { outcome?: string; stage?: string; reason?: string } }>(
      `/api/amaze/feeds/${feed.id}/go-live/summary`, {}, devUser);
    if (r.result.outcome === "test_failed_held") {
      setHeld(`The test load failed at ${r.result.stage} (${(r.result.reason ?? "").replaceAll("_", " ")}). The feed is held.`);
      return "The test load failed: nothing was proposed.";
    }
    return "Go-live summary ready for the owner.";
  });
  const approve = () => run(async () => {
    const r = await apiPost<{ result: { hypercare_until: string; daily_run_utc: string } }>(
      `/api/amaze/proposals/${summary!.id}/decision`, { decision: "approved" }, devUser);
    return `Approved: the daily job loads this feed at ${r.result.daily_run_utc} UTC; hypercare until ${r.result.hypercare_until}.`;
  });
  const reject = () => run(async () => {
    await apiPost(`/api/amaze/proposals/${summary!.id}/decision`, { decision: "rejected", note, back_to_step: step }, devUser);
    setNote("");
    setStep("");
    return `Rejected: the feed goes back to step ${step}.`;
  });

  return (
    <div data-testid="section-go-live">
      <h3>Go-live</h3>
      {live.latest ? (
        <p data-testid="go-live-latest">
          Version {live.latest.version} approved by {live.latest.approved_by}; the daily job loads this feed.{" "}
          <span className={live.hypercare_active ? "ok" : "muted"}>
            Hypercare {live.hypercare_active ? "until" : "ended"} {live.latest.hypercare_until}.
          </span>
        </p>
      ) : <p className="muted" data-testid="go-live-none">Not live: the owner has not approved it for loading.</p>}
      {held && <p className="bad" role="alert" data-testid="go-live-held">{held}</p>}

      {summary && (
        <div className="detail" data-testid="go-live-summary">
          <div className="muted">ReviewSummariserAgent's draft; it decides nothing. The owner is {owner ?? "not named"}.</div>
          <p>{summary.payload.summary}</p>
          {summary.payload.highlights.length > 0 && <ul>{summary.payload.highlights.map((h) => <li key={h}>{h}</li>)}</ul>}
          {summary.payload.risks.length > 0 && (
            <ul data-testid="go-live-risks">{summary.payload.risks.map((r) => <li key={r} className="bad">{r}</li>)}</ul>
          )}
          <p data-testid="go-live-unmapped">
            Not loaded (unmapped columns): {summary.payload.unmapped_columns.length > 0
              ? summary.payload.unmapped_columns.map((c) => <code key={c}>{c} </code>) : "none"}
          </p>
          <p data-testid="go-live-rejected-glossary">
            Rejected glossary proposals: {summary.payload.rejected_glossary.length > 0
              ? summary.payload.rejected_glossary.map((g) => <span key={g.source_field}><code>{g.source_field}</code>
                {g.note ? ` (${g.note})` : ""} </span>) : "none"}
          </p>
          {isOwner ? (
            <div className="toolbar" style={{ marginBottom: 0 }}>
              <button type="button" disabled={busy} data-testid="approve-go-live" onClick={approve}>Approve go-live</button>
              <select aria-label="Back to step" value={step} data-testid="back-to-step"
                      onChange={(e) => setStep(e.target.value as typeof step)}>
                <option value="">— back to step —</option>
                {STEPS.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
              </select>
              <input aria-label="Note for the rejection" placeholder="Note (needed to reject)" value={note}
                     maxLength={500} size={20} onChange={(e) => setNote(e.target.value)} />
              <button type="button" disabled={busy || !note.trim() || !step} data-testid="reject-go-live"
                      onClick={reject}>Reject</button>
            </div>
          ) : (
            <p className="muted">Only {owner ?? "the named owner"} decides this go-live.</p>
          )}
        </div>
      )}

      {canPrepare ? (
        <p>
          <button type="button" disabled={busy || !!summary} data-testid="prepare-go-live" onClick={prepare}>
            Prepare go-live
          </button>{" "}
          <span className="muted">a test load of the latest file, then the agent's summary for the owner</span>
        </p>
      ) : <p className="muted">Only the backend team prepares a go-live.</p>}
    </div>
  );
}
