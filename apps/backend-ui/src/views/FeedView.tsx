import { Anchor, Badge, Button, Group, NativeSelect, Radio, Table, Text, Textarea, TextInput, Title } from "@mantine/core";
import { useEffect, useState, type ReactNode } from "react";
import {
  apiGet, apiPost, decideProposal, describeError, words, type ColumnMapping, type FeedPage, type GlossaryRun,
  type Owner, type SignoffOutcome, type User,
} from "../api";
import { formatWhen } from "../theme";
import GoLiveSection from "./GoLiveSection";
import LoadsSection from "./LoadsSection";
import RulesSection from "./RulesSection";

// One feed, in the order of the work (phase 4 plan, section 10): source and requirements, the latest file,
// the profile, the ProfilerAgent's summary, the sign-off, the registration, and the history.
// The four actions show only for the backend team; every other backend role reads.
// Phase 5 (plan, section 10.3), on a registered feed: the glossary builder's last run, and the mapping table.
// Run the glossary builder shows for the glossary owner and the backend team; the mapping actions for the steward.
// Phase 7.5: the look of task 4 (Mantine); each section is a section-<name> element (task 1).

const WHY_NONE: Record<string, string> = {
  with_glossary_builder: "with the glossary builder",
  rejected_by_glossary_owner: "rejected by the glossary owner",
  type_does_not_fit: "its type does not fit",
};

type Proposed = {
  recommended: { source_field: string }[];
  results: { outcome: "tie_to_steward" | "no_element_back_to_05"; source_field: string }[];
  unchanged: string[];
};

function proposedText(r: Proposed): string {
  const ties = r.results.filter((x) => x.outcome === "tie_to_steward").length;
  const back = r.results.filter((x) => x.outcome === "no_element_back_to_05").length;
  const parts = [`${r.recommended.length} recommended`, `${ties} ${ties === 1 ? "tie" : "ties"} for you to choose`,
                 `${back} sent back to the glossary builder`];
  if (r.unchanged.length > 0) parts.push(`${r.unchanged.length} unchanged (${r.unchanged.join(", ")})`);
  return `Mappings proposed: ${parts.join(", ")}.`;
}

// Phase 7.5, P9, P19, P23 (plan, assumption 19): the one result line of the page, under the heading of the section
// that holds the next action, or of the section acted on; `at` names that section.
type Result = { ok: boolean; text: string; at: string };

function ResultLine({ result }: { result: Result }) {
  return result.ok
    ? <Text className="ok" role="status" data-testid="feed-done" mb="xs">{result.text}</Text>
    : <Text className="bad" role="alert" mb="xs">{result.text}</Text>;
}

// One part of the feed page: a section-<name> element with its heading, and the result line when it is here.
function Section({ name, title, result, children }: {
  name: string; title: ReactNode; result: Result | null; children: ReactNode;
}) {
  return (
    <div data-testid={`section-${name}`}>
      <Title order={3} mt="xl" mb="xs">{title}</Title>
      {result?.at === name && <ResultLine result={result} />}
      {children}
    </div>
  );
}

