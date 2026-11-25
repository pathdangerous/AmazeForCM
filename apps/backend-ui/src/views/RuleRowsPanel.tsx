import { Button, Drawer, Text, VisuallyHidden } from "@mantine/core";
import { useEffect, useState } from "react";
import { describeError, getRuleRows, type RuleRowsAnswer, type RuleRowsShown } from "../api";

// Phase 7.20: click a rule, see the data it checked. The rule's columns are marked in the header; in them a cell is
// green (the row passes), red (it fails) or amber (a waiver took the failure out of the flags). The fills carry dark
// text (theme.ts, CONTRAST_PAIRS), and each coloured cell says its word and shows a mark, so colour is never alone.
// Read only: the API runs no model and writes nothing.

type State = "passes" | "fails" | "waived";
const LOOK: Record<State, { fill: string; text: string; mark: string }> = {
  passes: { fill: "var(--amz-pass)", text: "var(--amz-on-pass)", mark: "✓" },
  fails: { fill: "var(--amz-fail)", text: "var(--amz-on-fail)", mark: "✗" },
  waived: { fill: "var(--amz-waived)", text: "var(--amz-on-bright)", mark: "~" },
};

function stateOf(row: RuleRowsShown["rows"][number]): State {
  return row.waived ? "waived" : row.passes ? "passes" : "fails";
}

function show(value: unknown): string {
  return value === null || value === undefined ? "—" : String(value);
}

function Legend() {
  return (
    <div aria-label="Colours" role="group" style={{ display: "flex", gap: 12, flexWrap: "wrap", margin: "8px 0" }}>
      {(Object.keys(LOOK) as State[]).map((state) => (
        <span key={state} style={{ background: LOOK[state].fill, color: LOOK[state].text, borderRadius: 4, padding: "2px 8px",
                                   fontSize: 12, fontWeight: 600 }}>
          <span aria-hidden="true">{LOOK[state].mark} </span>{state}
        </span>
      ))}
    </div>
  );
}

function Rows({ shown }: { shown: RuleRowsShown }) {
  return (
    <div style={{ overflowX: "auto" }} role="region" aria-label="Rows checked by the rule" tabIndex={0}>
      <table className="amz-grid">
        <thead>
          <tr>
            <th scope="col">Row</th>
            {shown.columns.map((c) => (
              <th key={c.key} scope="col" data-affected={c.affected ? "true" : undefined}
                  style={c.affected ? { background: "var(--amz-marker)", color: "var(--amz-on-bright)" } : undefined}>
                {c.key}{c.affected && <VisuallyHidden> (a column of the rule)</VisuallyHidden>}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {shown.rows.map((r) => {
            const state = stateOf(r);
            const look = LOOK[state];
            return (
              <tr key={r.row}>
                <td>{r.row}</td>
                {shown.columns.map((c) => c.affected ? (
                  <td key={c.key} data-state={state} style={{ backgroundColor: look.fill, color: look.text }}>
                    <VisuallyHidden>{state}: </VisuallyHidden><span aria-hidden="true">{look.mark} </span>{show(r.values[c.key])}
                  </td>
                ) : <td key={c.key}>{show(r.values[c.key])}</td>)}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function Panel({ ruleId, devUser }: { ruleId: string; devUser: string }) {
  const [failingOnly, setFailingOnly] = useState(false);
  const [page, setPage] = useState(1);
  const [answer, setAnswer] = useState<RuleRowsAnswer | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let current = true;
    getRuleRows(ruleId, failingOnly, page, devUser)
      .then((a) => { if (current) { setAnswer(a); setError(null); } })
      .catch((err: unknown) => { if (current) setError(describeError(err)); });
    return () => { current = false; };
  }, [ruleId, failingOnly, page, devUser]);

  if (error) return <Text role="alert" c="red">{error}</Text>;
  if (!answer) return <Text role="status">Loading the rows…</Text>;
  if (answer.outcome === "rule_not_found") return <Text role="status">This rule was not found.</Text>;
  if (answer.outcome === "no_file") return <Text role="status">{answer.reason}</Text>;
  if (answer.outcome === "sql_refused") return <Text role="status">{`The rule's SQL was refused: ${answer.reason}`}</Text>;

  const { counts } = answer;
  const pages = Math.max(1, Math.ceil(counts.shown / answer.size));
  return (
    <div data-testid="rule-rows">
      <Text>{answer.rule.description}</Text>
      <Text size="sm" mt={4} data-testid="rule-rows-counts">
        <span>{`${counts.failing} of ${counts.rows} rows fail`}</span>
        {counts.waived > 0 && <> · <span>{`${counts.waived} waived`}</span></>}
      </Text>
      <Text size="xs" c="dimmed">
        {answer.source === "draft" ? "Run on the feed's latest file, as a draft." : "From the feed's newest load; no rule was run."}
      </Text>
      <Legend />
      <label style={{ display: "inline-flex", gap: 6, alignItems: "center", margin: "4px 0 8px" }}>
        <input type="checkbox" checked={failingOnly} onChange={(e) => { setFailingOnly(e.currentTarget.checked); setPage(1); }} />
        Show failing rows only
      </label>
      {answer.rows.length === 0 ? <Text>No rows to show.</Text> : <Rows shown={answer} />}
      <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 8 }}>
        <Button size="xs" variant="default" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous page</Button>
        <Text size="sm">{`Page ${answer.page} of ${pages}`}</Text>
        <Button size="xs" variant="default" disabled={page >= pages} onClick={() => setPage(page + 1)}>Next page</Button>
      </div>
    </div>
  );
}

// The opener: a button on a rule (a card in the Rules step, a chain review item, a row of the feed page's rules). The
// drawer closes with Escape and gives the focus back to this button.
export default function RuleRowsButton({ ruleId, ruleKey, devUser, variant = "default" }: {
  ruleId: string; ruleKey: string; devUser: string; variant?: "default" | "subtle";
}) {
  const [opened, setOpened] = useState(false);
  return (
    <>
      <Button size="compact-xs" variant={variant} aria-label={`Show rows of ${ruleKey}`} onClick={() => setOpened(true)}>
        Show rows
      </Button>
      <Drawer opened={opened} onClose={() => setOpened(false)} position="right" size="xl" transitionProps={{ duration: 0 }}
              title={<code>{ruleKey}</code>}>
        {opened && <Panel ruleId={ruleId} devUser={devUser} />}
      </Drawer>
    </>
  );
}
