import { Anchor, Badge, Button, Checkbox, Group, Text, Textarea, Title } from "@mantine/core";
import { useEffect, useState } from "react";
import {
  apiGet,
  apiPost,
  describeError,
  type FindSourceResponse,
  type RequirementPage,
  type SourceCandidate,
  type SourceConditions,
  type StepResponse,
} from "../api";
import { formatWhen } from "../theme";

// Phase 7.5, P29 (plan, assumption 25): where the data is, as five steps with the current one marked. A held
// delivery or a missing part marks the fourth step in the warning colour. The progress sentence stays as it is.
const STEPS = ["Checking the first file", "Setting up", "Approved for loading", "First delivery", "Ready"];
const STEP_OF: Record<string, number> = { checking_file: 0, setting_up: 1, approved: 2, delivery_held: 3,
                                          part_missing: 3 };

function ProgressSteps({ page }: { page: RequirementPage }) {
  const ready = page.status === "ready" || page.status === "access_restricted";
  const current = ready ? 4 : page.progress ? STEP_OF[page.progress.state] : null;
  if (current === null || current === undefined) return null;
  const warn = !ready && (page.progress?.state === "delivery_held" || page.progress?.state === "part_missing");
  return (
    <ol className="progress-steps" data-testid="progress-steps" aria-label="Where your data is">
      {STEPS.map((step, i) => (
        <li key={step} aria-current={i === current ? "step" : undefined} data-done={i < current ? "true" : undefined}
            data-warn={i === current && warn ? "true" : undefined}>
          {step}
        </li>
      ))}
    </ol>
  );
}

