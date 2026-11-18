import { Badge, Text, Title } from "@mantine/core";
import { useEffect, useState } from "react";
import { apiGet, type Health } from "../api";

// Phase 7.5: a value as a success or danger badge; its words stay the API's own.
function Status({ good, text }: { good: boolean | null; text: string }) {
  if (good === null) return <Text span className="muted">checking...</Text>;
  return <Badge tt="none" color={good ? "var(--amz-success)" : "var(--amz-danger)"}>{text}</Badge>;
}

export default function StatusView() {
  const [health, setHealth] = useState<Health | null>(null);
  const [apiReachable, setApiReachable] = useState<boolean | null>(null);

  useEffect(() => {
    apiGet<Health>("/api/health", undefined, [503])
      .then((h) => {
        setHealth(h);
        setApiReachable(true);
      })
      .catch(() => setApiReachable(false));
  }, []);

  return (
    <section>
      <Title order={2} mb="md">Status</Title>
      <dl>
        <dt>API</dt>
        <dd data-testid="api-status">
          <Status good={apiReachable} text={apiReachable ? "ok" : "unreachable"} />
        </dd>
        <dt>Database</dt>
        <dd data-testid="db-status">
          {health ? (
            <Status good={health.database === "ok"} text={health.database} />
          ) : (
            <Status good={apiReachable === false ? false : null} text="unknown" />
          )}
        </dd>
        <dt>Adapters</dt>
        <dd data-testid="adapters">
          {health ? Object.entries(health.adapters).map(([k, v]) => `${k}: ${v}`).join(", ") : "-"}
        </dd>
        <dt>Unavailable</dt>
        <dd data-testid="unavailable">
          {health ? <Status good={health.unavailable.length === 0} text={health.unavailable.join(", ") || "none"} /> : "-"}
        </dd>
      </dl>
    </section>
  );
}
