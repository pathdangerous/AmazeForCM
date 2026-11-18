import { Anchor, Button, Group, Text, Title } from "@mantine/core";
import { useCallback, useEffect, useState } from "react";
import { apiGet, describeError, type Notification } from "../api";
import { formatWhen } from "../theme";

// The signed-in user's own notifications, newest 50 (phase 7 plan, assumption 37). Read only: no read state.
// "Ready: ..." and "Access restricted: ..." arrive here, and so does a delivery that was held.
// Phase 7.5, P27 (plan, assumption 18): App polls the list every 15 s (and at once when the tab opens or the user
// changes); the tab counts the notices newer than this user's last look at it, kept in this browser (localStorage).
// P28 (assumption 16): a notice's link is a link only when it is one of this app's pages (#/requirement/<id>).

export const POLL_MS = 15_000;
const LINKS = "#/requirement/";
const seenKey = (userId: string) => `amaze.consumer-ui.notificationsSeen.${userId}`;

function readSeen(userId: string): number {
  try {
    return Number(localStorage.getItem(seenKey(userId)) ?? 0);
  } catch {
    return 0;
  }
}

function writeSeen(userId: string, at: number): void {
  try {
    localStorage.setItem(seenKey(userId), String(at));
  } catch {
    // storage blocked: the count starts again from every notice
  }
}

export function useInbox(devUser: string, viewing: boolean) {
  const [rows, setRows] = useState<Notification[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const [seen, setSeen] = useState(0);
  const refresh = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    setRows(null);
    setSeen(devUser ? readSeen(devUser) : 0);
  }, [devUser]);

  useEffect(() => {
    if (!devUser) return;
    let current = true;
    const load = () => apiGet<Notification[]>("/api/notifications", devUser)
      .then((list) => current && (setRows(list), setError(null)))
      .catch((err: unknown) => current && setError(describeError(err)));
    load();
    const timer = window.setInterval(load, POLL_MS);
    return () => {
      current = false;
      window.clearInterval(timer);
    };
  }, [devUser, viewing, tick]);

  const newest = rows?.reduce((max, n) => Math.max(max, Date.parse(n.created_at)), 0) ?? 0;
  useEffect(() => {
    if (viewing && devUser && newest > seen) {
      writeSeen(devUser, newest);
      setSeen(newest);
    }
  }, [viewing, devUser, newest, seen]);

  const unseen = viewing || !rows ? 0 : rows.filter((n) => Date.parse(n.created_at) > seen).length;
  return { rows, error, refresh, unseen };
}

export default function NotificationsView({ rows, error, onRefresh }: {
  rows: Notification[] | null; error: string | null; onRefresh: () => void;
}) {
  return (
    <section>
      <Title order={2} mb="md">Notifications</Title>
      <Group gap="sm" mb="sm">
        <Button size="xs" variant="default" onClick={onRefresh}>Refresh</Button>
        <span className="muted">the latest 50 sent to you; checked every 15 seconds</span>
      </Group>
      {error && <Text className="bad" role="alert">{error}</Text>}
      {rows !== null && rows.length === 0 && <Text className="muted" data-testid="no-notifications">Nothing yet.</Text>}
      {rows !== null && rows.length > 0 && (
        <ul className="hits" data-testid="notifications">
          {rows.map((n) => (
            <li key={n.id} data-testid="notification">
              {n.link?.startsWith(LINKS)
                ? <Anchor href={n.link} fw={600}>{n.subject}</Anchor>
                : <strong>{n.subject}</strong>}{" "}
              <span className="muted">{formatWhen(n.created_at)}</span>
              <div>{n.body}</div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
