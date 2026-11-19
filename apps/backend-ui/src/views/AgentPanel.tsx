import { Tabs, Text, Title } from "@mantine/core";
import type { AgentNote, OnboardingItem, StepInputs, StepName, TimelineEntry } from "../api";
import { formatWhen } from "../theme";
import { STEP_LABELS } from "./Stepper";

const SHOWN_COLUMNS = 12;
const NO_AGENT_STEPS: StepName[] = ["first_load", "product_ready"]; // the load and the trust score are deterministic code

function percent(value: number): string {
  return `${Math.round(value * 100)}%`;
}

// "Ticker -> x" and "Ticker: new element x" already start with the column; the panel shows the column on its own.
function decisionOf(item: OnboardingItem): string {
  const column = item.column;
  if (column && item.summary.startsWith(column)) return item.summary.slice(column.length).replace(/^[:\s]+/, "");
  return item.summary;
}

// Each line of "Inputs it saw": counts, names and flags only; a count of nothing is left out.
function inputLines(inputs: StepInputs | null): string[] {
  if (!inputs) return [];
  const lines: string[] = [];
  if (inputs.row_count) lines.push(`Rows: ${inputs.row_count}`);
  if (inputs.column_count) lines.push(`Columns: ${inputs.column_count}`);
  if (inputs.columns.length > 0) {
    const more = inputs.columns.length - SHOWN_COLUMNS;
    lines.push(`Column names: ${inputs.columns.slice(0, SHOWN_COLUMNS).join(", ")}${more > 0 ? `, and ${more} more` : ""}`);
  }
  if (inputs.issues.length > 0) lines.push(`Issues found: ${inputs.issues.length}`);
  for (const [column, count] of Object.entries(inputs.candidate_counts)) {
    if (count > 0) lines.push(`Candidates for ${column}: ${count}`);
  }
  for (const fact of inputs.facts) lines.push(`${fact.label}: ${fact.value}`);
  return lines;
}

// "What the agent did and why": per proposal of the viewed step what was proposed and why, the logged call once, and
// the inputs the agent saw. Never sample values.
export default function AgentPanel({ step, items, notes, inputs, timeline, people }: {
  step: StepName; items: OnboardingItem[]; notes: AgentNote[]; inputs: StepInputs | null; timeline: TimelineEntry[] | null;
  people: Record<string, string>;
}) {
  const who = (id: string) => people[id] ?? id;
  const noteOf = new Map(notes.map((n) => [n.proposal_id, n]));
  const agents = [...new Map(notes.map((n) => [`${n.agent}/${n.model}/${n.prompt_version}`, n])).values()];
  const lines = inputLines(inputs);
  const empty = notes.length === 0 && items.length === 0;

  return (
    <aside data-testid="agent-panel" aria-label="What the agent did and why">
      <Title order={2} size="h4" mb="xs">What the agent did and why</Title>
      <Text size="sm" c="dimmed" mb="sm">{STEP_LABELS[step]}</Text>
      <Tabs defaultValue="why">
        <Tabs.List>
          <Tabs.Tab value="why">Reasoning</Tabs.Tab>
          <Tabs.Tab value="timeline">Timeline</Tabs.Tab>
        </Tabs.List>
        <Tabs.Panel value="why" pt="sm">
          {NO_AGENT_STEPS.includes(step)
            ? <Text size="sm">No agent runs at this step: rules and the trust score are computed</Text>
            : empty && <Text size="sm">The agent has nothing to explain for this step yet.</Text>}
          {agents.map((n) => (
            <Text size="sm" key={`${n.agent}/${n.model}/${n.prompt_version}`}>
              <strong>{n.agent}</strong> | model {n.model} | prompt {n.prompt_version}
            </Text>
          ))}
          {items.length > 0 && (
            <ul aria-label="Proposals and why" style={{ paddingLeft: 18 }}>
              {items.map((i) => {
                const why = i.reason ?? noteOf.get(i.proposal_id)?.reasoning;
                return (
                  <li key={i.proposal_id} style={{ marginBottom: 6 }}>
                    <strong>{i.column ?? i.kind.replaceAll("_", " ")}</strong>: {decisionOf(i)}
                    {i.confidence !== null && ` (${percent(i.confidence)})`}
                    {why && <div>{why}</div>}
                  </li>
                );
              })}
            </ul>
          )}
          {(!empty || lines.length > 0) && (
            <>
              <Title order={3} size="h5" mt="sm">Inputs it saw</Title>
              <ul aria-label="Inputs it saw" style={{ paddingLeft: 18 }}>
                {lines.length === 0 && <li>No recorded inputs for this step.</li>}
                {lines.map((line) => <li key={line}>{line}</li>)}
              </ul>
            </>
          )}
        </Tabs.Panel>
        <Tabs.Panel value="timeline" pt="sm">
          <ul data-testid="agent-timeline" style={{ paddingLeft: 18, margin: 0 }}>
            {(timeline ?? []).length === 0 && <li>No events yet.</li>}
            {(timeline ?? []).map((t, i) => {
              const editor = typeof t.detail.edited_by === "string" ? t.detail.edited_by : t.actor;
              return (
                <li key={`${t.at}-${i}`}>
                  {t.title} | {who(t.actor)} | {formatWhen(t.at)}
                  {t.kind === "edit" ? ` | edited by ${who(editor)}` : ""}
                </li>
              );
            })}
          </ul>
        </Tabs.Panel>
      </Tabs>
    </aside>
  );
}
