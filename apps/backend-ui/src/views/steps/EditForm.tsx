import { Alert, Button, NativeSelect, Textarea, TextInput } from "@mantine/core";
import { useRef, useState } from "react";
import type { OnboardingItem } from "../../api";
import { asList, asRecord, asText } from "./shared";

const SNAKE = /^[a-z][a-z0-9_]*$/;
const SNAKE_HELP = "Use lower-case letters, digits and underscores, starting with a letter.";
const DATA_TYPES = ["string", "integer", "decimal", "date", "datetime", "boolean"];
// The fixed safe set of transformation operations and the lists their arguments come from (core/domain/transformation.py).
const OPS = ["trim", "placeholder_to_null", "parse_date", "cast", "rename"] as const;
type Op = (typeof OPS)[number];
const DATE_FORMATS = ["DD-MM-YYYY", "MM-DD-YYYY", "DD/MM/YYYY", "MM/DD/YYYY", "YYYY-MM-DD", "YYYYMMDD", "DD.MM.YYYY"];
const CAST_TYPES = ["string", "integer", "decimal", "date", "datetime", "boolean"];
const MAX_STEPS = 6;

// The four kinds a person may edit (core/steps/onboarding.py _checked_payload). A glossary conflict has no
// recommended option to edit: the owner chooses one of its options instead.
export function isEditable(item: OnboardingItem): boolean {
  if (item.kind === "glossary_element") return item.payload.recommended !== null && item.payload.recommended !== undefined;
  return ["mapping", "transformation", "rule"].includes(item.kind);
}

type FormProps = { payload: Record<string, unknown>; onSave: (payload: Record<string, unknown>) => void };

function GlossaryFields({ payload, onSave }: FormProps) {
  const options = (Array.isArray(payload.options) ? payload.options : []).map(asRecord);
  const at = typeof payload.recommended === "number" ? payload.recommended : 0;
  const option = options[at] ?? {};
  const element = asRecord(option.element);
  const isAlias = option.kind === "alias";
  const [name, setName] = useState(asText(element.name));
  const [definition, setDefinition] = useState(asText(element.definition));
  const [dataType, setDataType] = useState(asText(element.data_type) || "string");
  const [unit, setUnit] = useState(asText(element.unit));
  const [alias, setAlias] = useState(asText(option.alias));
  const [tried, setTried] = useState(false);

  const problems = {
    name: !isAlias && name.trim() === "" ? "A name is needed" : undefined,
    definition: !isAlias && definition.trim() === "" ? "A definition is needed" : undefined,
    alias: isAlias && !SNAKE.test(alias) ? SNAKE_HELP : undefined,
  };
  const valid = !problems.name && !problems.definition && !problems.alias;
  const save = () => {
    setTried(true);
    if (!valid) return;
    const changed = isAlias ? { ...option, alias }
      : { ...option, element: { ...element, name: name.trim(), definition: definition.trim(), data_type: dataType,
                                 unit: unit.trim() === "" ? null : unit.trim() } };
    onSave({ ...payload, options: options.map((o, i) => (i === at ? changed : o)) });
  };
  return (
    <>
      {isAlias ? (
        <TextInput label="Alias" value={alias} error={tried ? problems.alias : undefined}
                   onChange={(e) => setAlias(e.currentTarget.value)} />
      ) : (
        <>
          <TextInput label="Name" value={name} error={tried ? problems.name : undefined} onChange={(e) => setName(e.currentTarget.value)} />
          <Textarea label="Definition" rows={2} value={definition} error={tried ? problems.definition : undefined}
                    onChange={(e) => setDefinition(e.currentTarget.value)} />
          <NativeSelect label="Type" value={dataType} data={DATA_TYPES} w={200} onChange={(e) => setDataType(e.currentTarget.value)} />
          <TextInput label="Unit" value={unit} w={200} onChange={(e) => setUnit(e.currentTarget.value)} />
        </>
      )}
      <SaveBar onSave={save} />
    </>
  );
}

