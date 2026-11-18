import { Anchor, Button, Stack, Text } from "@mantine/core";
import { useEffect, useRef, useState } from "react";
import { apiGet, apiPost, describeError, type RunOnBanner, type RunsBanner } from "../api";

// Under the header on every view (phase 5 plan, section 10): what the glossary builder is doing, how many of its
// proposals wait for the glossary owner, and a failed run with Run again for those who may. It asks the API
// every 3 seconds while a run is active; otherwise only when the view changes or an action reports a change.

const POLL_MS = 3000;

function feedLabel(run: RunOnBanner): string {
  return `${run.feed_name} (${run.feed_code})`;
}

export default function RunBanner({ devUser, canRun, refresh, onChange }: {
  devUser: string; canRun: boolean; refresh: string; onChange: () => void;
}) {
  const [banner, setBanner] = useState<RunsBanner | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (!devUser) {
      setBanner(null);
      return;
    }
    let current = true;
    apiGet<RunsBanner>("/api/amaze/glossary/runs", devUser)
      .then((b) => current && (setBanner(b), setError(null)))
      .catch((err: unknown) => current && setError(describeError(err)));
    return () => {
      current = false;
    };
  }, [devUser, refresh, tick]);

  const active = banner?.active.length ?? 0;
  useEffect(() => {
    if (active === 0) return;
    const timer = window.setInterval(() => setTick((t) => t + 1), POLL_MS);
    return () => window.clearInterval(timer);
  }, [active]);

  // a run that ended means new proposals or a new state on the page: tell the views
  const wasActive = useRef(0);
  useEffect(() => {
    if (active < wasActive.current) onChange();
    wasActive.current = active;
  }, [active, onChange]);

  async function runAgain(feedId: string) {
    setBusy(true);
    try {
      await apiPost(`/api/amaze/feeds/${feedId}/glossary/run`, {}, devUser);
      setError(null);
      setTick((t) => t + 1);
    } catch (err: unknown) {
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  }

  if (!banner) return error ? <div className="detail run-banner bad" role="alert" data-testid="run-banner">{error}</div> : null;

  const running = banner.active.filter((r) => r.status === "running");
  const queued = banner.active.filter((r) => r.status === "queued");
  const activeFeeds = new Set(banner.active.map((r) => r.feed_id));
  // the newest finished run of each feed, if it failed and nothing is active for that feed
  const newest = new Map<string, RunOnBanner>();
  for (const r of banner.finished) if (!newest.has(r.feed_id)) newest.set(r.feed_id, r);
  const failed = [...newest.values()].filter((r) => r.status === "failed" && !activeFeeds.has(r.feed_id));

  if (banner.active.length === 0 && banner.waiting_proposals === 0 && failed.length === 0 && !error) return null;

  return (
    // phase 7.5: a panel with the info colour as a bar (a fill, never text: plan, assumption 2)
    <Stack gap={4} className="detail run-banner" role="status" aria-live="polite" data-testid="run-banner">
      {banner.active.length > 0 && (
        <div data-testid="run-banner-active">
          <strong>Glossary builder:</strong>{" "}
          {running.length > 0 ? <>running for {running.map(feedLabel).join(", ")}</> : <>not running</>}
          {queued.length > 0 && <>; {queued.length} waiting ({queued.map(feedLabel).join(", ")})</>}
        </div>
      )}
      {banner.waiting_proposals > 0 && (
        <div data-testid="run-banner-waiting">
          <Anchor href="#/glossary">
            {banner.waiting_proposals} {banner.waiting_proposals === 1 ? "proposal waits" : "proposals wait"} for the
            glossary owner
          </Anchor>
        </div>
      )}
      {failed.map((r) => (
        <div key={r.id} data-testid="run-banner-failed">
          <span className="bad">Glossary builder failed for {feedLabel(r)}: {r.error ?? "no reason recorded"}</span>{" "}
          {canRun && (
            <Button size="compact-xs" variant="default" disabled={busy} onClick={() => runAgain(r.feed_id)}
                    data-testid="run-again">
              Run again
            </Button>
          )}
        </div>
      ))}
      {error && <Text className="bad" role="alert">{error}</Text>}
    </Stack>
  );
}
