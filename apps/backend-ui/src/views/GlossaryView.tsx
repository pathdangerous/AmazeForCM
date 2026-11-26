import { Anchor, Button, Group, Radio, Stack, Table, Text, TextInput, Title } from "@mantine/core";
import { useEffect, useState } from "react";
import DataGrid from "../../../ui-shared/DataGrid";
import { LoadingSkeleton } from "../../../ui-shared/phase711";
import {
  apiGet, decideProposal, describeError, words, type ConfigProposal, type DomainCount, type ElementDetail,
  type ElementOption, type GlossaryElement,
} from "../api";
import { formatWhen } from "../theme";
import GlossaryUploads from "./GlossaryUploads";

function domainLabel(domains: DomainCount[], domain: string): string {
  return domains.find((d) => d.domain === domain)?.label ?? domain;
}

function optionText(option: ElementOption): string {
  if (option.kind === "alias") return `alias ${option.alias} on ${option.key}`;
  const e = option.element;
  return e ? `new element ${e.name} (${e.key}, ${e.data_type}, ${words(e.domain)}): ${e.definition}` : option.key;
}

const WHAT: Record<string, string> = {
  proposed: "A new element",
  duplicate_alias: "An alias on an existing element",
  conflict_to_owner: "A conflict for the glossary owner",
};

// The proposal's feed and column, shared by the pending card and the decided row.
function Subject({ proposal }: { proposal: ConfigProposal }) {
  return (
    <>
      <strong>{WHAT[proposal.payload.outcome ?? ""] ?? "A proposal"}</strong> for column{" "}
      <code>{proposal.column}</code> of{" "}
      {proposal.feed_deleted ? proposal.feed_name : <Anchor href={`#/feed/${proposal.feed_id}`}>{proposal.feed_name}</Anchor>}{" "}
      <span className="muted">({proposal.feed_code}{proposal.feed_deleted && ", feed deleted"})</span>
    </>
  );
}

