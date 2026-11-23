import { Button, NativeSelect, Text, Textarea } from "@mantine/core";
import { useCallback, useEffect, useState } from "react";
import {
  addDriftAction, decideProposal, describeError, getDrift, type DriftAction, type DriftView,
} from "../api";

const ACTIONS = [
  { value: "map_new_column", label: "Map a new column" }, { value: "review_missing_column", label: "Look into a missing column" },
  { value: "accept_type_change", label: "Accept a type change" }, { value: "review_rule", label: "Review a rule" },
  { value: "add_rule", label: "Add a rule" }, { value: "exclude_column", label: "Leave a column out" },
];

function who(a: DriftAction): string {
  const by = a.applied_by === "manual" ? "I'll do it" : "Agent applies";
  return `${by}${a.origin === "manual" ? ", added by hand" : ", proposed by rules"}`;
}

// Drift on a new delivery (T4): what step 12 found against the last accepted structure, the actions it came with (made by
// code, no model), and a person's choice: select or reject each, or add one. A selected action reaches the later agents; the
// next go-live stores the next code version, and the Code view shows its diff. A changed column order is information only.
export default function DriftSection({ feedId, devUser, canAct }: { feedId: string; devUser: string; canAct: boolean }) {
  const [view, setView] = useState<DriftView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [action, setAction] = useState("add_rule");
  const [column, setColumn] = useState("");
  const [reason, setReason] = useState("");
  const [rejecting, setRejecting] = useState<string | null>(null);
  const [note, setNote] = useState("");

  const load = useCallback(() => getDrift(feedId, devUser).then((v) => { setView(v); setError(null); })
    .catch((err: unknown) => setError(describeError(err))), [feedId, devUser]);
  useEffect(() => { void load(); }, [load]);

  const decide = (a: DriftAction, decision: "approved" | "rejected", why: string | null) =>
    decideProposal(a.proposal_id, decision, null, why, devUser).then(() => { setRejecting(null); setNote(""); return load(); })
      .catch((err: unknown) => setError(describeError(err)));
  const add = () => addDriftAction(feedId, { action, columns: column.trim() ? [column.trim()] : [], reason: reason.trim() }, devUser)
    .then((v) => { setView(v); setAdding(false); setReason(""); setColumn(""); setError(null); })
    .catch((err: unknown) => setError(describeError(err)));

  if (!view || view.load_id === null) return error ? <Text role="alert" className="bad">{error}</Text> : null;
  const r = view.report;
  const lines = [
    r.new_columns.length > 0 && `New columns: ${r.new_columns.join(", ")}. They are parked until mapped and a new go-live is approved.`,
    r.missing_columns.length > 0 && `Missing columns: ${r.missing_columns.join(", ")}. The delivery was held.`,
    r.type_changes.length > 0 && `Type changes: ${r.type_changes.map((t) => `${t.column} ${t.was} to ${t.now}`).join("; ")}.`,
    r.order_changed && "The column order changed. This is information only: columns are matched by name.",
    r.rule_rises.length > 0 && `Rules flagging more rows than in the last released load: ${r.rule_rises.map((x) => `${x.rule_key} (${(x.was * 100).toFixed(1)}% to ${(x.now * 100).toFixed(1)}%)`).join("; ")}.`,
  ].filter((x): x is string => typeof x === "string");

  return (
    <div data-testid="drift">
      {error && <Text role="alert" className="bad">{error}</Text>}
      <ul data-testid="drift-report" style={{ margin: "0 0 8px", paddingLeft: 18 }}>
        {lines.map((l) => <li key={l}>{l}</li>)}
      </ul>
      {view.actions.length === 0 && <Text className="muted">No action was proposed.</Text>}
      <ul data-testid="drift-actions" style={{ listStyle: "none", padding: 0, margin: 0 }}>
        {view.actions.map((a) => (
          <li key={a.proposal_id} data-testid={`drift-action-${a.proposal_id}`}
              style={{ border: "1px solid var(--amz-line)", borderRadius: 6, padding: "6px 12px", margin: "6px 0" }}>
            <strong>{a.action.replaceAll("_", " ")}{a.columns.length > 0 ? `: ${a.columns.join(", ")}` : ""}</strong>{" "}
            <span className="muted">({who(a)}; {a.status === "pending" ? "waiting for you" : a.status === "approved" ? "selected" : a.status})</span>
            <Text size="sm">{a.reason}</Text>
            {a.status === "pending" && canAct && rejecting !== a.proposal_id && (
              <div style={{ display: "flex", gap: 8, marginTop: 4 }}>
                <Button size="xs" onClick={() => void decide(a, "approved", null)}>Select</Button>
                <Button size="xs" variant="default" onClick={() => setRejecting(a.proposal_id)}>Reject</Button>
              </div>
            )}
            {rejecting === a.proposal_id && (
              <div style={{ marginTop: 4 }}>
                <Textarea label="Why it is rejected" rows={2} value={note} onChange={(e) => setNote(e.currentTarget.value)} />
                <Button size="xs" mt="xs" disabled={!note.trim()} onClick={() => void decide(a, "rejected", note.trim())}>Confirm</Button>{" "}
                <Button size="xs" mt="xs" variant="default" onClick={() => setRejecting(null)}>Cancel</Button>
              </div>
            )}
          </li>
        ))}
      </ul>
      {canAct && !adding && <Button size="xs" mt="xs" variant="default" onClick={() => setAdding(true)}>Add action</Button>}
      {adding && (
        <div role="group" aria-label="Add action" style={{ marginTop: 8 }}>
          <NativeSelect label="Action" value={action} w={280} data={ACTIONS} onChange={(e) => setAction(e.currentTarget.value)} />
          <label>Column <input aria-label="Column" value={column} onChange={(e) => setColumn(e.currentTarget.value)} /></label>
          <Textarea label="Why" rows={2} value={reason} onChange={(e) => setReason(e.currentTarget.value)} />
          <Button size="xs" mt="xs" disabled={!reason.trim()} onClick={() => void add()}>Add item</Button>{" "}
          <Button size="xs" mt="xs" variant="default" onClick={() => setAdding(false)}>Cancel</Button>
        </div>
      )}
    </div>
  );
}
