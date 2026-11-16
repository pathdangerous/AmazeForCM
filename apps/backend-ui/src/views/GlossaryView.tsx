import { useEffect, useState } from "react";
import {
  apiGet, decideProposal, describeError, words, type ConfigProposal, type DomainCount, type ElementDetail,
  type ElementOption, type GlossaryElement,
} from "../api";

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
      <div>
        <strong>{WHAT[proposal.payload.outcome ?? ""] ?? "A proposal"}</strong> for column{" "}
        <code>{proposal.column}</code> of <a href={`#/feed/${proposal.feed_id}`}>{proposal.feed_name}</a>{" "}
        <span className="muted">({proposal.feed_code})</span>
      </div>
      {(proposal.payload.clashes ?? []).length > 0 && (
        <div>Clashes with: {(proposal.payload.clashes ?? []).map((c) => <code key={c} style={{ marginRight: 6 }}>{c}</code>)}</div>
      )}
      <p className="muted" style={{ margin: "6px 0" }}>GlossaryBuilderAgent's reason (it decides nothing): {proposal.reason}</p>
      {options.length === 0 ? (
        <p className="muted">No option to approve: the glossary owner closes it with a note.</p>
      ) : (
        <div role="radiogroup" aria-label={`Options for ${proposal.column}`}>
          {options.map((o, i) => (
            <div key={i}>
              <label>
                <input type="radio" name={`option-${proposal.id}`} checked={choice === i} disabled={!canDecide}
                       onChange={() => setChoice(i)} /> {optionText(o)}
                {i === recommended && <span className="muted"> (recommended)</span>}
              </label>
            </div>
          ))}
        </div>
      )}
      {canDecide && (
        <div className="toolbar" style={{ marginTop: 8, marginBottom: 0 }}>
          <button type="button" disabled={busy || choice === null || options.length === 0}
                  onClick={() => decide("approved")} data-testid="approve">Approve</button>
          <input aria-label={`Note for ${proposal.column}`} placeholder="Note (needed to reject)" value={note}
                 maxLength={500} onChange={(e) => setNote(e.target.value)} />
          <button type="button" disabled={busy || !note.trim()} onClick={() => decide("rejected")}
                  data-testid="reject">Reject</button>
        </div>
      )}
      {error && <p className="bad" role="alert">{error}</p>}
    </div>
  );
}

export default function GlossaryView({ devUser, canDecide, changed, onChange }: {
  devUser: string; canDecide: boolean; changed: number; onChange: () => void;
}) {
  const [proposals, setProposals] = useState<ConfigProposal[] | null>(null);
  const [decided, setDecided] = useState<string | null>(null);
  const [domains, setDomains] = useState<DomainCount[]>([]);
  const [domain, setDomain] = useState<string>(""); // "" = all domains
  const [query, setQuery] = useState("");
  const [elements, setElements] = useState<GlossaryElement[] | null>(null);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [detail, setDetail] = useState<ElementDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

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
      <h2>Glossary</h2>
      <h3>Proposals</h3>
      {decided && <p className="ok" role="status" data-testid="glossary-decided">{decided}</p>}
      {proposals !== null && proposals.length === 0 && (
        <div className="empty" data-testid="no-glossary-proposals">No proposal waits for the glossary owner.</div>
      )}
      {proposals !== null && proposals.length > 0 && (
        <div data-testid="glossary-proposals">
          {!canDecide && <p className="muted">Only the glossary owner decides these.</p>}
          {proposals.map((p) => (
            <GlossaryProposal key={p.id} proposal={p} devUser={devUser} canDecide={canDecide}
                              onDecided={(message) => (setDecided(message), onChange())} />
          ))}
        </div>
      )}

      <h3>Elements</h3>
      <div className="toolbar">
        <div className="filters" role="group" aria-label="Domain">
          <button type="button" aria-pressed={domain === ""} onClick={() => setDomain("")}>
            All {total}
          </button>
          {domains.map((d) => (
            <button key={d.domain} type="button" aria-pressed={domain === d.domain} onClick={() => setDomain(d.domain)}>
              {d.label} {d.count}
            </button>
          ))}
        </div>
        <input
          type="search"
          aria-label="Search the glossary"
          placeholder="Search key, name, alias or definition"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          data-testid="glossary-search"
        />
      </div>

      {error && <p className="bad" role="alert">{error}</p>}

      {elements !== null && elements.length === 0 && <div className="empty">No elements match.</div>}

      {elements !== null && elements.length > 0 && (
        <div className="table-wrap">
          <table data-testid="glossary-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Key</th>
                <th>Domain</th>
                <th>Type</th>
                <th>Definition</th>
                <th>Aliases</th>
              </tr>
            </thead>
            <tbody>
              {elements.map((e) => (
                <tr
                  key={e.key}
                  className="clickable"
                  aria-selected={selectedKey === e.key}
                  tabIndex={0}
                  onClick={() => setSelectedKey(e.key === selectedKey ? null : e.key)}
                  onKeyDown={(ev) => ev.key === "Enter" && setSelectedKey(e.key === selectedKey ? null : e.key)}
                >
                  <td>{e.name}</td>
                  <td><code>{e.key}</code></td>
                  <td>{domainLabel(domains, e.domain)}</td>
                  <td>{e.data_type}</td>
                  <td>{e.definition}</td>
                  <td>{e.aliases.join(", ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {detail && (
        <div className="detail" data-testid="element-detail">
          <h3>
            {detail.element.name} <code>{detail.element.key}</code>
          </h3>
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
            <dd>{detail.element.created_by}</dd>
          </dl>
          <h4>History</h4>
          <table>
            <thead>
              <tr>
                <th>Version</th>
                <th>Created</th>
                <th>Created by</th>
                <th>Proposal</th>
              </tr>
            </thead>
            <tbody>
              {detail.history.map((v) => (
                <tr key={v.id}>
                  <td>{v.version}</td>
                  <td>{new Date(v.created_at).toLocaleString()}</td>
                  <td>{v.created_by}</td>
                  <td className="muted">{v.proposal_id ?? "seed"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
