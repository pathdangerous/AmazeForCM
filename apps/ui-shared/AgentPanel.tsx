import { useId, useRef, useState } from "react";
import type { AgentNote, OnboardingItem, StepInputs, StepName, TimelineEntry } from "../backend-ui/src/api";
import { formatWhen } from "../backend-ui/src/theme";
import { STEP_LABELS } from "./Stepper";

function decisionOf(item: OnboardingItem) {
  return item.column && item.summary.startsWith(item.column)
    ? item.summary.slice(item.column.length).replace(/^[:\s]+/, "") : item.summary;
}
function inputLines(inputs: StepInputs | null) {
  if (!inputs) return [];
  const lines: string[] = [];
  if (inputs.row_count) lines.push(`Rows: ${inputs.row_count}`);
  if (inputs.column_count) lines.push(`Columns: ${inputs.column_count}`);
  if (inputs.columns.length) {
    const more = inputs.columns.length - 12;
    lines.push(`Column names: ${inputs.columns.slice(0, 12).join(", ")}${more > 0 ? `, and ${more} more` : ""}`);
  }
  if (inputs.issues.length) lines.push(`Issues found: ${inputs.issues.length}`);
  for (const [key, count] of Object.entries(inputs.candidate_counts)) if (count > 0) lines.push(`Candidates for ${key}: ${count}`);
  for (const fact of inputs.facts) lines.push(`${fact.label}: ${fact.value}`);
  return lines;
}

export default function AgentPanel({ step, items, notes, inputs, timeline, people }: {
  step: StepName; items: OnboardingItem[]; notes: AgentNote[]; inputs: StepInputs | null;
  timeline: TimelineEntry[] | null; people: Record<string, string>;
}) {
  const [tab, setTab] = useState("why");
  const id = useId();
  const tabs = useRef<Record<string, HTMLButtonElement | null>>({});
  const noteOf = new Map(notes.map((note) => [note.proposal_id, note]));
  const agents = [...new Map(notes.map((note) => [`${note.agent}/${note.model}/${note.prompt_version}`, note])).values()];
  const lines = inputLines(inputs);
  const empty = !notes.length && !items.length;
  const noAgent = step === "first_load" || step === "product_ready";
  const who = (person: string) => people[person] ?? person;
  return <aside data-testid="agent-panel" aria-label="What the agent did and why" className="agent-panel" tabIndex={0}>
    <div className="agent-heading"><span className="agent-mark" aria-hidden="true">AI</span>
      <div><h2>What the agent did and why</h2><span className="muted" style={{ fontSize: 12 }}>{STEP_LABELS[step]}</span></div>
    </div>
    <div role="tablist" aria-label="Agent details" className="agent-tabs">
      {(["why", "timeline"] as const).map((key) => <button type="button" role="tab" key={key}
        id={`${id}-${key}-tab`} aria-controls={`${id}-${key}`} aria-selected={tab === key} tabIndex={tab === key ? 0 : -1}
        ref={(el) => { tabs.current[key] = el; }} onClick={() => setTab(key)}
        onKeyDown={(event) => {
          if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
            event.preventDefault();
            const next = event.key === "Home" ? "why" : event.key === "End" ? "timeline" : key === "why" ? "timeline" : "why";
            setTab(next); tabs.current[next]?.focus();
          }
        }}>{key === "why" ? "Reasoning" : "Timeline"}</button>)}
    </div>
    <div role="tabpanel" id={`${id}-why`} aria-labelledby={`${id}-why-tab`} hidden={tab !== "why"}>
      {noAgent ? <p>No agent runs at this step: rules and the trust score are computed</p>
        : empty && <p>The agent has nothing to explain for this step yet.</p>}
      {agents.map((note) => <p className="agent-model" key={`${note.agent}/${note.model}/${note.prompt_version}`}>
        <strong>{note.agent}</strong> | model {note.model} | prompt {note.prompt_version}
      </p>)}
      {inputs && (inputs.row_count !== null || inputs.column_count !== null) && <div className="agent-facts">
        {inputs.row_count !== null && <div className="agent-fact">Rows<strong>{inputs.row_count}</strong></div>}
        {inputs.column_count !== null && <div className="agent-fact">Columns<strong>{inputs.column_count}</strong></div>}
      </div>}
      {items.length > 0 && <ul aria-label="Proposals and why" style={{ paddingLeft: 18 }}>
        {items.map((item) => <li key={item.proposal_id} style={{ marginBottom: 10 }}>
          <strong>{item.column ?? item.kind.replaceAll("_", " ")}</strong>: {decisionOf(item)}
          {item.confidence !== null && ` (${Math.round(item.confidence * 100)}%)`}
          {(item.reason ?? noteOf.get(item.proposal_id)?.reasoning) && <div className="muted">{item.reason ?? noteOf.get(item.proposal_id)?.reasoning}</div>}
        </li>)}
      </ul>}
      {(!empty || lines.length > 0) && <><h3>Inputs it saw</h3><ul aria-label="Inputs it saw" style={{ paddingLeft: 18 }}>
        {lines.length ? lines.map((line) => <li key={line}>{line}</li>) : <li>No recorded inputs for this step.</li>}
      </ul></>}
    </div>
    <div role="tabpanel" id={`${id}-timeline`} aria-labelledby={`${id}-timeline-tab`} hidden={tab !== "timeline"}>
      <ul data-testid="agent-timeline" style={{ paddingLeft: 18 }}>
        {!(timeline ?? []).length && <li>No events yet.</li>}
        {(timeline ?? []).map((entry, index) => <li key={`${entry.at}-${index}`}>
          {entry.title} | {who(entry.actor)} | {formatWhen(entry.at)}
          {entry.kind === "edit" && ` | edited by ${who(typeof entry.detail.edited_by === "string" ? entry.detail.edited_by : entry.actor)}`}
        </li>)}
      </ul>
    </div>
  </aside>;
}