// One glossary_element proposal (phase 5 plan, section 10.2): the feed and column, what is proposed, the agent's
// reason, and Approve with the option chosen or Reject with a note, for the glossary owner only.
function GlossaryProposal({ proposal, devUser, canDecide, onDecided }: {
  proposal: ConfigProposal; devUser: string; canDecide: boolean; onDecided: (message: string) => void;
}) {
  const options = proposal.payload.options ?? [];
  const recommended = typeof proposal.payload.recommended === "number" ? proposal.payload.recommended : null;
  const [choice, setChoice] = useState<number | null>(recommended);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const clashes = proposal.payload.clashes ?? [];

  async function decide(decision: "approved" | "rejected") {
    setBusy(true);
    try {
      await decideProposal(proposal.id, decision, decision === "approved" ? choice : null, note, devUser);
      setError(null);
      onDecided(decision === "approved"
        ? `Approved for ${proposal.feed_code}.${proposal.column}: ${choice !== null ? optionText(options[choice]) : ""}.`
        : `Rejected for ${proposal.feed_code}.${proposal.column}.`);
    } catch (err: unknown) {
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="detail" data-testid="glossary-proposal" data-column={proposal.column}>
      <div><Subject proposal={proposal} /></div>
      {clashes.length > 0 && (
        // P14: an alias joins the element it names; only a conflict clashes
        <div>{proposal.payload.outcome === "duplicate_alias" ? "Same as" : "Clashes with"}:{" "}
          {clashes.map((c, i) => <span key={c}>{i > 0 && ", "}<code>{c}</code></span>)}</div>
      )}
      <Text className="muted" my={6}>GlossaryBuilderAgent's reason (it decides nothing): {proposal.reason}</Text>
      {options.length === 0 ? (
        <Text className="muted">No option to approve: the glossary owner closes it with a note.</Text>
      ) : (
        <Radio.Group value={choice === null ? "" : String(choice)} onChange={(v) => setChoice(Number(v))}
                     aria-label={`Options for ${proposal.column}`}>
          <Stack gap={4}>
            {options.map((o, i) => (
              <Radio key={i} name={`option-${proposal.id}`} value={String(i)} disabled={!canDecide}
                     label={<>{optionText(o)}{i === recommended && <span className="muted"> (recommended)</span>}</>} />
            ))}
          </Stack>
        </Radio.Group>
      )}
      {canDecide && (
        <Group gap="xs" mt="sm">
          <Button size="xs" disabled={busy || choice === null || options.length === 0}
                  onClick={() => decide("approved")} data-testid="approve">Approve</Button>
          <TextInput size="xs" w={220} aria-label={`Note for ${proposal.column}`} placeholder="Note (needed to reject)"
                     value={note} maxLength={500} onChange={(e) => setNote(e.currentTarget.value)} />
          <Button size="xs" variant="default" disabled={busy || !note.trim()} onClick={() => decide("rejected")}
                  data-testid="reject">Reject</Button>
        </Group>
      )}
      {error && <Text className="bad" role="alert">{error}</Text>}
    </div>
  );
}

// P16: one decided proposal: what it was, the decision, who decided and the note.
function DecidedRow({ proposal, name }: { proposal: ConfigProposal; name: (id: string) => string }) {
  const last = proposal.decisions[proposal.decisions.length - 1];
  return (
    <div className="detail" data-testid="glossary-decided-row" data-column={proposal.column}>
      <div><Subject proposal={proposal} /></div>
      <div>
        <span className={proposal.status === "approved" ? "ok" : "bad"}>
          {proposal.status === "approved" ? "Approved" : "Rejected"}
        </span>
        {last && <> by {name(last.decided_by)}{last.note ? `: ${last.note}` : ""}</>}
        {proposal.decided_at && <span className="muted"> · {formatWhen(proposal.decided_at)}</span>}
      </div>
    </div>
  );
}

export default function GlossaryView({ devUser, canDecide, changed, people, onChange, initialKey = null }: {
  devUser: string; canDecide: boolean; changed: number; people: Record<string, string>; onChange: () => void;
  initialKey?: string | null; // phase 7.22: an element link (#/glossary/<key>) opens that element
}) {
  const [proposals, setProposals] = useState<ConfigProposal[] | null>(null);
  const [show, setShow] = useState<"pending" | "decided">("pending");
  const [decidedList, setDecidedList] = useState<ConfigProposal[] | null>(null);
  const [decided, setDecided] = useState<string | null>(null);
  const [domains, setDomains] = useState<DomainCount[]>([]);
  const [domain, setDomain] = useState<string>(""); // "" = all domains
  const [query, setQuery] = useState("");
  const [elements, setElements] = useState<GlossaryElement[] | null>(null);
  const [selectedKey, setSelectedKey] = useState<string | null>(initialKey);
  useEffect(() => { if (initialKey) setSelectedKey(initialKey); }, [initialKey]); // the hash changed while the page is open
  const [detail, setDetail] = useState<ElementDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const name = (id: string) => people[id] ?? id;

  useEffect(() => {
    if (!devUser) return;
    let current = true;
    apiGet<DomainCount[]>("/api/amaze/glossary/domains", devUser)
      .then((d) => current && setDomains(d))
      .catch((err: unknown) => current && setError(describeError(err)));
    apiGet<ConfigProposal[]>("/api/amaze/proposals?kind=glossary_element&status=pending", devUser)
      .then((p) => current && setProposals(p))
      .catch((err: unknown) => current && setError(describeError(err)));
    return () => {
      current = false;
    };
  }, [devUser, changed]);

  // P16: the decided proposals, read when the Decided list is shown (the existing status filter), newest first
  useEffect(() => {
    if (!devUser || show !== "decided") return;
    let current = true;
    Promise.all(["approved", "rejected"].map((status) =>
      apiGet<ConfigProposal[]>(`/api/amaze/proposals?kind=glossary_element&status=${status}`, devUser)))
      .then(([approved, rejected]) => current && setDecidedList([...approved, ...rejected].sort(
        (a, b) => (b.decided_at ?? "").localeCompare(a.decided_at ?? ""))))
      .catch((err: unknown) => current && setError(describeError(err)));
    return () => {
      current = false;
    };
  }, [devUser, show, changed]);

  // Search waits briefly after typing; a newer request always wins over an older one.
  useEffect(() => {
    if (!devUser) return;
    let current = true;
    const timer = window.setTimeout(() => {
      const params = new URLSearchParams();
      if (domain) params.set("domain", domain);
      if (query.trim()) params.set("q", query.trim());
      apiGet<GlossaryElement[]>(`/api/amaze/glossary/elements?${params}`, devUser)
        .then((list) => {
          if (!current) return;
          setElements(list);
          setError(null);
        })
        .catch((err: unknown) => current && setError(describeError(err)));
    }, 200);
    return () => {
      current = false;
      window.clearTimeout(timer);
    };
  }, [devUser, domain, query, changed]);

  useEffect(() => {
    if (!devUser || !selectedKey) {
      setDetail(null);
      return;
    }
    let current = true;
    apiGet<ElementDetail>(`/api/amaze/glossary/elements/${encodeURIComponent(selectedKey)}`, devUser)
      .then((d) => current && setDetail(d))
      .catch((err: unknown) => current && setError(describeError(err)));
    return () => {
      current = false;
    };
  }, [devUser, selectedKey, changed]);

  const total = domains.reduce((sum, d) => sum + d.count, 0);

  return (
    <section>
      <Title order={2} mb="md">Glossary</Title>
      <Group justify="space-between" mb="xs">
        <Title order={3}>Proposals</Title>
        <Button.Group aria-label="Proposals to show">
          {(["pending", "decided"] as const).map((s) => (
            <Button key={s} size="xs" variant={show === s ? "filled" : "default"} aria-pressed={show === s}
                    onClick={() => setShow(s)}>
              {s === "pending" ? "Pending" : "Decided"}
            </Button>
          ))}
        </Button.Group>
      </Group>
      {decided && <Text className="ok" role="status" data-testid="glossary-decided">{decided}</Text>}
      {show === "pending" && proposals !== null && proposals.length === 0 && (
        <div className="empty" data-testid="no-glossary-proposals">No proposal waits for the glossary owner.</div>
      )}
      {show === "pending" && proposals !== null && proposals.length > 0 && (
        <div data-testid="glossary-proposals">
          {!canDecide && <Text className="muted">Only the glossary owner decides these.</Text>}
          {proposals.map((p) => (
            <GlossaryProposal key={p.id} proposal={p} devUser={devUser} canDecide={canDecide}
                              onDecided={(message) => (setDecided(message), onChange())} />
          ))}
        </div>
      )}
      {show === "decided" && decidedList !== null && (decidedList.length === 0
        ? <div className="empty" data-testid="no-glossary-decided">No proposal has been decided yet.</div>
        : <div data-testid="glossary-decided-list">
            {decidedList.map((p) => <DecidedRow key={p.id} proposal={p} name={name} />)}
          </div>)}

      <GlossaryUploads devUser={devUser} canDecide={canDecide} people={people} changed={changed} domains={domains}
                       onChange={onChange} />

      <Title order={3} mt="xl" mb="xs">Elements</Title>
      <div className="toolbar">
        <Button.Group aria-label="Domain">
          <Button size="xs" variant={domain === "" ? "filled" : "default"} aria-pressed={domain === ""}
                  onClick={() => setDomain("")}>
            All {total}
          </Button>
          {domains.map((d) => (
            <Button key={d.domain} size="xs" variant={domain === d.domain ? "filled" : "default"}
                    aria-pressed={domain === d.domain} onClick={() => setDomain(d.domain)}>
              {d.label} {d.count}
            </Button>
          ))}
        </Button.Group>
        <TextInput type="search" size="xs" w={300} aria-label="Search the glossary"
                   placeholder="Search key, name, alias or definition" value={query}
                   onChange={(e) => setQuery(e.currentTarget.value)} data-testid="glossary-search" />
      </div>

      {error && <Text className="bad" role="alert">{error}</Text>}

      {elements !== null && elements.length === 0 && <div className="empty">No elements match.</div>}
      {elements === null && !error && <LoadingSkeleton label="Loading glossary elements" />}

      {elements !== null && elements.length > 0 && (
        <DataGrid rows={elements} rowKey={(element) => element.key} aria-label="Glossary elements" data-testid="glossary-table"
          pageSize={10} columns={[
            { key: "name", label: "Name", value: (e) => e.name },
            { key: "key", label: "Key", value: (e) => e.key, render: (e) => <code>{e.key}</code> },
            { key: "domain", label: "Domain", value: (e) => domainLabel(domains, e.domain) },
            { key: "type", label: "Type", value: (e) => e.data_type },
            { key: "definition", label: "Definition", value: (e) => e.definition },
            { key: "aliases", label: "Aliases", value: (e) => e.aliases.join(", ") },
          ]} rowProps={(e) => ({
            className: "clickable", "aria-selected": selectedKey === e.key, tabIndex: 0,
            onClick: () => setSelectedKey(e.key === selectedKey ? null : e.key),
            onKeyDown: (event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault(); setSelectedKey(e.key === selectedKey ? null : e.key);
              }
            },
          })} />
      )}

      {detail && (
        <div className="detail" data-testid="element-detail">
          <Title order={3} mb="xs">
            {detail.element.name} <code>{detail.element.key}</code>
          </Title>
          <dl>
            <dt>Domain</dt>
            <dd>{domainLabel(domains, detail.element.domain)}</dd>
            <dt>Type</dt>
            <dd>{detail.element.data_type}</dd>
            <dt>Definition</dt>
            <dd>{detail.element.definition}</dd>
            <dt>Aliases</dt>
            <dd>{detail.element.aliases.join(", ") || "none"}</dd>
            <dt>Version</dt>
            <dd>{detail.element.version}</dd>
            <dt>Created by</dt>
            <dd>{name(detail.element.created_by)}</dd>
          </dl>
          <Title order={4} mt="md" mb="xs">History</Title>
          <Table verticalSpacing={4}>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Version</Table.Th><Table.Th>Created</Table.Th><Table.Th>Created by</Table.Th>
                <Table.Th>Proposal</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {detail.history.map((v) => (
                <Table.Tr key={v.id}>
                  <Table.Td>{v.version}</Table.Td>
                  <Table.Td>{formatWhen(v.created_at)}</Table.Td>
                  <Table.Td>{name(v.created_by)}</Table.Td>
                  <Table.Td className="muted">{v.proposal_id ?? "seed"}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </div>
      )}
    </section>
  );
}
