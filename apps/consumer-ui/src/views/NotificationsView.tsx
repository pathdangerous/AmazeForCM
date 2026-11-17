import { useEffect, useState } from "react";
import { apiGet, describeError, type Notification } from "../api";

// The signed-in user's own notifications, newest 50 (phase 7 plan, assumption 37). Read only: no read state.
// "Ready: ..." and "Access restricted: ..." arrive here, and so does a delivery that was held.
export default function NotificationsView({ devUser }: { devUser: string }) {
  const [rows, setRows] = useState<Notification[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (!devUser) return;
    let current = true;
    setRows(null);
    apiGet<Notification[]>("/api/notifications", devUser)
      .then((list) => current && (setRows(list), setError(null)))
      .catch((err: unknown) => current && setError(describeError(err)));
    return () => {
      current = false;
    };
  }, [devUser, tick]);

  return (
    <section>
      <h2>Notifications</h2>
      <p>
        <button type="button" onClick={() => setTick((t) => t + 1)}>Refresh</button>{" "}
        <span className="muted">the latest 50 sent to you</span>
      </p>
      {error && <p className="bad" role="alert">{error}</p>}
      {rows !== null && rows.length === 0 && <p className="muted" data-testid="no-notifications">Nothing yet.</p>}
      {rows !== null && rows.length > 0 && (
        <ul className="hits" data-testid="notifications">
          {rows.map((n) => (
            <li key={n.id} data-testid="notification">
              <strong>{n.subject}</strong>{" "}
              <span className="muted">{new Date(n.created_at).toLocaleString()}</span>
              <div>{n.body}</div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
