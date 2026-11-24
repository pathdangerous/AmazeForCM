import { Alert, Button, Text, Title } from "@mantine/core";
import { useEffect, useState } from "react";
import { StatusChip } from "../../../ui-shared/phase711";
import { approveConfident, describeError, getChain, type ChainReview as Chain, type StepName } from "../api";
import { StateBadge, STEP_LABELS } from "./Stepper";

// Phase 7.13, T2: every draft of the chain, by step. Blocked items (no single recommended value) first, then the
// low-confidence ones. Each says what it was built on; "Based on" opens that step. Approve, edit, reject and add are
// the step's own panel (Open the step); "Approve all confident" is one approval per item, by the person who clicks.
export default function ChainReview({ id, devUser, stamp, onOpenStep, onChanged }: {
  id: string; devUser: string; stamp: string; onOpenStep: (step: StepName) => void; onChanged: () => void;
}) {
  const [chain, setChain] = useState<Chain | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let current = true;
    getChain(id, devUser).then((c) => current && setChain(c)).catch((err: unknown) => current && setNote(describeError(err)));
    return () => { current = false; };
  }, [id, devUser, stamp]);

  if (!chain) return note ? <Alert role="alert" color="red">{note}</Alert> : null;

  const approveAll = async (step: StepName) => {
    setBusy(true);
    setNote(null);
    try {
      const done = await approveConfident(id, step, devUser);
      setChain(done.chain);
      setNote(`Approved ${done.approved.length} item${done.approved.length === 1 ? "" : "s"} of ${STEP_LABELS[step]}`);
      onChanged();
    } catch (err: unknown) {
      setNote(describeError(err));
    } finally {
      setBusy(false);
    }
  };
  const blocked = Object.entries(chain.blocked);

  return (
    <section data-testid="chain-review" aria-label="Chain review" style={{ marginBottom: 16 }}>
      <Title order={3} size="h4">Chain review</Title>
      <Text size="sm" c="dimmed" mb="xs">
        Every agent has drafted on the drafts before it. Approve top to bottom: an item waits for what it is based on.
      </Text>
      {blocked.length > 0 && (
        <div data-testid="chain-blocked" role="note" style={{ background: "var(--amz-warning)", color: "var(--amz-on-bright)",
                                                               borderRadius: 6, padding: "8px 12px", marginBottom: 8 }}>
          {`Not drafted past: ${blocked.map(([column, why]) => `${column} (${why})`).join("; ")}`}
        </div>
      )}
      {note && <Text role="status" size="sm" mb="xs">{note}</Text>}
      {chain.steps.map((step) => {
        const confident = step.items.filter((i) => i.confident).length;
        return (
          <div key={step.step} data-testid={`chain-step-${step.step}`} style={{ marginBottom: 12 }}>
            <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
              <Title order={4} size="h5">{STEP_LABELS[step.step]}</Title>
              <StateBadge state={step.state} />
              <div style={{ flex: 1 }} />
              <Button size="xs" variant="default" onClick={() => onOpenStep(step.step)}>Open the step</Button>
              <Button size="xs" data-testid={`chain-approve-confident-${step.step}`} disabled={busy || confident === 0}
                      onClick={() => void approveAll(step.step)}>
                {`Approve all confident (${confident})`}
              </Button>
            </div>
            <ul style={{ listStyle: "none", padding: 0, margin: "4px 0" }}>
              {step.items.map(({ item, based_on, blocked: isBlocked, low_confidence }) => (
                <li key={item.proposal_id} data-testid={`chain-item-${item.proposal_id}`}
                    style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", padding: "2px 0" }}>
                  {isBlocked && <StatusChip tone="attention">Blocked</StatusChip>}
                  {low_confidence && <StatusChip tone="attention">Low confidence</StatusChip>}
                  <span>{item.summary}</span>
                  <Text size="sm" c="dimmed" component="span">
                    {item.confidence === null ? "" : `${Math.round(item.confidence * 100)}%`} {item.status}
                  </Text>
                  {based_on.map((b) => (
                    <Button key={b.proposal_id} size="compact-xs" variant="subtle" onClick={() => onOpenStep(b.step)}
                            aria-label={`Based on ${STEP_LABELS[b.step]}: ${b.column ?? b.proposal_id}`}>
                      {`based on ${b.column ?? STEP_LABELS[b.step]} (${b.status})`}
                    </Button>
                  ))}
                </li>
              ))}
            </ul>
          </div>
        );
      })}
    </section>
  );
}
