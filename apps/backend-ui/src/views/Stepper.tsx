import { Badge } from "@mantine/core";
import { useRef } from "react";
import type { StepName, StepState, StepView } from "../api";

export const STEP_LABELS: Record<StepName, string> = {
  upload: "Upload", profile: "Profile", glossary: "Glossary", mapping: "Mapping", transformations: "Transformations",
  rules: "Rules", go_live: "Go-live", first_load: "First load", product_ready: "Product ready",
};

export const STATE_LABELS: Record<StepState, string> = {
  waiting: "waiting", agent_working: "waiting for agent", needs_decision: "needs decision", ready: "ready",
  done: "done", stale: "stale", failed: "failed",
};

// Warning, info and the marker are fills with dark text, never text colours on the panel (phase 7.5 look).
const FILLS: Record<StepState, { bg: string; fg: string }> = {
  waiting: { bg: "var(--amz-subtle)", fg: "var(--amz-text)" },
  agent_working: { bg: "var(--amz-info)", fg: "var(--amz-on-bright)" },
  needs_decision: { bg: "var(--amz-warning)", fg: "var(--amz-on-bright)" },
  ready: { bg: "var(--amz-primary)", fg: "var(--amz-on-fill)" },
  done: { bg: "var(--amz-success)", fg: "var(--amz-on-fill)" },
  stale: { bg: "var(--amz-marker)", fg: "var(--amz-on-bright)" },
  failed: { bg: "var(--amz-danger)", fg: "var(--amz-on-fill)" },
};

export function StateBadge({ state, testId }: { state: StepState; testId?: string }) {
  const fill = FILLS[state];
  return (
    <Badge tt="none" data-testid={testId} style={{ backgroundColor: fill.bg, color: fill.fg }}>{STATE_LABELS[state]}</Badge>
  );
}

// A step can be opened when it is done, current or stale; the later ones are waiting for the earlier ones.
export function canOpen(step: StepView, current: StepName): boolean {
  return step.name === current || step.state === "done" || step.state === "stale";
}

export default function Stepper({ steps, current, viewed, onOpen }: {
  steps: StepView[]; current: StepName; viewed: StepName; onOpen: (step: StepName) => void;
}) {
  const refs = useRef<Record<string, HTMLButtonElement | null>>({});
  const enabled = steps.filter((s) => canOpen(s, current)).map((s) => s.name);

  const move = (from: StepName, key: string): boolean => {
    const at = enabled.indexOf(from);
    let target: StepName | undefined;
    if (key === "ArrowDown") target = enabled[Math.min(at + 1, enabled.length - 1)];
    else if (key === "ArrowUp") target = enabled[Math.max(at - 1, 0)];
    else if (key === "Home") target = enabled[0];
    else if (key === "End") target = enabled[enabled.length - 1];
    else return false;
    if (target) refs.current[target]?.focus();
    return true;
  };

  return (
    <nav aria-label="Onboarding steps" className="stepper">
      <ol style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 4 }}>
        {steps.map((step, i) => {
          const open = canOpen(step, current);
          return (
            <li key={step.name}>
              <button
                type="button" ref={(el) => { refs.current[step.name] = el; }} disabled={!open}
                data-testid={`step-${step.name}`}
                aria-current={step.name === current ? "step" : undefined}
                data-viewed={step.name === viewed ? "true" : undefined}
                onClick={() => onOpen(step.name)}
                onKeyDown={(e) => {
                  if (move(step.name, e.key)) e.preventDefault();
                  else if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    onOpen(step.name);
                  }
                }}
                style={{
                  width: "100%", display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8,
                  textAlign: "left", padding: "8px 10px", borderRadius: 6, cursor: open ? "pointer" : "default",
                  border: "1px solid var(--amz-line)", color: "var(--amz-text)",
                  background: step.name === viewed ? "var(--amz-subtle)" : "var(--amz-panel)",
                  borderLeft: step.name === current ? "4px solid var(--amz-marker)" : "1px solid var(--amz-line)",
                  opacity: open ? 1 : 0.75, font: "inherit",
                }}
              >
                <span>{i + 1}. {STEP_LABELS[step.name]}</span>
                <StateBadge state={step.state} />
              </button>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
