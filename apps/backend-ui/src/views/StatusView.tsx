import { useEffect, useState } from "react";
import { apiGet, type Health } from "../api";

function Status({ good, text }: { good: boolean | null; text: string }) {
  if (good === null) return <span>checking...</span>;
  return <span className={good ? "ok" : "bad"}>{text}</span>;
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
      <h2>Status</h2>
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
