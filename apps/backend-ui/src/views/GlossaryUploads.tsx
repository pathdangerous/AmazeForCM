import { Button, Group, NativeSelect, Stack, Table, Text, TextInput, Title } from "@mantine/core";
import { useEffect, useMemo, useState } from "react";
import {
  apiGet, decideImports, describeError, getGlossaryTemplate, uploadGlossary, words, type DomainCount, type ImportItem,
  type GlossaryUploadPage, type GlossaryUploadRow, type GlossaryUploadSummary, type GlossaryUploaded,
} from "../api";
import { formatWhen } from "../theme";

// Phase 7.19: bring many glossary elements in at once. Every new or changed row is a proposal the glossary owner
// approves or rejects (rule 1); an approved row is a new element or the element's next version (rule 7).
const DATA_TYPES = ["string", "integer", "decimal", "date", "datetime", "boolean"];
const CSV_HEADER = "key,name,domain,data_type,definition,aliases";

const quote = (value: string) => `"${value.replaceAll('"', '""')}"`;
const labelText = (r: GlossaryUploadRow) => (r.label === "alias_conflict" && r.detail ? `alias_conflict (${r.detail})` : r.label);

function whatText(r: GlossaryUploadRow): string {
  if (r.label === "update") {
    const used = r.used_by_mappings ? `; used by ${r.used_by_mappings} ${r.used_by_mappings === 1 ? "mapping" : "mappings"}` : "";
    return `changes ${r.changes.join(", ")}${used}`;
  }
  if (r.label === "unchanged") return "the same as the current version";
  if (r.label === "new") return "a new element";
  return r.label === "alias_conflict" ? "another element owns a name here" : (r.detail ?? "");
}

function saveAs(text: string, name: string) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/csv;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  URL.revokeObjectURL(url);
}

