import { useState, type FormEvent } from "react";
import {
  apiGet,
  apiPost,
  describeError,
  type Assistance,
  type Discovery,
  type FindSourceResponse,
  type RequestResult,
} from "../api";

// Step 17 search, then "Request this data" (routing checks and step 01). Phase 2 plan, section 10.
export default function SearchView({ devUser }: { devUser: string }) {
  const [query, setQuery] = useState("");
  const [searched, setSearched] = useState("");
  const [found, setFound] = useState<Discovery | null>(null);
  const [elements, setElements] = useState<Set<string>>(new Set());
  const [terms, setTerms] = useState<Set<string>>(new Set());
  const [purpose, setPurpose] = useState("");
  const [result, setResult] = useState<RequestResult | null>(null);
  const [sourceResult, setSourceResult] = useState<FindSourceResponse | null>(null);
  const [assistance, setAssistance] = useState<Assistance | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // The read-only DiscoveryAssistant beside the search (phase 3 plan, assumption 38). It changes
  // nothing: its suggestions start unticked.
  async function onAssist() {
    setBusy(true);
    setError(null);
    try {
      setAssistance(await apiPost<Assistance>("/api/consumer/search/assist", { q: searched }, devUser));
    } catch (err) {
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  }

  async function onSearch(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setResult(null);
    setSourceResult(null);
    setAssistance(null);
    setFound(null); // never leave the previous search's results and ticks on screen
    try {
      const d = await apiGet<Discovery>(`/api/consumer/search?q=${encodeURIComponent(query)}`, devUser);
      setFound(d);
      setSearched(query);
      setElements(new Set(d.match.elements.filter((m) => m.ticked).map((m) => m.key)));
      setTerms(new Set());
      setPurpose("");
    } catch (err) {
      setFound(null);
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setSourceResult(null);
    try {
      const raised = await apiPost<RequestResult>("/api/consumer/requests", {
        query: searched, purpose, element_keys: [...elements], terms: [...terms],
      }, devUser);
      setResult(raised);
      // Right after a raise, look for a source (phase 3 plan, assumption 2). The raise stays as it is.
      const requirementId = raised.requirement?.requirement.id;
      if (requirementId) {
        setSourceResult(await apiPost<FindSourceResponse>(
          `/api/consumer/requirements/${requirementId}/find-source`, {}, devUser));
      }
    } catch (err) {
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  }

  const toggle = (set: Set<string>, update: (s: Set<string>) => void, value: string) => {
    const next = new Set(set);
    if (next.has(value)) next.delete(value);
    else next.add(value);
    update(next);
  };

  const candidates = found ? [...new Set(found.match.ambiguous.flatMap((a) => a.candidates))] : [];
  const elementChoices = found ? [...found.match.elements.map((m) => m.key), ...candidates.filter(
    (c) => !found.match.elements.some((m) => m.key === c))] : [];
  const suggested = (assistance?.suggestions ?? []).filter((s) => !elementChoices.includes(s.element_key));
  const nothingTicked = elements.size === 0 && terms.size === 0;

  return (
    <section>
      <h2>Search</h2>
      <form onSubmit={onSearch} className="row">
        <label htmlFor="q" className="visually-hidden">Search for data</label>
        <input id="q" data-testid="search-box" value={query} onChange={(e) => setQuery(e.target.value)}
               placeholder="for example: close price and volume" maxLength={500} />
        <button type="submit" disabled={busy || !devUser}>Search</button>
      </form>

      {error && <p className="bad" role="alert" data-testid="error">{error}</p>}

      {found && (
        <>
          {found.match.domains.length > 0 && (
            <p className="muted">Filtered to domain: {found.match.domains.join(", ").replaceAll("_", " ")}</p>
          )}
          {found.hits.length === 0 ? (
            <p data-testid="no-hits">No products hold this data.</p>
          ) : (
            <ul className="hits" data-testid="hits">
              {found.hits.map((h) => (
                <li key={h.product.code} data-testid={`hit-${h.product.code}`}>
                  <strong>{h.product.name}</strong>{" "}
                  <span className={h.outcome === "found" ? "badge ok" : "badge warn"}>
                    {h.outcome === "found" ? "Available" : "Access restricted"}
                  </span>
                  <div>{h.product.description}</div>
                  <div className="muted">Elements: {h.product.element_keys.join(", ")}</div>
                  {h.outcome === "not_entitled_visible" && <div className="route">{h.route}</div>}
                </li>
              ))}
            </ul>
          )}

          <form onSubmit={onSubmit} className="request" data-testid="request-form">
            <h3>Request this data</h3>
            {elementChoices.length > 0 && (
              <fieldset>
                <legend>Data items in the glossary</legend>
                {elementChoices.map((key) => (
                  <label key={key} className="choice">
                    <input type="checkbox" checked={elements.has(key)} onChange={() => toggle(elements, setElements, key)} />
                    {found.element_names[key] ?? key}{candidates.includes(key) ? " (one of several meanings)" : ""}
                  </label>
                ))}
              </fieldset>
            )}
            {found.match.terms.length > 0 && (
              <fieldset>
                <legend>Is this a data item you need?</legend>
                {found.match.terms.map((t) => (
                  <label key={t} className="choice">
                    <input type="checkbox" checked={terms.has(t)} onChange={() => toggle(terms, setTerms, t)} />
                    {t}
                  </label>
                ))}
                {!assistance && (
                  <button type="button" data-testid="assist" onClick={onAssist} disabled={busy}>
                    Ask the assistant
                  </button>
                )}
              </fieldset>
            )}
            {assistance && (
              <fieldset data-testid="suggestions">
                <legend>The assistant suggests</legend>
                {suggested.length === 0 && <p className="muted">No suggestion for these words.</p>}
                {suggested.map((s) => (
                  <div key={`${s.term}-${s.element_key}`}>
                    <label className="choice">
                      <input type="checkbox" data-testid={`suggested-${s.element_key}`}
                             checked={elements.has(s.element_key)}
                             onChange={() => toggle(elements, setElements, s.element_key)} />
                      {s.element_name} <span className="badge">suggested</span> for "{s.term}"
                    </label>
                    <div className="muted">{s.reason}</div>
                    {s.products.length > 0 && (
                      <div className="muted">
                        Held by: {s.products.map((h) => `${h.product.name}${h.outcome === "found" ? "" : " (access restricted)"}`).join(", ")}
                      </div>
                    )}
                  </div>
                ))}
              </fieldset>
            )}
            <label htmlFor="purpose">Purpose (optional)</label>
            <textarea id="purpose" value={purpose} onChange={(e) => setPurpose(e.target.value)} maxLength={2000} rows={2} />
            <button type="submit" data-testid="submit" disabled={busy || nothingTicked || !searched.trim()}>
              Submit request
            </button>
            {nothingTicked && <span className="muted"> Tick at least one item.</span>}
          </form>
        </>
      )}

      {result && (
        <div className={result.routing.product?.entitled === false ? "result warn" : "result"} role="status"
             data-testid="result">
          <p><strong data-testid="result-message">{result.message}</strong></p>
          {result.notes.length > 0 && <ul>{result.notes.map((n) => <li key={n}>{n}</li>)}</ul>}
          {sourceResult && (
            <p data-testid="find-source-message"
               className={sourceResult.fetch?.outcome === "source_down" ? "warn" : sourceResult.fetch ? "ok" : undefined}>
              {sourceResult.message}
            </p>
          )}
          {result.requirement && (
            <p><a href={`#/requirement/${result.requirement.requirement.id}`}>See this requirement</a></p>
          )}
          <p><a href="#/requests">See My requests</a></p>
        </div>
      )}
    </section>
  );
}
