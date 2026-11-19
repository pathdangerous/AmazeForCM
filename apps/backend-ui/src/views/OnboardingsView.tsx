import { Alert, Badge, Button, Loader, Table, Text, Title } from "@mantine/core";
import { useEffect, useState } from "react";
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

  useEffect(() => {
    if (!devUser) return;
    let current = true;
    getOnboardings(devUser)
      .then((list) => current && (setRows(list), setError(null)))
      .catch((err: unknown) => current && setError(describeError(err)));
    return () => { current = false; };
  }, [devUser]);

  return (
    <section>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
        <Title order={2}>Onboardings</Title>
        <Button component="a" href="#/onboard" size="lg" data-testid="onboard-a-file">Onboard a file</Button>
      </div>
      {error && <Alert role="alert" color="red" mt="md">{error}</Alert>}
      {!error && rows === null && <Loader size="sm" mt="md" aria-label="Loading onboardings" />}
      {rows !== null && rows.length === 0 && (
        <div className="empty" style={{ marginTop: 16 }}>No onboardings yet. Start with Onboard a file.</div>
      )}
      {rows !== null && rows.length > 0 && (
        <Table.ScrollContainer minWidth={560} mt="md">
          <Table>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Feed</Table.Th><Table.Th>Step</Table.Th><Table.Th>Status</Table.Th>
                <Table.Th>Progress</Table.Th><Table.Th>Updated</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {rows.map((row) => (
                <Table.Tr key={row.id} data-testid={`onboarding-row-${row.feed_code}`}>
                  <Table.Td>
                    <a href={`#/onboarding/${row.id}`}>{row.feed_name}</a>
                    <Text size="xs" c="dimmed">{row.feed_code}</Text>
                  </Table.Td>
                  <Table.Td>{STEP_LABELS[row.current_step]}</Table.Td>
                  <Table.Td><Badge tt="none" variant="default">{row.status}</Badge></Table.Td>
                  <Table.Td>{progress(row)}</Table.Td>
                  <Table.Td>{formatWhen(row.updated_at)}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      )}
    </section>
  );
}
