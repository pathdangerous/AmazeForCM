import { Alert, Button, NativeSelect, Table, Text, TextInput, Title } from "@mantine/core";
import { useRef, useState } from "react";
import { LoadingSkeleton } from "../../../ui-shared/phase711";
import ScheduleForm, { EMPTY_SCHEDULE, scheduleBody, scheduleValid, type ScheduleValue } from "./ScheduleForm";
import {
  ApiError, confirmUpload, describeError, reinspect, uploadFile,
  type InspectedUpload, type NeedsImageExtraction, type UploadAnswer, type UploadRefusal, type User,
} from "../api";

const CODE_PATTERN = /^[a-z][a-z0-9_]*$/;
const CODE_HELP = "Use lower-case letters, digits and underscores, starting with a letter.";
const DEFAULT_CONDITIONS = { licence: "Internal use only", permitted_use: "Internal analysis", redistribution: "None" };

function stem(filename: string): string {
  return filename.replace(/\.[^.]*$/, "");
}

// "Security Master 2026.xlsx" -> "security_master_2026"; a name that cannot start a code gets a "feed_" prefix.
export function codeFromFilename(filename: string): string {
  const snake = stem(filename).toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
  if (!snake) return "feed";
  return /^[a-z]/.test(snake) ? snake : `feed_${snake}`;
}

// "security_master-2019.csv" -> "security master 2019": underscores and hyphens become spaces, runs collapse, case is kept.
export function feedNameFromFilename(filename: string): string {
  const name = stem(filename).replace(/[_\-\s]+/g, " ").trim();
  return name || stem(filename);
}

const PREVIEW_ROWS = 10;
const PLACEHOLDERS =["all", "na", "n/a", "-"];

// What a preview cell is, if it is worth a warning: a placeholder value, or nothing.
function cellKind(cell: string | null): "Placeholder" | "Empty" | null {
  const value = (cell ?? "").trim();
  if (value === "") return "Empty";
  return PLACEHOLDERS.includes(value.toLowerCase()) ? "Placeholder" : null;
}

function summaryLine(r: InspectedUpload): string {
  const parts: string[] = [r.format];
  if (r.format === "csv") {
    if (r.encoding) parts.push(r.encoding);
    if (r.delimiter) parts.push(`delimiter ${r.delimiter === "\t" ? "tab" : `"${r.delimiter}"`}`);
  }
  if (r.format === "xlsx" && r.sheet) parts.push(`sheet ${r.sheet}`);
  parts.push(`${r.row_count} rows`, `${r.columns.length} columns`);
  const skipped = Object.entries(r.skipped).filter(([, n]) => n > 0).map(([reason, n]) => `${reason}: ${n}`);
  if (skipped.length > 0) parts.push(`skipped ${skipped.join(", ")}`);
  return parts.join(" | ");
}