// One raised requirement: status, the requester's message, the scope to confirm, the sources to pick
// with their conditions, the selected source, the last fetch, release and the history of choices
// (phase 3 plan, section 10).
export default function RequirementView({ devUser, requirementId }: { devUser: string; requirementId: string }) {
  const [page, setPage] = useState<RequirementPage | null>(null);
  const [message, setMessage] = useState<string | null>(null); // the last step's message, if not the page's
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [confirmRelease, setConfirmRelease] = useState(false);

  const base = `/api/consumer/requirements/${requirementId}`;

  useEffect(() => {
    if (!devUser) return;
    apiGet<RequirementPage>(base, devUser)
      .then((p) => {
        setPage(p);
        setError(null);
      })
      .catch((err: unknown) => setError(describeError(err)));
  }, [devUser, base]);

  // Run one step; show its page and message. Returns the response, or null on an error.
  async function step(action: string, body: unknown = {}): Promise<StepResponse | null> {
    setBusy(true);
    setError(null);
    try {
      const response = await apiPost<StepResponse | FindSourceResponse>(`${base}/${action}`, body, devUser);
      setPage(response.requirement);
      setMessage(response.message);
      setNote("");
      setConfirmRelease(false);
      return response as StepResponse;
    } catch (err) {
      setError(describeError(err));
      return null;
    } finally {
      setBusy(false);
    }
  }

  // Confirming the scope lists the sources right away (consumer-ui chains the steps, assumption 2).
  async function confirmScope() {
    const decided = await step("scope/decision", { decision: "approved" });
    if (decided?.requirement.next_action === "list_sources") await step("sources");
  }

  const shown = message ?? page?.message;
  const warn = page?.last_fetch?.outcome === "source_down" || page?.status === "no_source_found"
    || page?.status === "access_restricted" || message?.includes("can be used only under these conditions");

  return (
    <section>
      <Title order={2} mb="xs">Requirement</Title>
      <Anchor href="#/requests" size="sm">Back to My requests</Anchor>
      {error && <Text className="bad" role="alert" data-testid="error">{error}</Text>}
      {page && (
        <>
          <div className={warn ? "result warn" : "result"} role="status" data-testid="requirement-message">
            <strong>{shown}</strong>
            {page.signoff_note && <div data-testid="signoff-note">{page.signoff_note}</div>}
          </div>
          <ProgressSteps page={page} />
          {page.progress && (
            <Text className="muted" data-testid="progress" data-state={page.progress.state}>{page.progress.text}</Text>
          )}
          {page.product && (
            <Text mt="sm" data-testid="product">
              Product: <strong>{page.product.name}</strong> ({page.product.code}){" "}
              {page.product.entitled
                ? <Badge tt="none" color="var(--amz-success)">Available</Badge>
                : <Badge tt="none" color="var(--amz-warning)" c="var(--amz-on-bright)">Access restricted</Badge>}
              {page.product.route && <span className="route" data-testid="product-route"> {page.product.route}.</span>}
            </Text>
          )}
          {page.held_note && <p className="warn" data-testid="held-note">{page.held_note}</p>}
          <dl>
            <dt>Asked for</dt>
            <dd>{page.title}</dd>
            <dt>Status</dt>
            <dd data-testid="requirement-status">{page.status.replaceAll("_", " ")}</dd>
            <dt>Elements</dt>
            <dd data-testid="elements">{page.elements.map((k) => page.element_names[k] ?? k).join(", ") || "-"}</dd>
            <dt>Terms not in the glossary</dt>
            <dd>{page.terms.join(", ") || "-"}</dd>
            {page.scope && (
              <>
                <dt>Where Amaze looks</dt>
                <dd data-testid="scope">
                  {page.scope.visibility}: {page.scope.where_to_search.join(", ").replaceAll("_", " ")}
                </dd>
              </>
            )}
            {page.source && (
              <>
                <dt>Source</dt>
                <dd data-testid="source">{page.source.name}, from {page.source.provider}</dd>
                <dt>Licence</dt>
                <dd>
                  {page.source.conditions.licence}. Use: {page.source.conditions.permitted_use}. Redistribution:{" "}
                  {page.source.conditions.redistribution}.
                </dd>
              </>
            )}
            {page.last_fetch && (
              <>
                <dt>Last fetch</dt>
                <dd data-testid="last-fetch" className={page.last_fetch.outcome === "fetched" ? "ok" : "warn"}>
                  {page.last_fetch.outcome === "fetched" ? "File received" : "Source did not answer"},{" "}
                  {formatWhen(page.last_fetch.at)}
                </dd>
              </>
            )}
          </dl>

          {page.next_action === "find_source" && (
            <Button data-testid="find-source" onClick={() => step("find-source")} disabled={busy}>
              Find a source
            </Button>
          )}

          {page.next_action === "confirm_scope" && page.proposed_scope && (
            <div className="request" data-testid="proposed-scope">
              <Title order={3}>Where should Amaze look?</Title>
              <Text>
                <strong>{page.proposed_scope.visibility === "public" ? "Outside the firm" : "Inside the firm"}</strong>
                {": "}{page.proposed_scope.provider}, in {page.proposed_scope.where_to_search.join(", ").replaceAll("_", " ")}.
              </Text>
              <Text className="muted">{page.proposed_scope.reason}</Text>
              <Textarea id="scope-note" label="Note, if you reject (optional)" value={note} maxLength={2000} autosize
                        minRows={2} onChange={(e) => setNote(e.currentTarget.value)} />
              <Group gap="sm">
                <Button data-testid="confirm-scope" onClick={confirmScope} disabled={busy}>Confirm</Button>
                <Button variant="default" data-testid="reject-scope" disabled={busy}
                        onClick={() => step("scope/decision", { decision: "rejected", note })}>Reject</Button>
              </Group>
            </div>
          )}

          {page.next_action === "ask_for_scope" && (
            <div className="request" data-testid="ask-for-scope">
              <Textarea id="ask-note" label="Tell Amaze what to look for (optional)" data-testid="ask-note" value={note}
                        maxLength={2000} autosize minRows={2} onChange={(e) => setNote(e.currentTarget.value)} />
              <Group>
                <Button data-testid="ask-again" onClick={() => step("scope", { note })} disabled={busy}>
                  Ask Amaze to look again
                </Button>
              </Group>
            </div>
          )}

          {page.next_action === "list_sources" && (
            <Button data-testid="list-sources" onClick={() => step("sources")} disabled={busy}>
              Find sources
            </Button>
          )}

          {page.next_action === "pick_source" && page.listed.length === 0 && (
            // P2 (plan, assumption 26): every listed source left the catalogue; a GET never closes the request
            <Text className="warn" mt="sm" data-testid="no-listed-source">
              No listed source is available any more; the next daily run closes this request.
            </Text>
          )}
          {page.next_action === "pick_source" && page.listed.length > 0 && (
            <ul className="hits" data-testid="listed-sources">
              {page.listed.map((c) => (
                <SourceCard key={c.proposal_id} candidate={c} busy={busy}
                            onPick={(conditions) => step("pick", { proposal_id: c.proposal_id, accepted_conditions: conditions })}
                            onDecline={(declineNote) => step("decline", { proposal_id: c.proposal_id, note: declineNote })} />
              ))}
            </ul>
          )}

          {page.can_release && !confirmRelease && (
            <Button variant="default" data-testid="release" onClick={() => setConfirmRelease(true)} disabled={busy}>
              Choose another source
            </Button>
          )}
          {page.can_release && confirmRelease && (
            <div className="result warn" data-testid="release-confirm">
              Stop using {page.source?.name ?? "this source"} for this request? It is not offered again.
              <Group gap="sm" mt="xs">
                <Button size="xs" data-testid="release-yes" onClick={() => step("release")} disabled={busy}>
                  Yes, choose another source
                </Button>
                <Button size="xs" variant="default" onClick={() => setConfirmRelease(false)} disabled={busy}>Cancel</Button>
              </Group>
            </div>
          )}

          {page.choices.length > 0 && (
            <>
              <Title order={3} mt="xl" mb="xs">Sources chosen</Title>
              <ol data-testid="choices">
                {page.choices.map((c) => (
                  <li key={c.version} data-testid={`choice-${c.version}`}>
                    Version {c.version}: {c.action} {c.source_name}
                    {c.reason === "requester_asked" && " (you asked)"}
                    {c.reason === "source_down_n_days" && " (it did not answer)"}
                    {c.reason === "candidate_rejected" && " (rejected by the backend team)"}
                    , {formatWhen(c.at)}
                  </li>
                ))}
              </ol>
            </>
          )}
        </>
      )}
    </section>
  );
}

