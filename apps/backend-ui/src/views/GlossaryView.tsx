import { useEffect, useState } from "react";
import { apiGet, describeError, type DomainCount, type ElementDetail, type GlossaryElement } from "../api";

function domainLabel(domains: DomainCount[], domain: string): string {
  return domains.find((d) => d.domain === domain)?.label ?? domain;
}

export default function GlossaryView({ devUser }: { devUser: string }) {
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
    return () => {
      current = false;
    };
  }, [devUser]);

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
  }, [devUser, domain, query]);

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
  }, [devUser, selectedKey]);

  const total = domains.reduce((sum, d) => sum + d.count, 0);

  return (
    <section>
      <h2>Glossary</h2>
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
