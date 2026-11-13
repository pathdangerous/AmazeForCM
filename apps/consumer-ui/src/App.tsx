import { useEffect, useState } from "react";
import { apiGet, type Health, type User } from "./api";

// The only lines that differ between backend-ui and consumer-ui.
const APP: User["app"] = "consumer";
const TITLE = "Amaze Marketplace";
const STORAGE_KEY = "amaze.consumer-ui.devUser";

function remembered(): string | null {
  try {
    return localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

function remember(userId: string): void {
  try {
    localStorage.setItem(STORAGE_KEY, userId);
  } catch {
    // storage blocked: the choice simply is not remembered
  }
}

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [apiReachable, setApiReachable] = useState<boolean | null>(null);
  const [users, setUsers] = useState<User[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [me, setMe] = useState<User | null>(null);
  const [meError, setMeError] = useState<string | null>(null);

  useEffect(() => {
    apiGet<Health>("/api/health", undefined, [503])
      .then((h) => {
        setHealth(h);
        setApiReachable(true);
      })
      .catch(() => setApiReachable(false));

    apiGet<User[]>(`/api/dev/users?app=${APP}`)
      .then((list) => {
        setUsers(list);
        const saved = remembered();
        setSelected(list.some((u) => u.id === saved) ? (saved as string) : (list[0]?.id ?? ""));
      })
      .catch(() => setUsers([]));
  }, []);

  useEffect(() => {
    if (!selected) return;
    remember(selected);
    apiGet<User>("/api/me", selected)
      .then((user) => {
        setMe(user);
        setMeError(null);
      })
      .catch((err: unknown) => {
        setMe(null);
        setMeError(err instanceof Error ? err.message : "Sign-in failed");
      });
  }, [selected]);

  const status = (good: boolean | null, text: string) =>
    good === null ? <span>checking...</span> : <span className={good ? "ok" : "bad"}>{text}</span>;

  return (
    <main>
      <h1>{TITLE}</h1>
      <p>Phase 0 skeleton. Synthetic data only.</p>
      <dl>
        <dt>API</dt>
        <dd data-testid="api-status">{status(apiReachable, apiReachable ? "ok" : "unreachable")}</dd>
        <dt>Database</dt>
        <dd data-testid="db-status">
          {health ? status(health.database === "ok", health.database) : status(apiReachable === false ? false : null, "unknown")}
        </dd>
        <dt>Adapters</dt>
        <dd data-testid="adapters">
          {health ? Object.entries(health.adapters).map(([k, v]) => `${k}: ${v}`).join(", ") : "-"}
        </dd>
        <dt>
          <label htmlFor="dev-user">Dev user</label>
        </dt>
        <dd>
          <select id="dev-user" data-testid="dev-user" value={selected} onChange={(e) => setSelected(e.target.value)}>
            {users.map((u) => (
              <option key={u.id} value={u.id}>
                {u.display_name} ({u.roles.join(", ")})
              </option>
            ))}
          </select>
        </dd>
        <dt>Signed in as</dt>
        <dd data-testid="signed-in-as">
          {me ? `${me.display_name} (${me.roles.join(", ")})` : meError ? <span className="bad">{meError}</span> : "-"}
        </dd>
      </dl>
    </main>
  );
}