// One column of the mapping table: mapped (with Propose again), pending (choose, approve, reject), or why none.
function MappingRow({ column, canMap, busy, onPropose, onDecide }: {
  column: ColumnMapping; canMap: boolean; busy: boolean; onPropose: (column: string) => void;
  onDecide: (proposalId: string, decision: "approved" | "rejected", choice: string | null, note: string) => void;
}) {
  const pending = column.pending;
  const [choice, setChoice] = useState<string>(pending?.recommended ?? "");
  const [note, setNote] = useState("");
  const [showVersions, setShowVersions] = useState(false);
  useEffect(() => setChoice(pending?.recommended ?? ""), [pending?.proposal_id, pending?.recommended]);

  return (
    <Table.Tr data-testid="mapping-row" data-column={column.name}>
      <Table.Td><code>{column.name}</code></Table.Td>
      <Table.Td>{column.type ?? <span className="muted">unknown</span>}</Table.Td>
      <Table.Td>{column.source_says ?? <span className="muted">not declared</span>}</Table.Td>
      <Table.Td>
        {column.mapped_to ? (
          <>
            <code>{column.mapped_to}</code> <span className="muted">version {column.mapped_version}</span>
            {column.versions.length > 1 && (
              <> <Button size="compact-xs" variant="default" onClick={() => setShowVersions(!showVersions)}
                         data-testid="versions">
                {showVersions ? "Hide versions" : `${column.versions.length} versions`}
              </Button></>
            )}
            {showVersions && (
              <ul data-testid="mapping-versions">
                {column.versions.map((v) => (
                  <li key={v.id}>Version {v.version}: <code>{v.element_key}</code>{" "}
                    <span className="muted">{formatWhen(v.created_at)}</span></li>
                ))}
              </ul>
            )}
          </>
        ) : !pending && (
          <span className="muted" data-testid="why-none">
            {column.why_none ? WHY_NONE[column.why_none] : "waiting for Propose mappings"}
          </span>
        )}
        {pending && (
          <div data-testid="pending-mapping">
            <div className="muted">
              {pending.recommended ? "MappingAgent recommends one" : "A tie: choose one"} (it decides nothing):{" "}
              {pending.reason}
            </div>
            <Radio.Group value={choice} onChange={setChoice} aria-label={`Candidates for ${column.name}`}>
              <Group gap="md" mt={4}>
                {pending.candidates.map((c) => (
                  <Radio key={c} name={`candidate-${pending.proposal_id}`} value={c} disabled={!canMap} size="xs"
                         label={<><code>{c}</code>{c === pending.recommended && <span className="muted"> (recommended)</span>}</>} />
                ))}
              </Group>
            </Radio.Group>
          </div>
        )}
      </Table.Td>
      <Table.Td>
        {canMap && pending && (
          <Group gap="xs" wrap="nowrap">
            <Button size="xs" disabled={busy || !choice} data-testid="approve-mapping"
                    onClick={() => onDecide(pending.proposal_id, "approved", choice || null, "")}>Approve</Button>
            <TextInput size="xs" w={170} aria-label={`Note for ${column.name}`} placeholder="Note (needed to reject)"
                       value={note} maxLength={500} onChange={(e) => setNote(e.currentTarget.value)} />
            <Button size="xs" variant="default" disabled={busy || !note.trim()} data-testid="reject-mapping"
                    onClick={() => onDecide(pending.proposal_id, "rejected", null, note)}>Reject</Button>
          </Group>
        )}
        {canMap && !pending && column.mapped_to && (
          <Button size="xs" variant="default" disabled={busy} data-testid="propose-again"
                  onClick={() => onPropose(column.name)}>
            Propose again
          </Button>
        )}
      </Table.Td>
    </Table.Tr>
  );
}

// P17: what a glossary run did, its own columns first (a sent-back run's, in its order); the columns the builder
// skipped (step 05's SKIPPED outcomes) are folded into one "Not asked" line.
const NOT_ASKED = new Set(["covered_by_name", "mapped", "already_asked"]);

function RunResults({ run }: { run: GlossaryRun }) {
  const entries = Object.entries(run.results ?? {});
  const own = (c: string) => (run.columns.includes(c) ? run.columns.indexOf(c) : run.columns.length);
  const asked = entries.filter(([, o]) => !NOT_ASKED.has(o)).sort(([a], [b]) => own(a) - own(b));
  const skipped = entries.filter(([, o]) => NOT_ASKED.has(o));
  return (
    <>
      {asked.length > 0 && (
        <ul>{asked.map(([c, outcome]) => <li key={c}><code>{c}</code>: {words(outcome)}</li>)}</ul>
      )}
      {skipped.length > 0 && (
        <div className="muted" data-testid="run-not-asked">
          Not asked: {skipped.map(([c, outcome]) => `${c} (${words(outcome)})`).join(", ")}
        </div>
      )}
    </>
  );
}

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

