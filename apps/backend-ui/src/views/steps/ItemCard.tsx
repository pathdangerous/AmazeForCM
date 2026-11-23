import { Button, NativeSelect, Text, Textarea, Title } from "@mantine/core";
import { useState, type ReactNode } from "react";
import type { OnboardingItem } from "../../api";
import EditForm, { isEditable } from "./EditForm";
import type { ItemActions } from "./shared";

export type Choice = { value: number | string; label: string };

// Warning, info and the marker are fills with dark text; the subtle fill carries the normal text colour.
function Tag({ id, text, fill }: { id: string; text: string; fill: string }) {
  const background = id === "pre_accepted" ? "#DBEAFE" : id === "needs_decision" ? "#FFEDD5" : fill;
  const color = id === "pre_accepted" ? "#1E3A8A" : id === "needs_decision" ? "#9A3412" : "var(--amz-text)";
  return (
    <span data-testid={`item-badge-${id}`} style={{
      background, color,
      borderRadius: 10, padding: "2px 10px", fontSize: 12, marginRight: 6, fontWeight: 600,
    }}>{text}</span>
  );
}

// One proposal of a step, with its decision controls shown once (task 7.3b, the developer's list, item 1):
// needs a decision: [choice] Approve, Reject, Edit; pre-accepted: the badge, Reject, Edit; decided: the decision only.
export default function ItemCard({ item, actions, choices, children }: {
  item: OnboardingItem; actions: ItemActions; choices?: Choice[]; children?: ReactNode;
}) {
  const [picked, setPicked] = useState("");
  const [rejecting, setRejecting] = useState(false); // Reject opens a note box; Confirm sends the rejection with it
  const [note, setNote] = useState("");
  const pending = item.status === "pending";
  const editing = actions.editing === item.proposal_id;
  const canAct = pending && !actions.readOnly && !item.waiting_for && !editing;
  const asks = canAct && !item.pre_accepted && choices !== undefined && choices.length > 0;
  const marked = actions.marked.includes(item.proposal_id);
  const choice = picked === "" || !choices ? undefined : choices[Number(picked)]?.value;

  return (
    <li data-testid={`item-${item.proposal_id}`} data-marked={marked ? "true" : undefined}
        style={{ listStyle: "none", border: `${marked ? 2 : 1}px solid ${marked ? "var(--amz-warning)" : "var(--amz-line)"}`,
                 borderRadius: 6, padding: "8px 12px", margin: "8px 0" }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8, flexWrap: "wrap", alignItems: "baseline" }}>
        <Title order={4} size="h5">{item.column ?? item.kind.replaceAll("_", " ")}</Title>
        <div>
          {pending && item.pre_accepted && <Tag id="pre_accepted" text="Pre-accepted" fill="var(--amz-info)" />}
          {pending && item.needs_decision && <Tag id="needs_decision" text="needs decision" fill="var(--amz-warning)" />}
          {item.origin === "manual" && <Tag id="manual" text={`added by ${actions.people?.[item.author ?? ""] ?? item.author}`} fill="var(--amz-subtle)" />}
          {item.edited && <Tag id="edited" text={item.edited_by ? `edited by ${item.edited_by}` : "edited"} fill="var(--amz-marker)" />}
          {!pending && <Tag id="status" text={item.status} fill="var(--amz-subtle)" />}
        </div>
      </div>
      {children}
      {!pending && item.decided_by && (item.status === "approved" || item.status === "rejected") && (
        <Text size="sm" mt="xs" data-testid="item-decider">
          {`${item.status} by ${actions.people?.[item.decided_by] ?? item.decided_by}`}
        </Text>
      )}
      {pending && item.waiting_for && <Text size="sm" mt="xs">{`Waiting for ${item.waiting_for}`}</Text>}
      {asks && (
        <NativeSelect mt="xs" w={300} label={`Choice for ${item.column ?? item.kind}`} value={picked}
                      onChange={(e) => setPicked(e.currentTarget.value)}
                      data={[{ value: "", label: "Choose an option" },
                             ...(choices ?? []).map((c, i) => ({ value: String(i), label: c.label }))]} />
      )}
      {canAct && (
        <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
          {!item.pre_accepted && (
            <Button size="xs" aria-label="Approve" disabled={actions.busy || (asks && choice === undefined)}
                    onClick={() => actions.decide(item, "approved", choice)}>Accept</Button>
          )}
          {!rejecting && (
            <Button size="xs" variant="default" disabled={actions.busy} onClick={() => setRejecting(true)}>Reject</Button>
          )}
          {isEditable(item) && !rejecting && (
            <Button size="xs" variant="default" disabled={actions.busy} onClick={() => actions.startEdit(item)}>Edit</Button>
          )}
        </div>
      )}
      {canAct && rejecting && (
        <div data-testid="reject-note" style={{ marginTop: 8 }}>
          <Textarea label="Why it is rejected" value={note} rows={2} maxLength={500}
                    onChange={(e) => setNote(e.currentTarget.value)} />
          <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
            <Button size="xs" disabled={actions.busy || !note.trim()}
                    onClick={() => { actions.decide(item, "rejected", undefined, note.trim()); setRejecting(false); setNote(""); }}>Confirm</Button>
            <Button size="xs" variant="default" onClick={() => { setRejecting(false); setNote(""); }}>Cancel</Button>
          </div>
        </div>
      )}
      {editing && (
        <EditForm item={item} error={actions.editError} onSave={(payload) => actions.saveEdit(item, payload)} onCancel={actions.cancelEdit} />
      )}
    </li>
  );
}
