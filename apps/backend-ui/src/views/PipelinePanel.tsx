import { Button, Text } from "@mantine/core";
import { useCallback, useEffect, useState } from "react";
import { formatWhen } from "../theme";
import { describeError, getCodePreview, getPipelineRuns, runPipeline, type PipelineOutcome, type PipelineRun } from "../api";

const RESULT: Record<PipelineOutcome, string> = {
  passed: "Passed",
  failed_at_commit: "Failed at commit",
  failed_at_sql_parse: "Failed at SQL parse",
  failed_at_rule_check: "Failed at rule check",
  failed_at_test_load: "Failed at test load",
};
const STAGE_WORDS: Record<string, string> = { commit: "commit", sql_parse: "SQL parse", rule_check: "rule check", test_load: "test load" };

// The pipeline runs of one version of a feed's generated code (phase 7.17): the result in words, each stage with its log, and
// the commit hash. `sha256` is the code shown; leave it out and the code of the configuration now is used. `canRun` offers
// "Run pipeline" for that code: a go-live needs a passed run of the code it pins.
export default function PipelinePanel({ feedId, devUser, sha256, canRun }: { feedId: string; devUser: string; sha256?: string; canRun: boolean }) {
  const [runs, setRuns] = useState<PipelineRun[] | null>(null);
  const [shown, setShown] = useState<string | null>(sha256 ?? null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const [list, now] = await Promise.all([getPipelineRuns(feedId, devUser), sha256 ? Promise.resolve(null) : getCodePreview(feedId, devUser)]);
    setShown(sha256 ?? now?.sha256 ?? null);
    setRuns(list);
  }, [feedId, devUser, sha256]);

  useEffect(() => {
    let live = true;
    load().catch((err: unknown) => live && setError(describeError(err)));
    return () => { live = false; };
  }, [load]);

  const run = async () => {
    setRunning(true);
    setError(null);
    try {
      const made = await runPipeline(feedId, devUser);
      setRuns((before) => [made, ...(before ?? [])]);
    } catch (err) {
      setError(describeError(err));
    } finally {
      setRunning(false);
    }
  };

  const mine = (runs ?? []).filter((r) => r.code_sha256 === shown);
  const latest = mine[0];
  return (
    <section aria-label="Pipeline" data-testid="pipeline-panel" style={{ marginTop: 12 }}>
      <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
        <Text fw={600}>Pipeline</Text>
        {canRun && <Button type="button" size="xs" disabled={running} onClick={() => void run()}>Run pipeline</Button>}
        {latest && <span role="status">{`Latest result: ${RESULT[latest.outcome]}${latest.outcome === "passed" ? " (a go-live of this code can be approved)" : ""}`}</span>}
      </div>
      {error && <Text role="alert" className="bad">{error}</Text>}
      {runs && mine.length === 0 && <Text size="sm" className="muted">This code has not been run through the pipeline.</Text>}
      {mine.map((r) => (
        <div key={r.id} data-testid="pipeline-run" style={{ margin: "8px 0", padding: 8, border: "1px solid var(--amz-border)", borderRadius: 4 }}>
          <Text size="sm" fw={600}>
            {`${r.outcome === "passed" ? "✔" : "✖"} ${RESULT[r.outcome]}`}
            <span className="muted" style={{ fontWeight: 400 }}>{` by ${r.run_by}, ${formatWhen(r.created_at, { time: true })}`}</span>
          </Text>
          {r.commit_hash && (
            <Text size="sm">Commit <code title={r.commit_hash}>{r.commit_hash.slice(0, 10)}</code>{r.commit_created ? "" : " (already in the repository)"}</Text>
          )}
          <ul style={{ margin: "4px 0", paddingLeft: 18 }}>
            {r.stages.map((s) => (
              <li key={s.stage}>
                {`${STAGE_WORDS[s.stage]}: ${s.status}`}
                <ul style={{ margin: 0, paddingLeft: 16 }}>{s.log.map((line, i) => <li key={i} className="muted">{line}</li>)}</ul>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </section>
  );
}