// Where an action's result line goes (assumption 19): the section of the next action, else the one acted on.
function resultAt(action: string, page: FeedPage, canAct: boolean): string {
  if (action === "profile") return "summary";
  if (action === "profile/summary") return canAct && page.next_action === "sign_off" ? "signoff" : "summary";
  if (action === "signoff") return canAct && page.next_action === "register" ? "register" : "summary";
  return page.registration ? "registration" : "register"; // register
}
const ACTED_ON: Record<string, string> = { profile: "file", "profile/summary": "summary", signoff: "signoff",
                                           register: "register" };

export default function FeedView({ feedId, devUser, me, people, canAct, canRun, canMap, changed, onChange }: {
  feedId: string; devUser: string; me: User | null; people: Record<string, string>; canAct: boolean;
  canRun: boolean; canMap: boolean; changed: number; onChange: () => void;
}) {
  const [page, setPage] = useState<FeedPage | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [result, setResult] = useState<Result | null>(null);
  const [busy, setBusy] = useState(false);
  const [owners, setOwners] = useState<Owner[]>([]);
  const [outcome, setOutcome] = useState<SignoffOutcome | "">("");
  const [text, setText] = useState("");
  const [owner, setOwner] = useState("");
  const [visibility, setVisibility] = useState<"open" | "restricted">("open");
  const name = (userId: string) => people[userId] ?? userId; // P8: a person by name; an unknown id as itself

  useEffect(() => {
    if (!devUser) return;
    let current = true;
    apiGet<FeedPage>(`/api/amaze/feeds/${feedId}`, devUser)
      .then((p) => current && (setPage(p), setLoadError(null)))
      .catch((err: unknown) => current && setLoadError(describeError(err)));
    apiGet<Owner[]>("/api/amaze/owners", devUser).then((o) => current && setOwners(o)).catch(() => undefined);
    return () => {
      current = false;
    };
  }, [feedId, devUser, changed]);

  // P9: the section holding the result line comes into view
  useEffect(() => {
    if (!result) return;
    document.querySelector(`[data-testid="section-${result.at}"]`)?.scrollIntoView?.({ block: "start" });
  }, [result]);

  async function act(action: string, body: unknown, describe: (r: { result?: { outcome: string } }) => string) {
    setBusy(true);
    setResult(null);
    try {
      const response = await apiPost<{ feed: FeedPage; result?: { outcome: string } }>(
        `/api/amaze/feeds/${feedId}/${action}`, body, devUser);
      setPage(response.feed);
      setResult({ ok: true, text: describe(response), at: resultAt(action, response.feed, canAct) });
      setOutcome("");
      setText("");
      if (action === "register") onChange(); // the run it queued shows in the banner
    } catch (err: unknown) {
      setResult({ ok: false, text: describeError(err), at: ACTED_ON[action] });
    } finally {
      setBusy(false);
    }
  }

  // phase 5: the three actions whose response is not the feed page; the page is read again (onChange)
  async function step(at: string, work: () => Promise<string>) {
    setBusy(true);
    setResult(null);
    try {
      const message = await work();
      setResult({ ok: true, text: message, at });
      onChange();
    } catch (err: unknown) {
      setResult({ ok: false, text: describeError(err), at });
    } finally {
      setBusy(false);
    }
  }

  // The rules, go-live and loads sections report through these; their line shows under their own heading.
  const reporter = (at: string) => ({
    onMessage: (text: string) => setResult({ ok: true, text, at }),
    onError: (text: string | null) => (text === null ? undefined : setResult({ ok: false, text, at })),
    notice: result?.at === at ? <ResultLine result={result} /> : null,
  });

  const runBuilder = () => step("glossary-builder", async () => {
    const r = await apiPost<{ created: boolean }>(`/api/amaze/feeds/${feedId}/glossary/run`, {}, devUser);
    return r.created ? "The glossary builder is queued for this feed." : "The glossary builder already has a run for this feed.";
  });
  const proposeMappings = (column?: string) => step("mapping", async () => {
    const r = await apiPost<{ result: Proposed }>(`/api/amaze/feeds/${feedId}/mappings/propose`,
                                                  column ? { column } : {}, devUser);
    return proposedText(r.result);
  });
  const decideMapping = (id: string, decision: "approved" | "rejected", choice: string | null, note: string) =>
    step("mapping", async () => {
      await decideProposal(id, decision, choice, note, devUser);
      return decision === "approved" ? `Mapped to ${choice}.` : "Rejected: its candidates are not proposed again.";
    });

  if (loadError && !page) return <Text className="bad" role="alert">{loadError}</Text>;
  if (!page) return <Text className="muted">Loading…</Text>;

  const { feed, profile } = page;
  const specOnly = profile?.outcome === "spec_only_low_confidence";
  const items = outcome === "rejected" ? "reasons" : "gaps";
  const ofLatestFile = profile !== null && page.last_file !== null && profile.fetch_id === page.last_file.id; // P6
  const leftToMap = page.columns.some((c) => !c.mapped_to && !c.pending); // P18

  return (
    <section data-testid="feed-view">
      <Anchor href="#/feeds" size="sm">← Feed registry</Anchor>
      <Title order={2} mt="xs" mb="sm">
        <code>{feed.code}</code> {feed.name} <Badge variant="default" ml="xs">{feed.stage}</Badge>
      </Title>
      {loadError && <Text className="bad" role="alert">{loadError}</Text>}

      <Section name="source" title="Source" result={result}>
        {page.source ? (
          <dl>
            <dt>Provider</dt><dd>{page.source.name} — {page.source.provider}</dd>
            <dt>Licence</dt><dd>{page.source.conditions.licence}</dd>
            <dt>Permitted use</dt><dd>{page.source.conditions.permitted_use}</dd>
            <dt>Redistribution</dt><dd>{page.source.conditions.redistribution}</dd>
          </dl>
        ) : <Text className="muted">No catalogue source.</Text>}
      </Section>

      <Section name="requirements" title="Requirements that select it" result={result}>
        {page.requirements.length === 0 ? <Text className="muted">None now.</Text> : (
          <Table.ScrollContainer minWidth={720}>
            <Table data-testid="feed-requirements" verticalSpacing="xs">
              <Table.Thead>
                <Table.Tr><Table.Th>Requirement</Table.Th><Table.Th>Status</Table.Th><Table.Th>How chosen</Table.Th>
                  <Table.Th>Conditions the requester accepted</Table.Th></Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {page.requirements.map((r) => (
                  <Table.Tr key={r.id}>
                    <Table.Td>
                      {r.title}
                      <div className="muted">raised {formatWhen(r.raised_at, { time: false })} by {name(r.requester_id)}</div>
                    </Table.Td>
                    <Table.Td>{r.status}</Table.Td>
                    <Table.Td>{r.basis === "known_source" ? "Known source" : "Requester's pick"}</Table.Td>
                    <Table.Td>
                      {r.accepted_conditions ? (
                        <>
                          {r.accepted_conditions.licence}; {r.accepted_conditions.permitted_use}
                          {r.accepted_conditions.external_approvals.map((a) => <div key={a} className="muted">Holds: {a}</div>)}
                        </>
                      ) : <span className="muted">Known source: Amaze holds the agreement</span>}
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        )}
      </Section>

      <Section name="file" title="Latest file" result={result}>
        {page.last_file ? (
          <Text>
            {page.last_file.outcome === "fetched" ? <code>{page.last_file.blob_ref}</code> : <span className="bad">{page.last_file.detail}</span>}
            {" "}<span className="muted">{formatWhen(page.last_file.created_at)}</span>
          </Text>
        ) : <Text className="muted">No file yet.</Text>}
        {ofLatestFile && <Text className="muted" mt="xs">Profile {profile.version} is of this file.</Text>}
        {canAct && feed.stage === "candidate" && page.last_file && !ofLatestFile && (
          <Button mt="sm" disabled={busy} data-testid="profile"
                  onClick={() => act("profile", {}, (r) => `Profile written: ${r.result?.outcome.replaceAll("_", " ")}.`)}>
            Profile the latest file
          </Button>
        )}
      </Section>

      {profile && (
        <Section name="profile" title={`Profile ${profile.version}`} result={result}>
          {specOnly && (
            <Text className="bad" role="note" data-testid="spec-only">
              Low confidence: spec only. {profile.detail} Only what the source declares is known.
            </Text>
          )}
          <Text className="muted">{profile.row_count ?? 0} rows · {profile.engine} · by {name(profile.profiled_by)}</Text>
          {profile.columns.length > 0 && (
            <Table.ScrollContainer minWidth={600}>
              <Table data-testid="profile-columns" verticalSpacing={4}>
                <Table.Thead>
                  <Table.Tr><Table.Th>Column</Table.Th><Table.Th>Type</Table.Th><Table.Th>Null rate</Table.Th>
                    <Table.Th>Distinct</Table.Th><Table.Th>Declared as</Table.Th></Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {profile.columns.map((c) => (
                    <Table.Tr key={c.name}>
                      <Table.Td><code>{c.name}</code></Table.Td>
                      <Table.Td>{c.type}</Table.Td>
                      <Table.Td>{pct(c.null_rate)}</Table.Td>
                      <Table.Td>{c.distinct_count}</Table.Td>
                      <Table.Td>{profile.spec.columns[c.name] ?? <span className="muted">not declared</span>}</Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </Table.ScrollContainer>
          )}
          {page.undeclared_columns.length > 0 && <Text mt="xs">Not declared by the source: {page.undeclared_columns.join(", ")}</Text>}
          {page.missing_columns.length > 0 && <Text mt="xs" className="bad">Declared but not in the file: {page.missing_columns.join(", ")}</Text>}
          {profile.coverage.map((c) => (
            <div key={c.requirement_id} className="detail">
              <strong>{page.coverage_titles[c.requirement_id] ?? "A requirement"}</strong>
              {!page.requirements.some((r) => r.id === c.requirement_id) && (
                <span className="muted"> — no longer selects this feed</span>
              )}
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
        </Section>
      )}

      {profile && (
        <Section name="summary" title="Summary" result={result}>
          {page.summary ? (
            <div className="detail" data-testid="summary">
              <div className="muted">ProfilerAgent's proposal ({page.summary_status}); it decides nothing</div>
              <p>{page.summary.summary}</p>
              <p>Recommends: <strong>{page.summary.recommended_outcome.replaceAll("_", " ")}</strong></p>
              {page.summary.gaps.length > 0 && <ul>{page.summary.gaps.map((g) => <li key={g}>Gap: {g}</li>)}</ul>}
              {page.summary.reasons.length > 0 && <ul>{page.summary.reasons.map((g) => <li key={g}>Reason: {g}</li>)}</ul>}
            </div>
          ) : <Text className="muted">Not summarised yet.</Text>}
          {canAct && page.next_action === "summarise" && (
            <Button mt="sm" disabled={busy} data-testid="summarise"
                    onClick={() => act("profile/summary", {}, () => "Summary ready to sign off.")}>
              Summarise
            </Button>
          )}
        </Section>
      )}

      {canAct && page.next_action === "sign_off" && page.summary && (
        <Section name="signoff" title={`Sign off profile ${profile?.version}`} result={result}>
          <form className="detail" data-testid="signoff-form" onSubmit={(e) => {
            e.preventDefault();
            if (!outcome) return;
            act("signoff", { outcome, [items]: outcome === "meets" ? [] : lines(text) },
                (r) => `Signed off: ${r.result?.outcome.replaceAll("_", " ")}.`);
          }}>
            <Radio.Group value={outcome} onChange={(v) => setOutcome(v as SignoffOutcome)} aria-label="Outcome">
              <Group gap="lg">
                {OUTCOMES.map((o) => (
                  <Radio key={o.value} name="outcome" value={o.value} label={o.label}
                         disabled={o.value === "meets" && specOnly} data-testid={`outcome-${o.value}`} />
                ))}
              </Group>
            </Radio.Group>
            {specOnly && <Text className="muted" mt="xs">Meets is not offered: no data rows were profiled.</Text>}
            {outcome && outcome !== "meets" && (
              <Textarea id="signoff-text" mt="sm" autosize minRows={3} maw={640}
                        label={`${outcome === "rejected" ? "Reasons" : "Gaps"}, one per line`} value={text}
                        onChange={(e) => setText(e.currentTarget.value)} />
            )}
            <Text className="muted" mt="sm" data-testid="shown-to-requester">Gaps and reasons are shown to the requester.</Text>
            <Button type="submit" mt="sm" disabled={busy || !outcome || (outcome !== "meets" && lines(text).length === 0)}
                    data-testid="signoff">
              Sign off
            </Button>
          </form>
        </Section>
      )}

      {canAct && page.next_action === "register" && (
        <Section name="register" title="Register" result={result}>
          <form className="detail" data-testid="register-form" onSubmit={(e) => {
            e.preventDefault();
            act("register", { owner_id: owner || null, visibility }, (r) =>
              r.result?.outcome === "registered" ? "Registered." : "Not registered: name a data owner.");
          }}>
            <NativeSelect id="owner" label="Owner" maw={320} value={owner} data-testid="owner"
                          onChange={(e) => setOwner(e.currentTarget.value)}
                          data={[{ value: "", label: "— choose a data owner —" },
                                 ...owners.map((o) => ({ value: o.id, label: o.display_name }))]} />
            <Radio.Group mt="sm" label="Who may see it" value={visibility}
                         onChange={(v) => setVisibility(v as "open" | "restricted")}>
              <Group gap="lg" mt={4}>
                {(["open", "restricted"] as const).map((v) => <Radio key={v} name="visibility" value={v} label={v} />)}
              </Group>
            </Radio.Group>
            {page.source && <Text className="muted" mt="sm">Licence, copied from the catalogue: {page.source.conditions.licence}</Text>}
            <Button type="submit" mt="sm" disabled={busy} data-testid="register">Register</Button>
          </form>
        </Section>
      )}

      {page.registration && (
        <Section name="registration" title="Registration" result={result}>
          <dl data-testid="registration">
            <dt>Owner</dt><dd>{name(page.registration.owner_id)}</dd>
            <dt>Who may see it</dt><dd>{page.registration.visibility}</dd>
            <dt>Licence</dt><dd>{page.registration.licence}</dd>
            <dt>Permitted use</dt><dd>{page.registration.permitted_use}</dd>
            <dt>Redistribution</dt><dd>{page.registration.redistribution}</dd>
            <dt>Registered by</dt><dd>{name(page.registration.registered_by)}</dd>
          </dl>
        </Section>
      )}

      {feed.stage === "registered" && (
        <>
          <Section name="glossary-builder" title="Glossary builder" result={result}>
            {page.glossary_run ? (
              <div className="detail" data-testid="glossary-run">
                <div>
                  Last run: <strong>{page.glossary_run.status}</strong>{" "}
                  <span className="muted">
                    ({words(page.glossary_run.reason)}, attempt {page.glossary_run.attempt},{" "}
                    {formatWhen(page.glossary_run.finished_at ?? page.glossary_run.created_at)})
                  </span>
                </div>
                {page.glossary_run.error && <div className="bad">Failed: {page.glossary_run.error}</div>}
                {page.glossary_run.results && Object.keys(page.glossary_run.results).length > 0 ? (
                  <RunResults run={page.glossary_run} />
                ) : page.glossary_run.status === "done" ? <div className="muted">No new column to ask about.</div>
                  : <div className="muted">Columns: {page.glossary_run.columns.join(", ") || "every new one"}</div>}
              </div>
            ) : <Text className="muted">No run yet.</Text>}
            {canRun && (
              <Button mt="sm" variant="default" disabled={busy} data-testid="run-glossary-builder" onClick={runBuilder}>
                Run the glossary builder
              </Button>
            )}
          </Section>

          <Section name="mapping" title="Mapping" result={result}>
            <Table.ScrollContainer minWidth={900}>
              <Table data-testid="mapping-table" verticalSpacing="xs">
                <Table.Thead>
                  <Table.Tr><Table.Th>Column</Table.Th><Table.Th>Type</Table.Th><Table.Th>The source says</Table.Th>
                    <Table.Th>Mapped to</Table.Th><Table.Th /></Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {page.columns.map((c) => (
                    <MappingRow key={c.name} column={c} canMap={canMap} busy={busy} onPropose={proposeMappings}
                                onDecide={decideMapping} />
                  ))}
                </Table.Tbody>
              </Table>
            </Table.ScrollContainer>
            {canMap ? (
              <Group gap="sm" mt="sm">
                <Button disabled={busy || !leftToMap} data-testid="propose-mappings" onClick={() => proposeMappings()}>
                  Propose mappings
                </Button>
                <span className="muted">
                  {leftToMap ? "for every column with no mapping and no pending proposal"
                    : "Nothing left to map: every column is mapped or waits for a decision."}
                </span>
              </Group>
            ) : <Text className="muted" mt="sm">Only the data steward proposes and decides mappings.</Text>}

            {page.product && (
              <Text mt="md" data-testid="product">
                Product: <code>{page.product.code}</code> {page.product.name}, {page.product.visibility}, owned by{" "}
                {name(page.product.owner_id)}, created {formatWhen(page.product.created_at)}.
              </Text>
            )}
            {page.parked_columns.length > 0 && (
              <Text mt="xs" className="bad" data-testid="parked-columns">
                Parked, not loaded until mapped and a new go-live is approved:{" "}
                {page.parked_columns.map((c) => <code key={c}>{c} </code>)}
              </Text>
            )}
          </Section>
          <RulesSection page={page} devUser={devUser} roles={me?.roles ?? []} onChange={onChange} {...reporter("rules")} />
          <GoLiveSection page={page} devUser={devUser} userId={me?.id ?? ""} roles={me?.roles ?? []} people={people}
                         onChange={onChange} {...reporter("go-live")} />
          <LoadsSection page={page} devUser={devUser} roles={me?.roles ?? []} onChange={onChange} {...reporter("loads")} />
        </>
      )}

      {(page.profiles.length > 0 || page.signoffs.length > 0) && (
        <Section name="history" title="History" result={result}>
          <ul data-testid="history">
            {page.profiles.map((p) => (
              <li key={`p${p.version}`}>Profile {p.version}: {p.outcome.replaceAll("_", " ")} <span className="muted">{formatWhen(p.at)}</span></li>
            ))}
            {page.signoffs.map((s) => (
              <li key={`s${s.version}`}>
                Sign-off {s.version}: {s.outcome.replaceAll("_", " ")} by {name(s.signed_off_by)}
                {[...s.gaps, ...s.reasons].length > 0 && <> — {[...s.gaps, ...s.reasons].join("; ")}</>}
              </li>
            ))}
          </ul>
        </Section>
      )}
    </section>
  );
}
