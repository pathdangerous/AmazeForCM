import { Alert, Button, NativeSelect, Text, TextInput } from "@mantine/core";
import { useState } from "react";
import { describeError, eraseProfiles, profileAgain, type FeedPage } from "../api";
import { formatWhen } from "../theme";

// Phase 7.16: a feed's profile history and the recorded erase of its old profiles. The database decides what may go
// (the latest profile and any a registration, sign-off, glossary run, arrival, code version or report points to stay): the
// screen only shows the reason it was given. To erase: pick the profile to erase everything older than, say why, confirm.
// Only the feed's owner and operations see the form (the API checks it again). An erased profile stays in the list as
// "erased by X": the record outlives the rows.

const KEPT: Record<string, string> = {
  latest: "it is the latest", registration: "its registration", signoff: "a sign-off", glossary_run: "a glossary run",
  load_arrival: "a load's arrival", code_version: "a code version", report: "a report",
};

export default function ProfileHistory({ page, devUser, people, canErase, canProfile = false, onChange }: {
  page: FeedPage; devUser: string; people: Record<string, string>; canErase: boolean; canProfile?: boolean; onChange: () => void;
}) {
  const [again, setAgain] = useState<string | null>(null);
  const [profiling, setProfiling] = useState(false);
  const [againError, setAgainError] = useState<string | null>(null);
  async function profileThePageAgain() {
    setProfiling(true);
    setAgainError(null);
    try {
      const { result } = await profileAgain(page.feed.id, devUser);
      if (result.outcome === "profiled_again") {
        setAgain(`Profile ${result.profile.version} was made from the latest file.`);
        onChange();
      } else {
        setAgain(`${result.detail}${result.added.length ? ` New: ${result.added.join(", ")}.` : ""}${
          result.missing.length ? ` Missing: ${result.missing.join(", ")}.` : ""}`);
      }
    } catch (e: unknown) {
      setAgainError(describeError(e));
    } finally {
      setProfiling(false);
    }
  }
  const name = (id: string) => people[id] ?? id;
  const profiles = page.profiles;
  const erasures = page.erasures ?? [];
  const [cutoff, setCutoff] = useState<number | null>(null);
  const [reason, setReason] = useState("");
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  // every profile, present or erased, in version order
  const erased = new Map(erasures.flatMap((e) => e.erased_versions.map((v) => [v, e] as const)));
  const versions = [...new Set([...profiles.map((p) => p.version), ...erased.keys()])].sort((a, b) => a - b);

  // the cutoffs worth offering: a profile with an erasable one older than it
  const erasableBefore = (v: number) => profiles.filter((p) => p.version < v && !p.keep_reason);
  const choices = profiles.filter((p) => erasableBefore(p.version).length > 0);
  const chosen = cutoff !== null && choices.some((p) => p.version === cutoff) ? cutoff : (choices.at(-1)?.version ?? null);
  const going = chosen === null ? [] : erasableBefore(chosen);
  const staying = chosen === null ? [] : profiles.filter((p) => p.version < chosen && p.keep_reason);

  async function go() {
    if (chosen === null) return;
    setBusy(true);
    setError(null);
    try {
      const { result } = await eraseProfiles(page.feed.id, chosen, reason.trim(), devUser);
      setDone(result.outcome === "profiles_erased"
        ? `Erased profile ${result.erased_versions.join(", ")}. The erase is recorded.` : result.detail);
      setAsking(false);
      setReason("");
      onChange();
    } catch (e: unknown) {
      setError(describeError(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div data-testid="profile-history">
      <ul data-testid="history">
        {versions.map((v) => {
          const p = profiles.find((x) => x.version === v);
          const e = erased.get(v);
          return (
            <li key={`p${v}`} data-testid={`history-profile-${v}`}>
              {p ? (
                <>Profile {v}: {p.outcome.replaceAll("_", " ")} <span className="muted">{formatWhen(p.at)}</span>
                  {p.keep_reason && <span className="muted"> · kept: {KEPT[p.keep_reason] ?? p.keep_reason}</span>}</>
              ) : (
                <>Profile {v}: erased by {name(e!.by)} <span className="muted">{formatWhen(e!.at)}: {e!.reason}</span></>
              )}
            </li>
          );
        })}
        {page.signoffs.map((s) => (
          <li key={`s${s.version}`}>
            Sign-off {s.version}: {s.outcome.replaceAll("_", " ")} by {name(s.signed_off_by)}
            {[...s.gaps, ...s.reasons].length > 0 && <> — {[...s.gaps, ...s.reasons].join("; ")}</>}
          </li>
        ))}
      </ul>
      {canProfile && page.feed.stage === "registered" && (
        <div style={{ marginTop: 8 }}>
          <Button size="xs" variant="default" disabled={profiling} onClick={() => void profileThePageAgain()}>Profile again</Button>
          <Text className="muted" size="sm" mt={4} data-testid="profile-again-note">
            Profiles the latest file once more as a new profile version. No sign-off is made, and only a file with the
            feed's current columns is taken.
          </Text>
          {again && <Text size="sm" mt={4} data-testid="profile-again-result">{again}</Text>}
          {againError && <Text role="alert" className="bad" size="sm">{againError}</Text>}
        </div>
      )}
      {done && <Text data-testid="erase-result" mt="xs">{done}</Text>}
      {canErase && chosen === null && profiles.length > 0 && (
        <Text className="muted" size="sm" mt="xs" data-testid="erase-none">
          Nothing can be erased: every older profile is kept, or there is none. The latest profile is never erased.
        </Text>
      )}
      {canErase && chosen !== null && (
        <div data-testid="erase-form" style={{ marginTop: 12, maxWidth: 520 }}>
          <NativeSelect label="Erase profiles older than" value={chosen} disabled={busy}
                        onChange={(e) => { setCutoff(Number(e.currentTarget.value)); setAsking(false); }}
                        data={choices.map((p) => ({ value: String(p.version), label: `Profile ${p.version} (${formatWhen(p.at)})` }))} />
          <Text size="sm" mt="xs" data-testid="erase-preview">
            Would erase: {going.map((p) => `Profile ${p.version}`).join(", ")}.
            {staying.length > 0 && <> Kept: {staying.map((p) => `Profile ${p.version} (${KEPT[p.keep_reason!] ?? p.keep_reason})`).join(", ")}.</>}
          </Text>
          <TextInput label="Why it is erased" mt="xs" value={reason} maxLength={500} disabled={busy}
                     onChange={(e) => setReason(e.currentTarget.value)} />
          {error && <Text role="alert" className="bad" mt="xs">{error}</Text>}
          {!asking && (
            <Button mt="sm" size="xs" color="red" disabled={busy || reason.trim() === ""} onClick={() => setAsking(true)}>Erase</Button>
          )}
          {asking && (
            <Alert mt="sm" title="This cannot be undone" role="group" data-testid="erase-confirm">
              {going.map((p) => `Profile ${p.version}`).join(", ")} will be deleted for good; who erased them and why is kept.
              <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
                <Button size="xs" color="red" disabled={busy} onClick={() => void go()}>Confirm erase</Button>
                <Button size="xs" variant="default" disabled={busy} onClick={() => setAsking(false)}>Cancel</Button>
              </div>
            </Alert>
          )}
        </div>
      )}
    </div>
  );
}
