import { useEffect, useState } from "react";
import { apiGet, apiPost, describeError, type FeedPage, type Owner, type SignoffOutcome } from "../api";

// One feed, in the order of the work (phase 4 plan, section 10): source and requirements, the latest file,
// the profile, the ProfilerAgent's summary, the sign-off, the registration, and the history.
// The four actions show only for the backend team; every other backend role reads.

const OUTCOMES: { value: SignoffOutcome; label: string }[] = [
  { value: "meets", label: "Meets" },
  { value: "meets_with_gaps", label: "Meets with gaps" },
  { value: "rejected", label: "Rejected" },
];

function pct(rate: number | null): string {
  return rate === null ? "–" : `${Math.round(rate * 1000) / 10}%`;
}

function lines(text: string): string[] {
  return text.split("\n").map((l) => l.trim()).filter(Boolean);
}

export default function FeedView({ feedId, devUser, canAct }: { feedId: string; devUser: string; canAct: boolean }) {
  const [page, setPage] = useState<FeedPage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [owners, setOwners] = useState<Owner[]>([]);
  const [outcome, setOutcome] = useState<SignoffOutcome | "">("");
  const [text, setText] = useState("");
  const [owner, setOwner] = useState("");
  const [visibility, setVisibility] = useState<"open" | "restricted">("open");

  useEffect(() => {
    if (!devUser) return;
    let current = true;
    apiGet<FeedPage>(`/api/amaze/feeds/${feedId}`, devUser)
      .then((p) => current && (setPage(p), setError(null)))
      .catch((err: unknown) => current && setError(describeError(err)));
    apiGet<Owner[]>("/api/amaze/owners", devUser).then((o) => current && setOwners(o)).catch(() => undefined);
    return () => {
      current = false;
    };
  }, [feedId, devUser]);

  async function act(action: string, body: unknown, describe: (r: { result?: { outcome: string } }) => string) {
    setBusy(true);
    setDone(null);
    try {
      const response = await apiPost<{ feed: FeedPage; result?: { outcome: string } }>(
        `/api/amaze/feeds/${feedId}/${action}`, body, devUser);
      setPage(response.feed);
      setError(null);
      setDone(describe(response));
      setOutcome("");
      setText("");
    } catch (err: unknown) {
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  }

  if (error && !page) return <p className="bad" role="alert">{error}</p>;
  if (!page) return <p className="muted">Loading…</p>;

  const { feed, profile } = page;
  const specOnly = profile?.outcome === "spec_only_low_confidence";
  const items = outcome === "rejected" ? "reasons" : "gaps";

  return (
    <section data-testid="feed-view">
      <p><a href="#/feeds">← Feed registry</a></p>
      <h2>
        <code>{feed.code}</code> {feed.name} <span className="muted">({feed.stage})</span>
      </h2>
      {done && <p className="ok" role="status" data-testid="feed-done">{done}</p>}
      {error && <p className="bad" role="alert">{error}</p>}

      <h3>Source</h3>
      {page.source ? (
        <dl>
          <dt>Provider</dt><dd>{page.source.name} — {page.source.provider}</dd>
          <dt>Licence</dt><dd>{page.source.conditions.licence}</dd>
          <dt>Permitted use</dt><dd>{page.source.conditions.permitted_use}</dd>
          <dt>Redistribution</dt><dd>{page.source.conditions.redistribution}</dd>
        </dl>
      ) : <p className="muted">No catalogue source.</p>}

      <h3>Requirements that select it</h3>
      {page.requirements.length === 0 ? <p className="muted">None now.</p> : (
        <div className="table-wrap">
          <table data-testid="feed-requirements">
            <thead><tr><th>Requirement</th><th>Status</th><th>How chosen</th><th>Conditions the requester accepted</th></tr></thead>
            <tbody>
              {page.requirements.map((r) => (
                <tr key={r.id}>
                  <td>{r.title}</td>
                  <td>{r.status}</td>
                  <td>{r.basis === "known_source" ? "Known source" : "Requester's pick"}</td>
                  <td>
                    {r.accepted_conditions ? (
                      <>
                        {r.accepted_conditions.licence}; {r.accepted_conditions.permitted_use}
                        {r.accepted_conditions.external_approvals.map((a) => <div key={a} className="muted">Holds: {a}</div>)}
                      </>
                    ) : <span className="muted">Known source: Amaze holds the agreement</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <h3>Latest file</h3>
      {page.last_file ? (
        <p>
          {page.last_file.outcome === "fetched" ? <code>{page.last_file.blob_ref}</code> : <span className="bad">{page.last_file.detail}</span>}
          {" "}<span className="muted">{new Date(page.last_file.created_at).toLocaleString()}</span>
        </p>
      ) : <p className="muted">No file yet.</p>}
      {canAct && feed.stage === "candidate" && page.last_file && (
        <button type="button" disabled={busy} data-testid="profile"
                onClick={() => act("profile", {}, (r) => `Profile written: ${r.result?.outcome.replaceAll("_", " ")}.`)}>
          Profile the latest file
        </button>
      )}

      {profile && (
        <>
          <h3>Profile {profile.version}</h3>
          {specOnly && (
            <p className="bad" role="note" data-testid="spec-only">
              Low confidence: spec only. {profile.detail} Only what the source declares is known.
            </p>
          )}
          <p className="muted">{profile.row_count ?? 0} rows · {profile.engine} · by {profile.profiled_by}</p>
          {profile.columns.length > 0 && (
            <div className="table-wrap">
              <table data-testid="profile-columns">
                <thead><tr><th>Column</th><th>Type</th><th>Null rate</th><th>Distinct</th><th>Declared as</th></tr></thead>
                <tbody>
                  {profile.columns.map((c) => (
                    <tr key={c.name}>
                      <td><code>{c.name}</code></td>
                      <td>{c.type}</td>
                      <td>{pct(c.null_rate)}</td>
                      <td>{c.distinct_count}</td>
                      <td>{profile.spec.columns[c.name] ?? <span className="muted">not declared</span>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {page.undeclared_columns.length > 0 && <p>Not declared by the source: {page.undeclared_columns.join(", ")}</p>}
          {page.missing_columns.length > 0 && <p className="bad">Declared but not in the file: {page.missing_columns.join(", ")}</p>}
          {profile.coverage.map((c) => (
            <div key={c.requirement_id} className="detail">
              <strong>{page.requirements.find((r) => r.id === c.requirement_id)?.title ?? "A requirement"}</strong>
              <ul>
                {c.items.map((i) => (
                  <li key={i.item}>
                    {i.item}: {!i.declared ? <span className="bad">not declared by the source</span>
                      : i.in_file === false ? <span className="bad">declared ({i.column}), not in the file</span>
                      : i.in_file === null ? <>declared ({i.column}); no file read</>
                      : <>in <code>{i.column}</code>, {pct(i.null_rate)} empty, {i.distinct_count} distinct</>}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </>
      )}

      {profile && (
        <>
          <h3>Summary</h3>
          {page.summary ? (
            <div className="detail" data-testid="summary">
              <div className="muted">ProfilerAgent's proposal ({page.summary_status}); it decides nothing</div>
              <p>{page.summary.summary}</p>
              <p>Recommends: <strong>{page.summary.recommended_outcome.replaceAll("_", " ")}</strong></p>
              {page.summary.gaps.length > 0 && <ul>{page.summary.gaps.map((g) => <li key={g}>Gap: {g}</li>)}</ul>}
              {page.summary.reasons.length > 0 && <ul>{page.summary.reasons.map((g) => <li key={g}>Reason: {g}</li>)}</ul>}
            </div>
          ) : <p className="muted">Not summarised yet.</p>}
          {canAct && page.next_action === "summarise" && (
            <button type="button" disabled={busy} data-testid="summarise"
                    onClick={() => act("profile/summary", {}, () => "Summary ready to sign off.")}>
              Summarise
            </button>
          )}
        </>
      )}

      {canAct && page.next_action === "sign_off" && page.summary && (
        <form className="detail" data-testid="signoff-form" onSubmit={(e) => {
          e.preventDefault();
          if (!outcome) return;
          act("signoff", { outcome, [items]: outcome === "meets" ? [] : lines(text) },
              (r) => `Signed off: ${r.result?.outcome.replaceAll("_", " ")}.`);
        }}>
          <h3>Sign off profile {profile?.version}</h3>
          <div role="radiogroup" aria-label="Outcome">
            {OUTCOMES.map((o) => (
              <label key={o.value} style={{ marginRight: 16 }}>
                <input type="radio" name="outcome" value={o.value} checked={outcome === o.value}
                       disabled={o.value === "meets" && specOnly} data-testid={`outcome-${o.value}`}
                       onChange={() => setOutcome(o.value)} /> {o.label}
              </label>
            ))}
          </div>
          {specOnly && <p className="muted">Meets is not offered: no data rows were profiled.</p>}
          {outcome && outcome !== "meets" && (
            <p>
              <label htmlFor="signoff-text">{outcome === "rejected" ? "Reasons" : "Gaps"}, one per line</label><br />
              <textarea id="signoff-text" rows={3} cols={60} value={text} onChange={(e) => setText(e.target.value)} />
            </p>
          )}
          <p className="muted" data-testid="shown-to-requester">Gaps and reasons are shown to the requester.</p>
          <button type="submit" disabled={busy || !outcome || (outcome !== "meets" && lines(text).length === 0)}
                  data-testid="signoff">
            Sign off
          </button>
        </form>
      )}

      {canAct && page.next_action === "register" && (
        <form className="detail" data-testid="register-form" onSubmit={(e) => {
          e.preventDefault();
          act("register", { owner_id: owner || null, visibility }, (r) =>
            r.result?.outcome === "registered" ? "Registered." : "Not registered: name a data owner.");
        }}>
          <h3>Register</h3>
          <p>
            <label htmlFor="owner">Owner</label>{" "}
            <select id="owner" value={owner} onChange={(e) => setOwner(e.target.value)} data-testid="owner">
              <option value="">— choose a data owner —</option>
              {owners.map((o) => <option key={o.id} value={o.id}>{o.display_name}</option>)}
            </select>
          </p>
          <p role="radiogroup" aria-label="Who may see it">
            {(["open", "restricted"] as const).map((v) => (
              <label key={v} style={{ marginRight: 16 }}>
                <input type="radio" name="visibility" checked={visibility === v} onChange={() => setVisibility(v)} /> {v}
              </label>
            ))}
          </p>
          {page.source && <p className="muted">Licence, copied from the catalogue: {page.source.conditions.licence}</p>}
          <button type="submit" disabled={busy} data-testid="register">Register</button>
        </form>
      )}

      {page.registration && (
        <>
          <h3>Registration</h3>
          <dl data-testid="registration">
            <dt>Owner</dt><dd>{page.registration.owner_id}</dd>
            <dt>Who may see it</dt><dd>{page.registration.visibility}</dd>
            <dt>Licence</dt><dd>{page.registration.licence}</dd>
            <dt>Permitted use</dt><dd>{page.registration.permitted_use}</dd>
            <dt>Redistribution</dt><dd>{page.registration.redistribution}</dd>
            <dt>Registered by</dt><dd>{page.registration.registered_by}</dd>
          </dl>
        </>
      )}

      {(page.profiles.length > 0 || page.signoffs.length > 0) && (
        <>
          <h3>History</h3>
          <ul data-testid="history">
            {page.profiles.map((p) => (
              <li key={`p${p.version}`}>Profile {p.version}: {p.outcome.replaceAll("_", " ")} <span className="muted">{new Date(p.at).toLocaleString()}</span></li>
            ))}
            {page.signoffs.map((s) => (
              <li key={`s${s.version}`}>
                Sign-off {s.version}: {s.outcome.replaceAll("_", " ")} by {s.signed_off_by}
                {[...s.gaps, ...s.reasons].length > 0 && <> — {[...s.gaps, ...s.reasons].join("; ")}</>}
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
