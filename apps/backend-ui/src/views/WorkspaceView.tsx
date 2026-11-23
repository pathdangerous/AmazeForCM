import { Alert, Button, Text, Title } from "@mantine/core";
import { useCallback, useEffect, useState } from "react";
import { LoadingSkeleton, StickyFooter } from "../../../ui-shared/phase711";
import {
  addItem, apiGet, approveStep, decideItem, DecisionNeeded, describeError, editItem, enterStep, getTimeline, getWorkspace,
  goBack, ONBOARDING_STEPS, setKeyPolicy, uploadFeedFile, type Health, type KeyPolicy, type OnboardingItem, type StepName,
  type TimelineEntry, type Visibility, type Workspace,
} from "../api";
import AgentPanel from "./AgentPanel";
import AddItem from "./steps/AddItem";
import Stepper, { StateBadge, STEP_LABELS } from "./Stepper";
import FirstLoadPanel from "./steps/FirstLoadPanel";
import GlossaryPanel from "./steps/GlossaryPanel";
import GoLiveSummary from "./steps/GoLiveSummary";
import MappingPanel from "./steps/MappingPanel";
import ProductReadyPanel from "./steps/ProductReadyPanel";
import ProfilePanel from "./steps/ProfilePanel";
import RulesPanel from "./steps/RulesPanel";
import type { ItemActions } from "./steps/shared";
import TransformationsPanel from "./steps/TransformationsPanel";

const POLL_MS = 2000;

