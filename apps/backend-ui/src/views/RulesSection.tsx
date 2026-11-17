import { useState } from "react";
import {
  apiPost, describeError, words, type Backtest, type FeedPage, type PendingRule, type RefusedRule, type ReturnedRule,
} from "../api";

// Step 09 on a registered feed (phase 6 plan, section 10): the rules it has, the rules waiting for the data steward
// and then the checker, and Propose rules for the steward. A rule is SQL over the table `curated` that returns the
// rows that break it; the RuleRecommenderAgent proposes it, code back-tests it on the synthetic history, two people
// decide it. Returned and refused rules are listed with why; they are not stored (they exist only in the answer).

function share(back: Backtest | null): string {
  if (!back || back.status !== "ran" || back.share === null) return "not back-tested";
  return `flags ${back.flagged_rows} of ${back.total_rows} history rows (${Math.round(back.share * 1000) / 10}%)`;
}

function BacktestDetail({ back }: { back: Backtest | null }) {
  if (!back || back.deliveries.length === 0) return null;
  return (
    <details>
      <summary className="muted">Back-test by delivery</summary>
      <ul data-testid="backtest">
        {back.deliveries.map((d) => (
          <li key={d.label}>
            {d.label}: {d.excluded ? <span className="bad">left out ({d.excluded})</span> : `${d.flagged} of ${d.rows} rows flagged`}
          </li>
        ))}
      </ul>
    </details>
  );
}

function PendingRuleRow({ rule, roles, busy, onDecide }: {
  rule: PendingRule; roles: string[]; busy: boolean;
  onDecide: (id: string, decision: "approved" | "rejected", note: string) => void;
}) {
  const [note, setNote] = useState("");
  const step = rule.decisions.length + 1; // 1: the data steward, 2: the checker
  const role = rule.required_approver_roles[step - 1];
  const mine = rule.status === "pending" && roles.includes(role);
  return (
    <tr data-testid="pending-rule" data-rule={rule.payload.rule_key}>
      <td><code>{rule.payload.rule_key}</code><div className="muted">{rule.payload.description}</div></td>
      <td><code>{rule.payload.sql}</code></td>
      <td>{share(rule.payload.backtest)}<BacktestDetail back={rule.payload.backtest} /></td>
      <td data-testid="rule-step">
        {rule.status === "pending" ? <>Waits for the {words(role)} (step {step} of {rule.required_approver_roles.length})</> : rule.status}
        {rule.decisions.map((d) => <div key={d.id} className="muted">{words(d.role)} {d.decision}{d.note ? `: ${d.note}` : ""}</div>)}
      </td>
      <td>
        {mine && (
          <div className="toolbar" style={{ marginBottom: 0 }}>
            <button type="button" disabled={busy} data-testid="approve-rule"
                    onClick={() => onDecide(rule.id, "approved", "")}>Approve</button>
            <input aria-label={`Note for ${rule.payload.rule_key}`} placeholder="Note (needed to reject)" value={note}
                   maxLength={500} size={16} onChange={(e) => setNote(e.target.value)} />
            <button type="button" disabled={busy || !note.trim()} data-testid="reject-rule"
                    onClick={() => onDecide(rule.id, "rejected", note)}>Reject</button>
          </div>
        )}
      </td>
    </tr>
  );
}

export default function RulesSection({ page, devUser, roles, onChange, onMessage, onError }: {
  page: FeedPage; devUser: string; roles: string[]; onChange: () => void; onMessage: (m: string) => void;
  onError: (m: string | null) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [returned, setReturned] = useState<ReturnedRule[]>([]);
  const [refused, setRefused] = useState<RefusedRule[]>([]);
  const { rules, feed } = page;
  const canPropose = roles.includes("data_steward");

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

  const propose = () => run(async () => {
    const r = await apiPost<{ result: { outcome: string; proposal_ids?: string[]; returned: ReturnedRule[];
                                        refused: RefusedRule[] } }>(`/api/amaze/feeds/${feed.id}/rules/propose`, {}, devUser);
    setReturned(r.result.returned);
    setRefused(r.result.refused);
    const made = r.result.proposal_ids?.length ?? 0;
    return r.result.outcome === "flags_most_history_returned"
      ? "Every answered rule flags most of the history: none was proposed."
      : `${made} rule${made === 1 ? "" : "s"} proposed, ${r.result.returned.length} returned, ${r.result.refused.length} refused.`;
  });
  const decide = (id: string, decision: "approved" | "rejected", note: string) => run(async () => {
    const r = await apiPost<{ result: { rule?: { rule_key: string; version: number } } }>(
      `/api/amaze/proposals/${id}/decision`, { decision, note: note || null }, devUser);
    if (decision === "rejected") return "Rejected: its key is not proposed again.";
    return r.result.rule ? `Rule ${r.result.rule.rule_key} stored (version ${r.result.rule.version}).` : "Approved: the checker decides next.";
  });

  return (
    <>
      <h3>Rules</h3>
      {rules.current.length === 0 ? <p className="muted">No rule yet.</p> : (
        <div className="table-wrap">
          <table data-testid="rules-table">
            <thead><tr><th>Rule</th><th>SQL over curated</th><th>Back-test</th><th>Version</th></tr></thead>
            <tbody>
              {rules.current.map((r) => (
                <tr key={r.id} data-testid="rule-row" data-rule={r.rule_key}>
                  <td><code>{r.rule_key}</code><div className="muted">{r.description}</div></td>
                  <td><code>{r.sql}</code></td>
                  <td>{share(r.backtest)}<BacktestDetail back={r.backtest} /></td>
                  <td>{r.version}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {rules.pending.length > 0 && (
        <>
          <h4>Waiting for a decision</h4>
          <div className="table-wrap">
            <table data-testid="pending-rules">
              <thead><tr><th>Rule</th><th>SQL over curated</th><th>Back-test</th><th>Status</th><th></th></tr></thead>
              <tbody>
                {rules.pending.map((r) => <PendingRuleRow key={r.id} rule={r} roles={roles} busy={busy} onDecide={decide} />)}
              </tbody>
            </table>
          </div>
        </>
      )}

      {returned.length > 0 && (
        <div className="detail" data-testid="returned-rules">
          <strong>Returned, not proposed</strong>
          <ul>
            {returned.map((r) => (
              <li key={r.rule_key}><code>{r.rule_key}</code>: flags {r.flagged_rows} of {r.total_rows} history rows
                ({Math.round(r.share * 1000) / 10}%), more than half.</li>
            ))}
          </ul>
        </div>
      )}
      {refused.length > 0 && (
        <div className="detail" data-testid="refused-rules">
          <strong>Refused</strong>
          <ul>{refused.map((r) => <li key={r.rule_key}><code>{r.rule_key}</code>: {r.reason}</li>)}</ul>
        </div>
      )}
      {rules.rejected_keys.length > 0 && (
        <p className="muted" data-testid="rejected-keys">Rejected, not proposed again: {rules.rejected_keys.join(", ")}.</p>
      )}

      {canPropose ? (
        <p>
          <button type="button" disabled={busy} data-testid="propose-rules" onClick={propose}>Propose rules</button>{" "}
          <span className="muted">the agent's answer is back-tested before you see it</span>
        </p>
      ) : <p className="muted">Only the data steward proposes rules; the steward and then the checker decide them.</p>}
    </>
  );
}