function MappingFields({ payload, onSave }: FormProps) {
  const candidates = asList(payload.candidates);
  const start = asText(payload.recommended) || (candidates.length === 1 ? candidates[0] : "");
  const [element, setElement] = useState(start);
  const [tried, setTried] = useState(false);
  const save = () => {
    setTried(true);
    if (element === "") return;
    onSave({ ...payload, candidates: [element], recommended: element });
  };
  return (
    <>
      <NativeSelect label="Element" value={element} w={300} error={tried && element === "" ? "Choose an element" : undefined}
                    data={[{ value: "", label: "Select an element" }, ...candidates.map((c) => ({ value: c, label: c }))]}
                    onChange={(e) => setElement(e.currentTarget.value)} />
      <SaveBar onSave={save} />
    </>
  );
}

type StepDraft = { key: number; op: Op; values: string; format: string; type: string; to: string };

function draftOf(raw: Record<string, unknown>, key: number): StepDraft {
  const op = (OPS as readonly string[]).includes(asText(raw.op)) ? (asText(raw.op) as Op) : "trim";
  return { key, op, values: asList(raw.values).join(", "), format: asText(raw.format) || DATE_FORMATS[0],
           type: asText(raw.type) || "string", to: asText(raw.to) };
}

// A step as the API wants it, or the message that says why not.
function stepOf(d: StepDraft): { step?: Record<string, unknown>; problem?: string } {
  if (d.op === "trim") return { step: { op: "trim" } };
  if (d.op === "parse_date") {
    return DATE_FORMATS.includes(d.format) ? { step: { op: "parse_date", format: d.format } } : { problem: "Pick a date format from the list" };
  }
  if (d.op === "cast") {
    return CAST_TYPES.includes(d.type) ? { step: { op: "cast", type: d.type } } : { problem: "Pick a type from the list" };
  }
  if (d.op === "rename") {
    return SNAKE.test(d.to) && d.to.length <= 63 ? { step: { op: "rename", to: d.to } } : { problem: `New name: ${SNAKE_HELP}` };
  }
  const values = d.values.split(",").map((v) => v.trim()).filter((v) => v !== "");
  if (values.length < 1 || values.length > 10 || values.some((v) => v.length > 16)) {
    return { problem: "Give 1 to 10 placeholder values, each up to 16 characters" };
  }
  return { step: { op: "placeholder_to_null", values } };
}

