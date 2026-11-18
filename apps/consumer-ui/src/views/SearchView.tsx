import { Anchor, Badge, Button, Checkbox, Fieldset, Group, Stack, Text, Textarea, TextInput, Title } from "@mantine/core";
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
      <Title order={2} mb="md">Search</Title>
      <form onSubmit={onSearch}>
        <Group gap="sm" wrap="nowrap">
          <TextInput id="q" flex={1} aria-label="Search for data" data-testid="search-box" value={query}
                     onChange={(e) => setQuery(e.currentTarget.value)}
                     placeholder="for example: close price and volume" maxLength={500} />
          <Button type="submit" variant="filled" disabled={busy || !devUser}>Search</Button>
        </Group>
      </form>

      {error && <Text className="bad" role="alert" data-testid="error">{error}</Text>}

      {found && (
        <>
          {found.match.domains.length > 0 && (
            <Text className="muted" mt="sm">Filtered to domain: {found.match.domains.join(", ").replaceAll("_", " ")}</Text>
          )}
          {found.hits.length === 0 ? (
            <Text mt="sm" data-testid="no-hits">No products hold this data.</Text>
          ) : (
            <ul className="hits" data-testid="hits">
              {found.hits.map((h) => (
                <li key={h.product.code} data-testid={`hit-${h.product.code}`}>
                  <strong>{h.product.name}</strong>{" "}
                  {h.outcome === "found"
                    ? <Badge tt="none" color="var(--amz-success)">Available</Badge>
                    : <Badge tt="none" color="var(--amz-warning)" c="var(--amz-on-bright)">Access restricted</Badge>}
                  <div>{h.product.description}</div>
                  <div className="muted">Elements: {h.product.element_keys.join(", ")}</div>
                  {h.outcome === "not_entitled_visible" && <div className="route">{h.route}</div>}
                </li>
              ))}
            </ul>
          )}

          <form onSubmit={onSubmit} className="request" data-testid="request-form">
            <Title order={3}>Request this data</Title>
            {elementChoices.length > 0 && (
              <Fieldset legend="Data items in the glossary">
                <Stack gap={6}>
                  {elementChoices.map((key) => (
                    <Checkbox key={key} checked={elements.has(key)} onChange={() => toggle(elements, setElements, key)}
                              label={`${found.element_names[key] ?? key}${candidates.includes(key) ? " (one of several meanings)" : ""}`} />
                  ))}
                </Stack>
              </Fieldset>
            )}
            {found.match.terms.length > 0 && (
              <Fieldset legend="Is this a data item you need?">
                <Stack gap={6}>
                  {found.match.terms.map((t) => (
                    <Checkbox key={t} checked={terms.has(t)} onChange={() => toggle(terms, setTerms, t)} label={t} />
                  ))}
                </Stack>
                {!assistance && (
                  <Button size="xs" variant="default" mt="sm" data-testid="assist" onClick={onAssist} disabled={busy}>
                    Ask the assistant
                  </Button>
                )}
              </Fieldset>
            )}
            {assistance && (
              <Fieldset legend="The assistant suggests" data-testid="suggestions">
                {suggested.length === 0 && <Text className="muted">No suggestion for these words.</Text>}
                <Stack gap={8}>
                  {suggested.map((s) => (
                    <div key={`${s.term}-${s.element_key}`}>
                      <Checkbox data-testid={`suggested-${s.element_key}`} checked={elements.has(s.element_key)}
                                onChange={() => toggle(elements, setElements, s.element_key)}
                                label={<>{s.element_name} <Badge size="xs" variant="default" tt="none">suggested</Badge> for "{s.term}"</>} />
                      <div className="muted">{s.reason}</div>
                      {s.products.length > 0 && (
                        <div className="muted">
                          Held by: {s.products.map((h) => `${h.product.name}${h.outcome === "found" ? "" : " (access restricted)"}`).join(", ")}
                        </div>
                      )}
                    </div>
                  ))}
                </Stack>
              </Fieldset>
            )}
            <Textarea id="purpose" label="Purpose (optional)" value={purpose} maxLength={2000} autosize minRows={2}
                      onChange={(e) => setPurpose(e.currentTarget.value)} />
            <Group gap="sm">
              <Button type="submit" variant="filled" data-testid="submit"
                      disabled={busy || nothingTicked || !searched.trim()}>
                Submit request
              </Button>
              {nothingTicked && <span className="muted">Tick at least one item.</span>}
            </Group>
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
            <p><Anchor href={`#/requirement/${result.requirement.requirement.id}`}>See this requirement</Anchor></p>
          )}
          <p><Anchor href="#/requests">See My requests</Anchor></p>
        </div>
      )}
    </section>
  );
}