// The first step of onboarding (phase 7.8, task 2.5): a file in, how it was read, then the feed it becomes.
export default function UploadView({ me, onUploaded }: { me: User | null; onUploaded: (feedId: string, onboardingId?: string) => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<InspectedUpload | null>(null);
  const [refusal, setRefusal] = useState<UploadRefusal | null>(null);
  const [needsImage, setNeedsImage] = useState<NeedsImageExtraction | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [headerRow, setHeaderRow] = useState("1");
  const [feedCode, setFeedCode] = useState("");
  const [feedName, setFeedName] = useState("");
  const [codeTaken, setCodeTaken] = useState(false);
  const [conditions, setConditions] = useState(DEFAULT_CONDITIONS);
  const [description, setDescription] = useState("");
  const [schedule, setSchedule] = useState<ScheduleValue>(EMPTY_SCHEDULE); // phase 7.21: unanswered until a person chooses
  const latest = useRef(0); // only the newest read may change the screen

  if (!me || !me.roles.includes("backend_team")) {
    return (
      <section>
        <Title order={2} mb="md">Upload a file</Title>
        <div className="empty" data-testid="upload-role">Uploading needs the backend team role.</div>
      </section>
    );
  }
  const devUser = me.id;

  const show = (answer: UploadAnswer, first: boolean) => {
    setRefusal(null);
    setNeedsImage(null);
    setFailure(null);
    if (answer.outcome === "inspected") {
      setResult(answer);
      setHeaderRow(String(answer.header_row));
      if (first) {
        setFeedCode(codeFromFilename(answer.filename));
        setFeedName(feedNameFromFilename(answer.filename));
        setCodeTaken(false);
      }
    } else if (answer.outcome === "needs_image_extraction") {
      setResult(null);
      setNeedsImage(answer);
    } else {
      if (first) setResult(null);
      setRefusal(answer);
    }
  };

  const run = async (call: () => Promise<UploadAnswer>, first: boolean) => {
    const mine = ++latest.current;
    setBusy(true);
    try {
      const answer = await call();
      if (mine === latest.current) show(answer, first);
    } catch (err: unknown) {
      if (mine === latest.current) setFailure(describeError(err));
    } finally {
      if (mine === latest.current) setBusy(false);
    }
  };

  const send = () => file && run(() => uploadFile(file, devUser), true);

  const again = (sheet: string | null, row: string) => {
    if (!result) return;
    const n = Number(row);
    if (!Number.isInteger(n) || n < 1) return;
    void run(() => reinspect(result.upload_id, result.filename, devUser, { sheet, header_row: n }), false);
  };

  const codeValid = CODE_PATTERN.test(feedCode) && feedCode.length <= 60;
  const nameValid = feedName.trim().length > 0;
  const headerValid = Number.isInteger(Number(headerRow)) && Number(headerRow) >= 1;
  const canConfirm = !!result && codeValid && nameValid && headerValid && scheduleValid(schedule) && !busy;

  const confirm = async () => {
    if (!result) return;
    setBusy(true);
    setFailure(null);
    setCodeTaken(false);
    try {
      const answer = await confirmUpload(result.upload_id, devUser, {
        filename: result.filename, feed_code: feedCode, feed_name: feedName.trim(),
        sheet: result.sheet, header_row: Number(headerRow), ...conditions, description, schedule: scheduleBody(schedule),
      });
      if (answer.outcome === "uploaded") {
        if (answer.onboarding_id) onUploaded(answer.feed_id, answer.onboarding_id);
        else onUploaded(answer.feed_id);
      }
      else setRefusal(answer);
    } catch (err: unknown) {
      if (err instanceof ApiError && err.status === 409) setCodeTaken(true);
      else setFailure(describeError(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section>
      <Title order={2} mb="md">Upload a file</Title>
      <div className="toolbar">
        <div>
          <label htmlFor="upload-file">File to onboard</label>
          <input id="upload-file" data-testid="upload-file" type="file" accept=".csv,.tsv,.txt,.xlsx,.pdf"
                 aria-describedby="upload-help" onChange={(e) => setFile(e.currentTarget.files?.[0] ?? null)} />
          <p className="muted" id="upload-help" data-testid="upload-help">
            Up to 50 MB. Excel is the slowest format: 5,000 rows × 10 columns read in 0.9 s, 20,000 rows in about 10 s
            (measured on the development laptop). For larger tables, upload a CSV (100,000 rows read and profiled in about 3 s).
          </p>
        </div>
        <Button data-testid="upload-send" disabled={!file || busy} onClick={send}>Upload</Button>
      </div>

      {!file && !result && !busy && <div className="empty" data-testid="upload-empty">Choose a CSV, TSV, XLSX or PDF file.</div>}
      {busy && (
        <LoadingSkeleton label="Reading the file…" />
      )}

      {refusal && (
        <Alert color="red" variant="light" title="This file cannot be used" data-testid="upload-error" role="alert" mb="md">
          {refusal.detail}
        </Alert>
      )}
      {failure && (
        <Alert color="red" variant="light" title="Something went wrong" data-testid="upload-failure" role="alert" mb="md">
          {failure}
        </Alert>
      )}
      {needsImage && (
        <Alert color="blue" variant="light" title="Scanned PDF" data-testid="upload-info" mb="md">
          This PDF is scanned: the model will read its pages (next step)
        </Alert>
      )}

      {result && (
        <>
          <Text data-testid="upload-summary" mb="sm">{summaryLine(result)}</Text>
          <div className="toolbar">
            {result.sheets.length > 1 && (
              <NativeSelect label="Sheet" data-testid="upload-sheet" value={result.sheet ?? ""} data={result.sheets}
                            onChange={(e) => again(e.currentTarget.value, headerRow)} />
            )}
            {result.format !== "pdf" && (
              <TextInput label="Header row" data-testid="upload-header-row" type="number" min={1} w={140}
                         value={headerRow}
                         error={headerValid ? undefined : "A whole number from 1"}
                         onChange={(e) => {
                           setHeaderRow(e.currentTarget.value);
                           again(result.sheet, e.currentTarget.value);
                         }} />
            )}
          </div>

          <div data-testid="upload-preview-scroll" style={{ overflowX: "auto", maxWidth: "100%" }}>
            <Table data-testid="upload-preview" verticalSpacing="xs" style={{ width: "max-content", minWidth: "100%" }}>
              <Table.Thead>
                <Table.Tr>
                  {result.columns.map((c, i) => <Table.Th key={`${i}-${c}`} style={{ whiteSpace: "nowrap" }}>{c}</Table.Th>)}
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {result.preview.slice(0, PREVIEW_ROWS).map((row, r) => (
                  <Table.Tr key={r}>
                    {row.map((cell, c) => {
                      const kind = cellKind(cell);
                      return (
                        <Table.Td key={c} title={kind ?? undefined}
                                  style={kind ? { background: "var(--amz-warning)", color: "var(--amz-on-bright)" } : undefined}>
                          {(cell ?? "") === "" ? <em>empty</em> : cell}
                        </Table.Td>
                      );
                    })}
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </div>
          <p className="muted" data-testid="upload-legend" style={{ marginTop: 6 }}>
            <span aria-hidden="true" style={{ display: "inline-block", width: 12, height: 12, background: "var(--amz-warning)",
                                              verticalAlign: "middle", marginRight: 6 }} />
            Highlighted cells hold a placeholder (ALL, NA, N/A, -) or are empty.
          </p>

          <Title order={3} mb="xs">The feed</Title>
          <div className="toolbar">
            <TextInput label="Feed code" data-testid="upload-feed-code" value={feedCode} w={260}
                       error={!codeValid ? CODE_HELP : codeTaken ? "The feed code is taken" : undefined}
                       onChange={(e) => {
                         setFeedCode(e.currentTarget.value);
                         setCodeTaken(false);
                       }} />
            <TextInput label="Feed name" data-testid="upload-feed-name" value={feedName} w={320}
                       error={nameValid ? undefined : "A name is needed"}
                       onChange={(e) => setFeedName(e.currentTarget.value)} />
          </div>
          <ScheduleForm value={schedule} onChange={setSchedule} idPrefix="upload-schedule" />
          <details className="conditions" style={{ margin: "var(--mantine-spacing-sm) 0" }}>
            <summary>Conditions of use</summary>
            <div className="toolbar">
              <TextInput label="Licence" value={conditions.licence}
                         onChange={(e) => setConditions({ ...conditions, licence: e.currentTarget.value })} />
              <TextInput label="Permitted use" value={conditions.permitted_use}
                         onChange={(e) => setConditions({ ...conditions, permitted_use: e.currentTarget.value })} />
              <TextInput label="Redistribution" value={conditions.redistribution}
                         onChange={(e) => setConditions({ ...conditions, redistribution: e.currentTarget.value })} />
            </div>
            <TextInput label="Description" value={description} maxLength={500}
                       onChange={(e) => setDescription(e.currentTarget.value)} />
          </details>
          <Button data-testid="upload-confirm" disabled={!canConfirm} onClick={confirm}>Create the feed</Button>
        </>
      )}
    </section>
  );
}
