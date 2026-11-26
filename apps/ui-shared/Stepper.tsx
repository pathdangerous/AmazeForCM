import { useRef } from "react";
import type { StepName, StepState, StepView } from "../backend-ui/src/api";
import { StatusChip } from "./phase711";

export const STEP_LABELS: Record<StepName, string> = {
  upload: "Upload", profile: "Profile", mapping: "Mapping", glossary: "Glossary requests", transformations: "Transformations",
  rules: "Rules", go_live: "Go-live", first_load: "First load", product_ready: "Product ready",
};
export const STATE_LABELS: Record<StepState, string> = {
  waiting: "waiting", agent_working: "waiting for agent", needs_decision: "needs decision", ready: "ready",
  done: "done", stale: "stale", failed: "failed",
};
export function StateBadge({ state, testId }: { state: StepState; testId?: string }) {
  return <span data-testid={testId}><StatusChip tone={state === "done" || state === "ready" ? "good"
    : state === "needs_decision" || state === "stale" || state === "failed" ? "attention" : "neutral"}>{STATE_LABELS[state]}</StatusChip></span>;
}
export function canOpen(step: StepView, current: StepName) {
  // phase 7.13: a step drafted ahead of the cursor (chain mode) opens too; in step mode only the current one has these
  return step.name === current || step.state === "done" || step.state === "stale" || step.state === "ready"
    || step.state === "needs_decision";
}
export default function Stepper({ steps, current, viewed, onOpen }: {
  steps: StepView[]; current: StepName; viewed: StepName; onOpen: (step: StepName) => void;
}) {
  const refs = useRef<Record<string, HTMLButtonElement | null>>({});
  const enabled = steps.filter((step) => canOpen(step, current)).map((step) => step.name);
  return <nav aria-label="Onboarding steps" className="stepper"><ol>
    {steps.map((step, index) => <li key={step.name}>
      <button type="button" ref={(el) => { refs.current[step.name] = el; }} disabled={!canOpen(step, current)}
        data-testid={`step-${step.name}`} data-viewed={step.name === viewed ? "true" : undefined}
        data-done={step.state === "done" ? "true" : undefined} aria-current={step.name === current ? "step" : undefined}
        onClick={() => onOpen(step.name)} onKeyDown={(event) => {
          const position = enabled.indexOf(step.name);
          let target: StepName | undefined;
          if (event.key === "ArrowRight" || event.key === "ArrowDown") target = enabled[Math.min(position + 1, enabled.length - 1)];
          else if (event.key === "ArrowLeft" || event.key === "ArrowUp") target = enabled[Math.max(position - 1, 0)];
          else if (event.key === "Home") target = enabled[0];
          else if (event.key === "End") target = enabled[enabled.length - 1];
          else if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            onOpen(step.name);
          }
          if (target) { event.preventDefault(); refs.current[target]?.focus(); }
        }}>
        <span className="stepper-bar" aria-hidden="true" />
        <span className="stepper-label"><span className="stepper-dot" aria-hidden="true">{index + 1}</span>{STEP_LABELS[step.name]}</span>
        <span className="stepper-state">{STATE_LABELS[step.state]}</span>
      </button>
    </li>)}
  </ol></nav>;
}
