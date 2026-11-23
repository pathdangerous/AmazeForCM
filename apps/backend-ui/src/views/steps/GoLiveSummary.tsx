import { Text, Title } from "@mantine/core";
import { ONBOARDING_STEPS, type Visibility } from "../../api";
import { STATE_LABELS, STEP_LABELS } from "../Stepper";
import ItemCard from "./ItemCard";
import { asList, asRecord, asText, type PanelProps } from "./shared";

// The summary page before go-live (task 7.3b): what was approved per step, the review summary, the test load, and the
// visibility chosen here (sent with Approve go-live only).
export default function GoLiveSummary({ ws, items, actions, visibility, onVisibility }: PanelProps & {
  visibility: Visibility; onVisibility: (v: Visibility) => void;
}) {
  const review = items.find((i) => i.kind === "review_summary");
  const p = asRecord(review?.payload);
  const facts = ws.inputs?.facts ?? [];
  const testLoad = facts.find((f) => f.label === "Test load");
  const counts = facts.filter((f) => f.label !== "Test load");
  const before = ONBOARDING_STEPS.slice(1, ONBOARDING_STEPS.indexOf("go_live"));
  const notes = (Array.isArray(p.notes) ? p.notes : []).map(asRecord); // T7: notes people added to the summary
  const highlights = asList(p.highlights);
  const risks = asList(p.risks);
  const expected = ws.inputs?.expected_score ?? null; // computed from the test load's records, not model text
  return (
    <div data-testid="summary-page">
      <Title order={3} size="h4" mb="xs">Summary before go-live</Title>
      <ul>
        {before.map((name) => {
          const state = ws.steps.find((s) => s.name === name)?.state;
          return state ? <li key={name}>{`${STEP_LABELS[name]}: ${STATE_LABELS[state]}`}</li> : null;
        })}
        {counts.map((f) => <li key={f.label}>{`${f.label}: ${f.value}`}</li>)}
      </ul>
      {ws.inputs?.completion && (
        <div data-testid="completion-report" style={{ marginBottom: 12 }}>
          <Text fw={600}>Completion report</Text>
          <ul>{ws.inputs.completion.words.map((w) => <li key={w}>{w}</li>)}</ul>
          {ws.inputs.completion.mappings.excluded_columns.length > 0 && (
            <Text size="sm">{`Left out: ${ws.inputs.completion.mappings.excluded_columns.join(", ")}`}</Text>
          )}
          <ul aria-label="Rules and their back-tests" style={{ margin: "4px 0" }}>
            {ws.inputs.completion.rules.current.map((r) => (
              <li key={r.rule_key}>
                {`${r.rule_key} (${r.made_by === "manual" ? "added by hand" : "agent"}): `}
                {r.backtest_status === "ran" ? `flagged ${r.flagged_rows} of ${r.total_rows} rows in its back-test`
                  : r.backtest_status === "not_back_tested" ? "not back-tested" : "no back-test stored"}
              </li>
            ))}
          </ul>
        </div>
      )}
      {review && <Text mb="xs">{asText(p.summary) || review.summary}</Text>}
      {notes.length > 0 && (
        <>
          <Text fw={600}>Notes</Text>
          <ul data-testid="summary-notes">
            {notes.map((n, i) => <li key={i}>{`${asText(n.author_name) || asText(n.author)}: ${asText(n.text)}`}</li>)}
          </ul>
        </>
      )}
      {highlights.length > 0 && (
        <>
          <Text fw={600}>Highlights</Text>
          <ul>{highlights.map((h) => <li key={h}>{h}</li>)}</ul>
        </>
      )}
      {(risks.length > 0 || expected) && (
        <>
          <Text fw={600}>Risks</Text>
          <ul>
            {expected && (
              <li data-testid="expected-score">
                {`Expected trust score ${expected.trust_score} from the test load (${expected.flagged_rows} of ${expected.rows} rows flagged): `}
                {expected.below_threshold
                  ? `below the release threshold ${expected.threshold}, so the first load would be held and its consumers warned.`
                  : `at or above the release threshold ${expected.threshold}, so the first load is expected to release.`}
              </li>
            )}
            {risks.map((r) => <li key={r}>{r}</li>)}
          </ul>
        </>
      )}
      {testLoad && <Text mb="xs">{`Test load: ${testLoad.value}`}</Text>}
      <ul style={{ padding: 0, margin: 0 }}>
        {items.map((item) => <ItemCard key={item.proposal_id} item={item} actions={actions} />)}
      </ul>
      <fieldset style={{ marginTop: 12, border: "1px solid var(--amz-line)", borderRadius: 6 }}>
        <legend>Visibility of the product</legend>
        <label style={{ display: "block" }}>
          <input type="radio" name="visibility" checked={visibility === "open"} disabled={actions.readOnly}
                 onChange={() => onVisibility("open")} /> Open to every user
        </label>
        <label style={{ display: "block" }}>
          <input type="radio" name="visibility" checked={visibility === "restricted"} disabled={actions.readOnly}
                 onChange={() => onVisibility("restricted")} /> Restricted to entitled users
        </label>
      </fieldset>
    </div>
  );
}