function AddElement({ devUser, domains, onUploaded }: {
  devUser: string; domains: DomainCount[]; onUploaded: (done: GlossaryUploaded) => void;
}) {
  const blank = { key: "", name: "", domain: domains[0]?.domain ?? "", data_type: "string", definition: "", aliases: "" };
  const [form, setForm] = useState(blank);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const set = (field: keyof typeof blank) => (value: string) => setForm((f) => ({ ...f, [field]: value }));
  const ready = form.key.trim() && form.name.trim() && form.definition.trim() && (form.domain || domains[0]?.domain);

  async function send() {
    setBusy(true);
    const values = [form.key, form.name, form.domain || domains[0]?.domain || "", form.data_type, form.definition, form.aliases];
    const csv = `${CSV_HEADER}\r\n${values.map((v) => quote(v.trim())).join(",")}\r\n`;
    try {
      const answer = await uploadGlossary(new Blob([csv], { type: "text/csv" }), "add-element.csv", devUser);
      if (answer.outcome === "file_refused") {
        setError(answer.detail);
        return;
      }
      setError(null);
      const row = answer.rows[0];
      setResult(row.proposal_id
        ? `Proposed ${row.label} element ${row.key}: it waits for the glossary owner's decision below.`
        : `Not proposed: ${labelText(row)}${row.label === "invalid" ? `: ${row.detail}` : ""}.`);
      if (row.proposal_id) setForm(blank);
      onUploaded(answer);
    } catch (err: unknown) {
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="detail" data-testid="add-element-form">
      <Stack gap="xs">
        <Group align="flex-end" gap="xs">
          <TextInput label="Key" size="xs" value={form.key} maxLength={80} onChange={(e) => set("key")(e.currentTarget.value)} />
          <TextInput label="Name" size="xs" value={form.name} maxLength={200} onChange={(e) => set("name")(e.currentTarget.value)} />
          <NativeSelect label="Domain" size="xs" value={form.domain || domains[0]?.domain || ""}
                        data={domains.map((d) => ({ value: d.domain, label: d.label }))}
                        onChange={(e) => set("domain")(e.currentTarget.value)} />
          <NativeSelect label="Data type" size="xs" value={form.data_type} data={DATA_TYPES}
                        onChange={(e) => set("data_type")(e.currentTarget.value)} />
        </Group>
        <TextInput label="Definition" size="xs" value={form.definition} maxLength={500}
                   onChange={(e) => set("definition")(e.currentTarget.value)} />
        <TextInput label="Aliases (separated by ;)" size="xs" value={form.aliases} maxLength={500}
                   onChange={(e) => set("aliases")(e.currentTarget.value)} />
        <Group>
          <Button size="xs" disabled={busy || !ready} onClick={send}>Propose this element</Button>
        </Group>
      </Stack>
      {result && <Text className="ok" role="status" data-testid="add-element-result" mt="xs">{result}</Text>}
      {error && <Text className="bad" role="alert" mt="xs">{error}</Text>}
    </div>
  );
}

export default function GlossaryUploads({ devUser, canDecide, people, changed, domains, onChange }: {
  devUser: string; canDecide: boolean; people: Record<string, string>; changed: number; domains: DomainCount[];
  onChange: () => void;
}) {
  const [uploads, setUploads] = useState<GlossaryUploadSummary[] | null>(null);
  const [selectedId, setSelectedId] = useState<string>("");
  const [page, setPage] = useState<GlossaryUploadPage | null>(null);
  const [reload, setReload] = useState(0);
  const [file, setFile] = useState<File | null>(null);
  const [filter, setFilter] = useState("");
  const [ticked, setTicked] = useState<Set<string>>(new Set());
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [adding, setAdding] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<string[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const name = (id: string) => people[id] ?? id;

  useEffect(() => {
    if (!devUser) return;
    let current = true;
    apiGet<GlossaryUploadSummary[]>("/api/amaze/glossary/uploads", devUser)
      .then((list) => {
        if (!current) return;
        setUploads(list);
        setSelectedId((id) => id || list[0]?.id || "");
      })
      .catch((err: unknown) => current && setError(describeError(err)));
    return () => {
      current = false;
    };
  }, [devUser, changed, reload]);

  useEffect(() => {
    if (!devUser || !selectedId) {
      setPage(null);
      return;
    }
    let current = true;
    apiGet<GlossaryUploadPage>(`/api/amaze/glossary/uploads/${selectedId}`, devUser)
      .then((p) => current && setPage(p))
      .catch((err: unknown) => current && setError(describeError(err)));
    return () => {
      current = false;
    };
  }, [devUser, selectedId, changed, reload]);

  const shown = useMemo(() => (page?.rows ?? []).filter((r) => !filter || r.label === filter), [page, filter]);
  const pendingShown = shown.filter((r) => r.status === "pending" && r.proposal_id);
  const keyOf = (proposalId: string) => page?.rows.find((r) => r.proposal_id === proposalId)?.key ?? proposalId;

  function afterUpload(done: GlossaryUploaded) {
    setSelectedId(done.upload_id);
    setFilter("");
    setTicked(new Set());
    setReload((n) => n + 1);
  }

  async function upload() {
    if (!file) return;
    setBusy(true);
    try {
      const answer = await uploadGlossary(file, file.name, devUser);
      if (answer.outcome === "file_refused") {
        setError(answer.detail);
        setMessage(null);
        return;
      }
      setError(null);
      const counts = Object.entries(answer.counts).map(([label, n]) => `${n} ${label}`).join(", ");
      setMessage(`Uploaded ${answer.file_name}: ${counts}. ${answer.proposals} proposal${answer.proposals === 1 ? "" : "s"} wait for the glossary owner.`);
      setFile(null);
      afterUpload(answer);
    } catch (err: unknown) {
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  }

  async function template() {
    try {
      saveAs(await getGlossaryTemplate(devUser), "glossary_template.csv");
      setError(null);
    } catch (err: unknown) {
      setError(describeError(err));
    }
  }

  async function decide(decision: "approved" | "rejected") {
    setBusy(true);
    try {
      const { items } = await decideImports([...ticked], decision, note, devUser);
      const count = (o: ImportItem["outcome"]) => items.filter((i) => i.outcome === o).length;
      const refused = items.filter((i) => i.outcome === "refused") as Extract<ImportItem, { outcome: "refused" }>[];
      setOutcome([`Approved ${count("approved")} · Rejected ${count("rejected")} · Refused ${refused.length}`,
                  ...refused.map((i) => `${keyOf(i.proposal_id)}: ${i.detail}`)]);
      setError(null);
      setTicked(new Set());
      setNote("");
      setReload((n) => n + 1);
      onChange();
    } catch (err: unknown) {
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  }

  const toggle = (id: string) => setTicked((t) => {
    const next = new Set(t);
    if (!next.delete(id)) next.add(id);
    return next;
  });
  const allTicked = pendingShown.length > 0 && pendingShown.every((r) => ticked.has(r.proposal_id as string));
  const labels = Object.entries(page?.counts ?? {});

  return (
    <section>
      <Title order={3} mt="xl" mb="xs">Upload glossary file</Title>
      <Text className="muted" mb="xs">
        A CSV or Excel file with the columns key, name, domain, data_type, definition and aliases (aliases separated by ;).
        Each new or changed row becomes a proposal; nothing joins the glossary until the glossary owner approves it.
      </Text>
      {canDecide ? (
        <Group gap="xs" align="center">
          <input type="file" accept=".csv,.xlsx" aria-label="Glossary file" data-testid="glossary-file"
                 onChange={(e) => setFile(e.currentTarget.files?.[0] ?? null)} />
          <Button size="xs" disabled={!file || busy} onClick={upload} data-testid="upload-glossary">Upload glossary file</Button>
          <Button size="xs" variant="default" onClick={template}>Download template</Button>
          <Button size="xs" variant="default" aria-expanded={adding} onClick={() => setAdding((a) => !a)}>Add element</Button>
        </Group>
      ) : (
        <Group gap="xs" align="center">
          <Text className="muted">Only the glossary owner uploads and decides.</Text>
          <Button size="xs" variant="default" onClick={template}>Download template</Button>
        </Group>
      )}
      {canDecide && adding && <AddElement devUser={devUser} domains={domains} onUploaded={afterUpload} />}
      {message && <Text className="ok" role="status" mt="xs" data-testid="glossary-upload-message">{message}</Text>}
      {error && <Text className="bad" role="alert" mt="xs">{error}</Text>}

      {uploads !== null && uploads.length === 0 && <div className="empty">No glossary file has been uploaded yet.</div>}
      {uploads !== null && uploads.length > 0 && (
        <>
          <Group gap="md" mt="md" align="flex-end">
            <NativeSelect label="Uploaded file" size="xs" value={selectedId}
                          onChange={(e) => { setSelectedId(e.currentTarget.value); setFilter(""); setTicked(new Set()); }}
                          data={uploads.map((u) => ({ value: u.id, label: `${u.file_name} · ${formatWhen(u.created_at)} · ${name(u.uploaded_by)}` }))} />
            <NativeSelect aria-label="Filter by label" size="xs" value={filter} onChange={(e) => setFilter(e.currentTarget.value)}
                          data={[{ value: "", label: `All labels (${page?.rows.length ?? 0})` },
                                 ...labels.map(([l, n]) => ({ value: l, label: `${words(l)} (${n})` }))]} />
          </Group>
          {page && (
            <div data-testid="glossary-upload-rows" style={{ overflowX: "auto" }}>
              <Table verticalSpacing={4} mt="sm" aria-label="Rows of the uploaded file">
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>
                      {canDecide && <input type="checkbox" aria-label="Select all pending rows" disabled={pendingShown.length === 0}
                                           checked={allTicked}
                                           onChange={() => setTicked(allTicked ? new Set() : new Set(pendingShown.map((r) => r.proposal_id as string)))} />}
                    </Table.Th>
                    <Table.Th>Row</Table.Th><Table.Th>Key</Table.Th><Table.Th>Label</Table.Th><Table.Th>What</Table.Th>
                    <Table.Th>Decision</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {shown.map((r) => (
                    <Table.Tr key={r.row} data-testid="glossary-upload-row" data-label={r.label}>
                      <Table.Td>
                        {canDecide && r.status === "pending" && r.proposal_id && (
                          <input type="checkbox" aria-label={`Select row ${r.row}`} checked={ticked.has(r.proposal_id)}
                                 onChange={() => toggle(r.proposal_id as string)} />)}
                      </Table.Td>
                      <Table.Td>{r.row}</Table.Td>
                      <Table.Td><code>{r.key}</code></Table.Td>
                      <Table.Td>{labelText(r)}</Table.Td>
                      <Table.Td>{whatText(r)}</Table.Td>
                      <Table.Td>
                        {r.status === "pending" && "waits for the glossary owner"}
                        {(r.status === "approved" || r.status === "rejected") &&
                          <>{r.status === "approved" ? "Approved" : "Rejected"} by {name(r.decided_by ?? "")}{r.note ? `: ${r.note}` : ""}</>}
                      </Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </div>
          )}
          {canDecide && page && (
            <Group gap="xs" mt="sm">
              <Button size="xs" disabled={busy || ticked.size === 0} onClick={() => decide("approved")}
                      data-testid="approve-selected">Approve selected</Button>
              <TextInput size="xs" w={260} aria-label="Note for the selected rows" placeholder="Note (needed to reject)"
                         value={note} maxLength={500} onChange={(e) => setNote(e.currentTarget.value)} />
              <Button size="xs" variant="default" disabled={busy || ticked.size === 0 || !note.trim()}
                      onClick={() => decide("rejected")} data-testid="reject-selected">Reject selected</Button>
            </Group>
          )}
          {outcome && (
            <div role="status" data-testid="glossary-import-result" className="detail">
              <Text className="ok">{outcome[0]}</Text>
              {outcome.slice(1).map((line) => <Text key={line} className="bad">{line}</Text>)}
            </div>
          )}
        </>
      )}
    </section>
  );
}
