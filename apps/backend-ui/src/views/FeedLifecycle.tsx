import { Alert, Button, Text, TextInput, Textarea } from "@mantine/core";
import { useState } from "react";
import { deleteFeed, describeError, pauseFeed, resumeFeed, type Feed, type FeedDeleted, type User } from "../api";

type Action = "pause" | "resume" | "delete";

const TITLES: Record<Action, string> = { pause: "Pause", resume: "Resume", delete: "Delete" };
const REASON_LABELS: Record<Action, string> = { pause: "Why it is paused", resume: "Why it is resumed", delete: "Why it is deleted" };
const GO_LABELS: Record<Action, string> = { pause: "Pause feed", resume: "Resume feed", delete: "Delete feed" };
const EXPLAIN: Record<Action, string> = {
  pause: "A paused feed is not fetched by the daily run. Its product stays, marked paused, with its last release.",
  resume: "The next daily run fetches the feed normally. Nothing is fetched for the days it was paused.",
  delete: "This removes the feed, its loads, its configuration and its product for good. Glossary elements stay. "
    + "Requests that used it go back to looking for a source. This cannot be undone.",
};

// Pause, Resume and Delete of one feed (phase 7.9). Only the feed's owner or a user with the operations role sees the
// controls (the API checks it again). Each action opens a small dialog: a reason, and for Delete the feed's code
// typed back. A refusal by the API is shown in the dialog, which stays open with what was typed.
export default function FeedLifecycle({ feed, me, devUser, onChanged, onDeleted }: {
  feed: Pick<Feed, "id" | "code" | "stage" | "owner_id">; me: User | null; devUser: string;
  onChanged: () => void; onDeleted: (deleted: FeedDeleted) => void;
}) {
  const [open, setOpen] = useState<Action | null>(null);
  const [reason, setReason] = useState("");
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const mayAct = !!me && (me.roles.includes("operations") || (feed.owner_id !== null && feed.owner_id === me.id));
  if (!mayAct) return null;

  const close = () => { setOpen(null); setReason(""); setTyped(""); setError(null); };
  const ready = reason.trim() !== "" && (open !== "delete" || typed === feed.code);

  const go = async () => {
    if (!open) return;
    setBusy(true);
    setError(null);
    try {
      if (open === "pause") await pauseFeed(feed.id, reason.trim(), devUser);
      else if (open === "resume") await resumeFeed(feed.id, reason.trim(), devUser);
      else {
        const deleted = await deleteFeed(feed.id, reason.trim(), typed, devUser);
        close();
        onDeleted(deleted);
        return;
      }
      close();
      onChanged();
    } catch (err: unknown) {
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div data-testid="feed-lifecycle">
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        {feed.stage === "registered" && <Button size="xs" variant="default" onClick={() => setOpen("pause")}>Pause</Button>}
        {feed.stage === "paused" && <Button size="xs" variant="default" onClick={() => setOpen("resume")}>Resume</Button>}
        <Button size="xs" variant="default" onClick={() => setOpen("delete")}>Delete</Button>
      </div>
      {open && (
        <section role="dialog" aria-label={`${TITLES[open]} ${feed.code}`} data-testid="feed-lifecycle-dialog"
                 style={{ border: "1px solid var(--amz-line)", borderRadius: 6, padding: 12, marginTop: 8,
                          background: "var(--amz-panel)", maxWidth: 520 }}>
          <Text fw={600}>{`${TITLES[open]} ${feed.code}`}</Text>
          <Text size="sm" my="xs">{EXPLAIN[open]}</Text>
          <Textarea label={REASON_LABELS[open]} value={reason} rows={2} maxLength={500}
                    onChange={(e) => setReason(e.currentTarget.value)} />
          {open === "delete" && (
            <TextInput mt="xs" label={`Type ${feed.code} to confirm`} value={typed} autoComplete="off"
                       onChange={(e) => setTyped(e.currentTarget.value)} />
          )}
          {error && <Alert role="alert" color="red" mt="xs">{error}</Alert>}
          <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
            <Button size="xs" color={open === "delete" ? "red" : undefined} disabled={!ready || busy} onClick={() => void go()}>
              {GO_LABELS[open]}
            </Button>
            <Button size="xs" variant="default" disabled={busy} onClick={close}>Cancel</Button>
          </div>
        </section>
      )}
    </div>
  );
}
