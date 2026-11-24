import { Badge, Table, Text, Title } from "@mantine/core";
import { useEffect, useState } from "react";
import { apiGet, describeError, words, type AgentSettingsT } from "../api";

// Phase 7.18: read only. Each agent, who answers it (the model provider, or the in-house external agent), and whether
// EXTERNAL_AGENT_FOR is set. The API sends selector values and agent names only, never a key or a URL.

function externalLine(s: AgentSettingsT): { text: string; warn: boolean } {
  if (!s.external_agent_set) return { text: "EXTERNAL_AGENT_FOR is not set: every agent is answered by the model provider.", warn: false };
  if (!s.external_agent_known) {
    return { text: `EXTERNAL_AGENT_FOR is ${s.external_agent_for}: no agent with that name, so nothing is routed.`, warn: true };
  }
  if (!s.external_agent_active) {
    return { text: `EXTERNAL_AGENT_FOR is ${s.external_agent_for} but the external provider is none: not routed.`, warn: true };
  }
  return { text: `EXTERNAL_AGENT_FOR is ${s.external_agent_for}: that agent is answered by the external agent (${s.external_agent_provider}).`, warn: false };
}

export default function SettingsView({ devUser = "" }: { devUser?: string }) {
  const [settings, setSettings] = useState<AgentSettingsT | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!devUser) return;
    apiGet<AgentSettingsT>("/api/amaze/settings", devUser)
      .then((s) => { setSettings(s); setError(null); })
      .catch((e: unknown) => setError(describeError(e)));
  }, [devUser]);

  const line = settings ? externalLine(settings) : null;
  return (
    <section>
      <Title order={2} mb="md">Settings</Title>
      {error && <Text role="alert" className="error">{error}</Text>}
      {settings && line && (
        <>
          <p data-testid="external-agent">
            {line.warn && <Badge tt="none" mr="xs" style={{ background: "#FFEDD5", color: "#9A3412" }}>check</Badge>}
            {line.text}
          </p>
          <Table>
            <Table.Thead>
              <Table.Tr><Table.Th>Agent</Table.Th><Table.Th>Autonomy</Table.Th><Table.Th>Approved by</Table.Th><Table.Th>Provider</Table.Th></Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {settings.agents.map((a) => (
                <Table.Tr key={a.name} data-testid={`agent-${a.name}`}>
                  <Table.Td>{a.name}</Table.Td>
                  <Table.Td>{a.autonomy}</Table.Td>
                  <Table.Td>{a.approver_roles.length ? a.approver_roles.map(words).join(", then ") : "read only"}</Table.Td>
                  <Table.Td>{a.provider}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </>
      )}
    </section>
  );
}