// Also the step list of "Add item" at the transformations stage (AddItem.tsx): the same steps, the same checks.
export function TransformationFields({ payload, onSave, saveLabel = "Save edit" }: FormProps & { saveLabel?: string }) {
  const counter = useRef(0);
  const next = () => ++counter.current;
  const [steps, setSteps] = useState<StepDraft[]>(() =>
    (Array.isArray(payload.steps) ? payload.steps : []).map((s) => draftOf(asRecord(s), next())));
  const [problems, setProblems] = useState<string[]>([]);

  const change = (key: number, patch: Partial<StepDraft>) => setSteps(steps.map((s) => (s.key === key ? { ...s, ...patch } : s)));
  const move = (at: number, by: number) => {
    const copy = [...steps];
    [copy[at], copy[at + by]] = [copy[at + by], copy[at]];
    setSteps(copy);
  };
  const add = () => {
    const unused = OPS.find((op) => !steps.some((s) => s.op === op)) ?? "cast";
    setSteps([...steps, draftOf({ op: unused }, next())]);
  };

  const save = () => {
    const found: string[] = [];
    const built: Record<string, unknown>[] = [];
    if (steps.length === 0) found.push("Add at least one step");
    steps.forEach((s, i) => {
      const { step, problem } = stepOf(s);
      if (problem) found.push(`Step ${i + 1}: ${problem}`);
      else if (step) built.push(step);
    });
    const ops = steps.map((s) => s.op);
    if (new Set(ops).size !== ops.length) found.push("Each step may be used once");
    if (ops.includes("rename") && ops[ops.length - 1] !== "rename") found.push("Rename must be the last step");
    setProblems(found);
    if (found.length === 0) onSave({ ...payload, steps: built });
  };

  return (
    <>
      <ol style={{ padding: 0, margin: 0 }}>
        {steps.map((s, i) => {
          const n = i + 1;
          return (
            <li key={s.key} style={{ listStyle: "none", display: "flex", gap: 8, alignItems: "flex-end", flexWrap: "wrap", margin: "6px 0" }}>
              <NativeSelect label={`Step ${n} operation`} value={s.op} w={200} data={OPS.map((op) => ({ value: op, label: op.replaceAll("_", " ") }))}
                            onChange={(e) => change(s.key, { op: e.currentTarget.value as Op })} />
              {s.op === "placeholder_to_null" && (
                <TextInput label={`Step ${n} placeholder values`} value={s.values} w={240}
                           onChange={(e) => change(s.key, { values: e.currentTarget.value })} />
              )}
              {s.op === "parse_date" && (
                <NativeSelect label={`Step ${n} format`} value={s.format} w={180} data={DATE_FORMATS}
                              onChange={(e) => change(s.key, { format: e.currentTarget.value })} />
              )}
              {s.op === "cast" && (
                <NativeSelect label={`Step ${n} type`} value={s.type} w={160} data={CAST_TYPES}
                              onChange={(e) => change(s.key, { type: e.currentTarget.value })} />
              )}
              {s.op === "rename" && (
                <TextInput label={`Step ${n} new name`} value={s.to} w={220} onChange={(e) => change(s.key, { to: e.currentTarget.value })} />
              )}
              <Button size="xs" variant="default" disabled={i === 0} onClick={() => move(i, -1)}>{`Move step ${n} up`}</Button>
              <Button size="xs" variant="default" disabled={i === steps.length - 1} onClick={() => move(i, 1)}>{`Move step ${n} down`}</Button>
              <Button size="xs" variant="default" onClick={() => setSteps(steps.filter((x) => x.key !== s.key))}>{`Remove step ${n}`}</Button>
            </li>
          );
        })}
      </ol>
      <Button size="xs" variant="default" mt="xs" disabled={steps.length >= MAX_STEPS} onClick={add}>Add step</Button>
      {problems.length > 0 && (
        <ul style={{ color: "var(--amz-danger-text)", margin: "8px 0 0", paddingLeft: 18 }}>
          {problems.map((p) => <li key={p}>{p}</li>)}
        </ul>
      )}
      <SaveBar onSave={save} label={saveLabel} />
    </>
  );
}

function RuleFields({ payload, onSave }: FormProps) {
  const [description, setDescription] = useState(asText(payload.description));
  const [sql, setSql] = useState(asText(payload.sql));
  const [tried, setTried] = useState(false);
  const save = () => {
    setTried(true);
    if (description.trim() === "" || sql.trim() === "") return;
    onSave({ ...payload, description: description.trim(), sql: sql.trim() });
  };
  return (
    <>
      <TextInput label="Description" value={description} error={tried && description.trim() === "" ? "A description is needed" : undefined}
                 onChange={(e) => setDescription(e.currentTarget.value)} />
      <Textarea label="SQL" rows={5} value={sql} error={tried && sql.trim() === "" ? "SQL is needed" : undefined}
                styles={{ input: { fontFamily: "monospace" } }} onChange={(e) => setSql(e.currentTarget.value)} />
      <SaveBar onSave={save} />
    </>
  );
}

function SaveBar({ onSave, label = "Save edit" }: { onSave: () => void; label?: string }) {
  return <Button size="xs" mt="xs" mr="xs" onClick={onSave}>{label}</Button>;
}

// The kind-specific edit form of one item (task 7.3b). Every control has a label; the checks run before anything is sent;
// the API's own message (409, 422) is shown in `error`.
export default function EditForm({ item, error, onSave, onCancel }: {
  item: OnboardingItem; error: string | null; onSave: (payload: Record<string, unknown>) => void; onCancel: () => void;
}) {
  const props = { payload: item.payload, onSave };
  return (
    <div style={{ marginTop: 8 }} role="group" aria-label={`Edit ${item.column ?? item.kind}`}>
      {error && <Alert role="alert" color="red" mb="xs">{error}</Alert>}
      {item.kind === "glossary_element" && <GlossaryFields {...props} />}
      {item.kind === "mapping" && <MappingFields {...props} />}
      {item.kind === "transformation" && <TransformationFields {...props} />}
      {item.kind === "rule" && <RuleFields {...props} />}
      <Button size="xs" mt="xs" variant="default" onClick={onCancel}>Cancel</Button>
    </div>
  );
}
