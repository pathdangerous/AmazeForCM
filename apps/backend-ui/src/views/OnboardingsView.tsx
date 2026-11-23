import { Alert, Button, Text, Title } from "@mantine/core";
import { useEffect, useState } from "react";
import DataGrid from "../../../ui-shared/DataGrid";
import { LoadingSkeleton, StatusChip } from "../../../ui-shared/phase711";
import { describeError, getOnboardings, ONBOARDING_STEPS, type OnboardingRow } from "../api";
import { formatWhen } from "../theme";
import { STEP_LABELS } from "./Stepper";

function progress(row: OnboardingRow): string {
  const done = ONBOARDING_STEPS.filter((s) => row.step_states[s] === "done").length;
  return `${Math.min(done + (row.status === "done" ? 0 : 1), ONBOARDING_STEPS.length)} of ${ONBOARDING_STEPS.length}`;
}

export default function OnboardingsView({ devUser }: { devUser: string }) {
  const [rows, setRows] = useState<OnboardingRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!devUser) return;
    let current = true;
    getOnboardings(devUser)
      .then((list) => current && (setRows(list), setError(null)))
      .catch((err: unknown) => current && setError(describeError(err)));
    return () => { current = false; };
  }, [devUser, attempt]);

  return (
    <section>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
        <Title order={2}>Onboardings</Title>
        <Button component="a" href="#/onboard" size="lg" data-testid="onboard-a-file">Onboard a file</Button>
      </div>
      {error && <><Alert role="alert" color="red" mt="md">{error}</Alert>
        <Button variant="default" mt="sm" onClick={() => setAttempt((value) => value + 1)}>Try again</Button></>}
      {!error && rows === null && <LoadingSkeleton label="Loading onboardings" />}
      {rows !== null && rows.length === 0 && (
        <div className="empty" style={{ marginTop: 16 }}>No onboardings yet. Start with Onboard a file.</div>
      )}
      {rows !== null && rows.length > 0 && (
        <div style={{ marginTop: 16 }}><DataGrid rows={rows} rowKey={(row) => row.id} aria-label="Onboardings"
          rowProps={(row) => ({ "data-testid": `onboarding-row-${row.feed_code}` })} columns={[
            { key: "feed", label: "Feed", value: (row) => row.feed_name, render: (row) => <>
              <a href={`#/onboarding/${row.id}`}>{row.feed_name}</a><Text size="xs" c="dimmed">{row.feed_code}</Text>
            </> },
            { key: "step", label: "Step", value: (row) => STEP_LABELS[row.current_step] },
            { key: "status", label: "Status", value: (row) => row.status, render: (row) => <StatusChip tone={row.status === "done" ? "good" : "neutral"}>{row.status}</StatusChip> },
            { key: "progress", label: "Progress", value: progress },
            { key: "updated", label: "Updated", value: (row) => row.updated_at, render: (row) => formatWhen(row.updated_at) },
          ]} /></div>
      )}
    </section>
  );
}