// One listed source: coverage, the agent's reason and the conditions in full. Pick stays disabled until
// "I accept these conditions" is ticked (assumption 34); the pick sends back the conditions shown.
function SourceCard({ candidate, busy, onPick, onDecline }: {
  candidate: SourceCandidate;
  busy: boolean;
  onPick: (conditions: SourceConditions) => void;
  onDecline: (note: string) => void;
}) {
  const [accepted, setAccepted] = useState(false);
  const { source, coverage } = candidate;
  const c = source.conditions;
  const covered = [...coverage.elements, ...coverage.terms];
  return (
    <li data-testid={`candidate-${source.code}`}>
      <strong>{source.name}</strong>, from {source.provider}
      <div className="muted">{candidate.reason}</div>
      <div>Covers: {covered.join(", ") || "-"}{coverage.missing.length > 0 && `; not: ${coverage.missing.join(", ")}`}</div>
      <dl data-testid={`conditions-${source.code}`}>
        <dt>Licence</dt><dd>{c.licence}</dd>
        <dt>Permitted use</dt><dd>{c.permitted_use}</dd>
        <dt>Redistribution</dt><dd>{c.redistribution}</dd>
        <dt>Approvals you must hold</dt>
        <dd>{c.external_approvals.length ? c.external_approvals.join("; ") : "None"}</dd>
      </dl>
      <Group gap="sm">
        <Checkbox data-testid={`accept-${source.code}`} checked={accepted} label="I accept these conditions"
                  onChange={(e) => setAccepted(e.currentTarget.checked)} />
        <Button variant="filled" data-testid={`pick-${source.code}`} disabled={busy || !accepted}
                onClick={() => onPick(c)}>Pick</Button>
        <Button variant="default" data-testid={`decline-${source.code}`} disabled={busy}
                onClick={() => onDecline("")}>Decline</Button>
      </Group>
    </li>
  );
}