// The onboarding workspace (phase 7.8, tasks 7.2 and 7.3b): stepper, the viewed step's panel, the agent panel, a footer.
export default function WorkspaceView({ id, devUser, people = {} }: { id: string; devUser: string; people?: Record<string, string> }) {
  const [ws, setWs] = useState<Workspace | null>(null);
  const [viewedWs, setViewedWs] = useState<Workspace | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [viewed, setViewed] = useState<StepName | null>(null);
  const [marked, setMarked] = useState<string[]>([]);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [visibilityChoice, setVisibilityChoice] = useState<Visibility | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [editError, setEditError] = useState<string | null>(null);
  const [addError, setAddError] = useState<string | null>(null);
  const [keyPolicyError, setKeyPolicyError] = useState<string | null>(null);
  const [fourEyes, setFourEyes] = useState(false);
  const [timeline, setTimeline] = useState<TimelineEntry[] | null>(null);

  const load = useCallback(() => {
    if (!devUser) return Promise.resolve();
    return getWorkspace(id, devUser).then((w) => { setWs(w); setError(null); })
      .catch((err: unknown) => setError(describeError(err)));
  }, [id, devUser]);

  useEffect(() => { void load(); }, [load]);

  useEffect(() => {
    apiGet<Health>("/api/health", undefined, [503]).then((h) => setFourEyes(h.four_eyes === true)).catch(() => setFourEyes(false));
  }, []);

  const stamp = ws?.session.updated_at;
  useEffect(() => {
    if (!devUser || !stamp) return;
    let current = true;
    getTimeline(id, devUser).then((t) => current && setTimeline(t)).catch(() => current && setTimeline(null));
    return () => { current = false; };
  }, [id, devUser, stamp]);

  // Another step than the current one is read from the API with ?step= and shown read only.
  useEffect(() => {
    if (!devUser || !viewed) return;
    let current = true;
    getWorkspace(id, devUser, viewed)
      .then((w) => current && setViewedWs(w))
      .catch((err: unknown) => { if (current) setNote(describeError(err)); });
    return () => { current = false; };
  }, [id, devUser, viewed, stamp]);

  const working = !error && ws !== null && (ws.job?.status === "queued" || ws.job?.status === "running"
    || ws.steps.some((s) => s.state === "agent_working"));
  useEffect(() => {
    if (!working || !devUser) return;
    let alive = true;
    const timer = setInterval(() => {
      getWorkspace(id, devUser)
        .then((w) => alive && setWs(w))
        .catch((err: unknown) => alive && setError(describeError(err)));
    }, POLL_MS);
    return () => { alive = false; clearInterval(timer); };
  }, [working, id, devUser]);

  if (error && !ws) {
    return (
      <section>
        <Alert role="alert" color="red">{error}</Alert>
        <Button mt="sm" variant="default" onClick={() => void load()}>Reload</Button>
      </section>
    );
  }
  if (!ws) return <LoadingSkeleton label="Loading the workspace" testId="workspace-loading" />;

  const current = ws.current_step;
  const shown = viewed ?? current;
  const atCurrent = shown === current;
  const view = atCurrent ? ws : viewedWs?.viewed_step === shown ? viewedWs : null;
  const items: OnboardingItem[] = view?.items ?? [];
  const stepView = ws.steps.find((s) => s.name === current);
  const state = stepView?.state ?? "waiting";
  const shownView = ws.steps.find((s) => s.name === shown);
  const shownState = shownView?.state ?? "waiting";
  const visibility: Visibility = visibilityChoice ?? ws.visibility ?? "open";

  const act = async (work: () => Promise<Workspace | void>) => {
    setBusy(true);
    setNote(null);
    try {
      const next = await work();
      if (next) { setWs(next); setViewed(null); setViewedWs(null); }
      setMarked([]);
      setEditing(null);
    } catch (err: unknown) {
      if (err instanceof DecisionNeeded) {
        setMarked(err.items.map((i) => i.proposal_id));
        setNote("Decide the marked items first");
      } else setNote(describeError(err));
    } finally {
      setBusy(false);
    }
  };

  const approve = () => act(async () => {
    const res = await approveStep(id, current === "go_live" ? visibility : undefined, devUser);
    if (res.advance.waiting_for) setNote(`Waiting for ${res.advance.waiting_for}`);
    return res.workspace;
  });
  const rerun = () => act(() => enterStep(id, current, devUser));

  const actions: ItemActions = {
    readOnly: !atCurrent, busy, marked, editing, editError, people, addError,
    add: async (body) => {
      setBusy(true);
      setAddError(null);
      try {
        setWs((await addItem(id, body, devUser)).workspace);
        setMarked([]);
        return true;
      } catch (err: unknown) {
        setAddError(describeError(err)); // the API's 409 or 422 message, in the form (a rule's back-test says why)
        return false;
      } finally {
        setBusy(false);
      }
    },
    decide: (item, decision, choice, note) => void act(async () => (await decideItem(id, item.proposal_id, decision, devUser, choice, note)).workspace),
    startEdit: (item) => { setEditError(null); setEditing(item.proposal_id); },
    cancelEdit: () => { setEditing(null); setEditError(null); },
    saveEdit: async (item, payload) => {
      setBusy(true);
      setEditError(null);
      try {
        const res = await editItem(id, item.proposal_id, payload, devUser);
        setWs(res.workspace);
        setEditing(null);
        setMarked([]);
      } catch (err: unknown) {
        setEditError(describeError(err)); // the API's 409 or 422 message, in the form
      } finally {
        setBusy(false);
      }
    },
  };

  const saveKeyPolicy = async (policy: KeyPolicy) => {
    setBusy(true);
    setKeyPolicyError(null);
    try {
      setWs((await setKeyPolicy(id, policy, devUser)).workspace);
    } catch (err: unknown) {
      setKeyPolicyError(describeError(err));
    } finally {
      setBusy(false);
    }
  };

  // One Back control: it goes back to the earlier done step being viewed (clicked in the stepper).
  const backTo: StepName | null = !atCurrent && shown !== "upload" && shownState === "done"
    && ONBOARDING_STEPS.indexOf(shown) < ONBOARDING_STEPS.indexOf(current) ? shown : null;
  const preAccepted = atCurrent ? ws.items.filter((i) => i.status === "pending" && i.pre_accepted).length : 0;

  const reason = ws.can_approve ? null
    : ws.blocked_by ?? (state === "agent_working" ? "The agent is working" : state === "failed" ? "The step failed"
      : current === "product_ready" ? "Nothing left to approve" : "Not ready to approve");
  const approveLabel = current === "go_live" ? "Approve go-live" : "Approve & next";
  const panel = { ws: view ?? ws, items, actions };

  let main;
  if (!view) {
    main = <LoadingSkeleton label="Loading the step" testId="step-loading" />;
  } else if (shown === "product_ready" && shownState === "done") {
    main = <ProductReadyPanel feedCode={ws.feed_code} />;
  } else if (atCurrent && state === "agent_working") {
    main = (
      <div data-testid="workspace-working" style={{ display: "flex", gap: 12, alignItems: "center" }}>
        <Text>{ws.progress ?? "The agent is working"}</Text>
      </div>
    );
  } else if (atCurrent && state === "failed" && shown === "first_load" && (view.inputs?.facts ?? []).some((f) => f.label === "Load")) {
    // a load that ran and did not release: not retried; back to the rules, or a new file (the panel offers both)
    main = (
      <div>
        <Alert role="alert" color="red" mb="sm">
          {view.inputs?.load_summary ?? stepView?.detail ?? ws.job?.error ?? "The first load did not release."}
        </Alert>
        <FirstLoadPanel ws={view} state={shownState} detail={null} busy={busy}
                        onBackToRules={() => void act(() => goBack(id, "rules", devUser))}
                        onUpload={(file) => void act(async () => {
                          await uploadFeedFile(ws.session.feed_id, file, devUser);
                          return enterStep(id, "first_load", devUser);
                        })} />
      </div>
    );
  } else if (atCurrent && state === "failed") {
    main = (
      <div>
        <Alert role="alert" color="red">{stepView?.detail ?? ws.job?.error ?? "The step failed."}</Alert>
        <Button mt="sm" onClick={() => void rerun()} disabled={busy}>Try again</Button>
      </div>
    );
  } else if (atCurrent && (state === "waiting" || (state === "stale" && ws.items.length === 0))) {
    main = (
      <div className="empty">
        <Text mb="sm">{state === "stale" ? "This step is out of date." : "Nothing here yet."}</Text>
        <Button onClick={() => void rerun()} disabled={busy}>Run the agent</Button>
      </div>
    );
  } else if (shown === "profile") {
    main = <ProfilePanel {...panel} />;
  } else if (shown === "glossary") {
    main = <GlossaryPanel {...panel} />;
  } else if (shown === "mapping") {
    main = <MappingPanel {...panel} />;
  } else if (shown === "transformations") {
    main = <TransformationsPanel {...panel} onKeyPolicy={(p) => void saveKeyPolicy(p)} keyPolicyError={keyPolicyError} />;
  } else if (shown === "rules") {
    main = <RulesPanel {...panel} />;
  } else if (shown === "go_live") {
    main = <GoLiveSummary {...panel} visibility={visibility} onVisibility={setVisibilityChoice} />;
  } else if (shown === "first_load") {
    main = <FirstLoadPanel ws={view} state={shownState} detail={shownView?.detail ?? null} />;
  } else {
    main = <Text>{view.inputs?.row_count ? `The file was uploaded and read: ${view.inputs.row_count} rows.` : "The file was uploaded and read."}</Text>;
  }

  return (
    <section className="workspace-page">
      <div className="workspace-heading">
        <div><div className="breadcrumbs"><a href="#/overview">Home</a> / <a href="#/onboardings">Onboardings</a> / {ws.feed_code}</div>
          <Title order={2}>Onboarding {ws.feed_code}</Title>
          {ws.inputs && <Text size="sm" c="dimmed">
            {ws.inputs.row_count !== null && `${ws.inputs.row_count} rows`}
            {ws.inputs.row_count !== null && ws.inputs.column_count !== null && " · "}
            {ws.inputs.column_count !== null && `${ws.inputs.column_count} columns`}
          </Text>}
        </div>
        <a href="#/onboardings" className="amz-button">Save and exit</a>
      </div>
      {fourEyes && (
        <div role="note" style={{ background: "var(--amz-info)", color: "var(--amz-on-bright)", borderRadius: 6, padding: "8px 12px", marginBottom: 12 }}>
          <div>Four-eyes is on: a second person decides the next step</div>
          <div>Use Feeds for the multi-role flow</div>
        </div>
      )}
      <Stepper steps={ws.steps} current={current} viewed={shown} onOpen={(s) => { setViewed(s === current ? null : s); setViewedWs(null); }} />
      <div className="workspace-body">
        <div className="workspace-step-content">
          <div className="workspace-step-heading">
            <Title order={3} size="h4">{STEP_LABELS[shown]}</Title>
            <StateBadge state={shownState} />
          </div>
          {main}
          {view && atCurrent && (state === "ready" || state === "needs_decision") && (
            <AddItem step={shown} ws={view} actions={actions} />
          )}
        </div>
        <div>
          <AgentPanel step={shown} items={items} notes={view?.agent_notes ?? []} inputs={view?.inputs ?? null}
                      timeline={timeline} people={people} />
        </div>
      </div>
      <StickyFooter data-testid="workspace-footer">
        <Button data-testid="workspace-back" variant="default" disabled={!backTo || busy}
                onClick={() => backTo && void act(() => goBack(id, backTo, devUser))}>
          {backTo ? `Go back to ${STEP_LABELS[backTo]}` : "Back"}
        </Button>
        {!backTo && <Text size="sm" c="dimmed">Pick an earlier step in the list to go back to it</Text>}
        <div style={{ flex: 1 }} />
        <span data-testid="workspace-reason" role="status">
          {note ?? reason ?? ""}
        </span>
        {preAccepted > 0 && (
          <span data-testid="workspace-preaccepted">{`Approves ${preAccepted} pre-accepted item${preAccepted === 1 ? "" : "s"}`}</span>
        )}
        <Button data-testid="workspace-approve" disabled={!ws.can_approve || busy || !atCurrent} onClick={() => void approve()}>
          {approveLabel}
        </Button>
      </StickyFooter>
    </section>
  );
}
