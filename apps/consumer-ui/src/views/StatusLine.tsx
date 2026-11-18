import { useEffect, useState } from "react";
import { apiGet, type Health } from "../api";

// The phase 0 status, on one line: API, database and any adapter that is still an Azure stub.
export default function StatusLine() {
  const [health, setHealth] = useState<Health | null>(null);
  const [reachable, setReachable] = useState<boolean | null>(null);

  useEffect(() => {
    apiGet<Health>("/api/health", undefined, [503])
      .then((h) => {
        setHealth(h);
        setReachable(true);
      })
      .catch(() => setReachable(false));
  }, []);

  const mark = (good: boolean | null, text: string) =>
    good === null ? <span>checking...</span> : <span className={good ? "ok" : "bad"}>{text}</span>;

  // phase 7.5: on a panel strip under the header bar (App); ok and bad are text on the panel
  return (
    <p className="status-line">
      API <span data-testid="api-status">{mark(reachable, reachable ? "ok" : "unreachable")}</span>
      {" · "}Database{" "}
      <span data-testid="db-status">
        {health ? mark(health.database === "ok", health.database) : mark(reachable === false ? false : null, "unknown")}
      </span>
      {" · "}Unavailable adapters{" "}
      <span data-testid="unavailable">
        {health ? mark(health.unavailable.length === 0, health.unavailable.join(", ") || "none") : "-"}
      </span>
    </p>
  );
}
