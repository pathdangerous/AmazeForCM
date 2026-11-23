import { Alert, Button, Checkbox, NativeSelect, Textarea, TextInput } from "@mantine/core";
import { useState } from "react";
import type { ManualItemBody, StepName, Workspace } from "../../api";
import { TransformationFields } from "./EditForm";
import type { ItemActions } from "./shared";

const SNAKE = /^[a-z][a-z0-9_]*$/;
const SNAKE_HELP = "Use lower-case letters, digits and underscores, starting with a letter.";
const DATA_TYPES = ["string", "integer", "decimal", "date", "datetime", "boolean"];
// The stages where a person may add an item (T7). Profile and drift are not here: their actions do not exist yet.
export const ADDABLE: StepName[] = ["profile", "glossary", "mapping", "transformations", "rules", "go_live"];
const ACTIONS = [
  { value: "parse_date", label: "Read a column as a date" }, { value: "placeholder_to_null", label: "Turn placeholders into empty" },
  { value: "key_policy", label: "Decide what happens to a repeated key" }, { value: "exclude_column", label: "Leave a column out" },
  { value: "add_rule", label: "Add a rule" },
];
const LABELS: Partial<Record<StepName, string>> = {
  profile: "Add action",
  glossary: "Add glossary item", mapping: "Add mapping item", transformations: "Add transformation",
  rules: "Add rule", go_live: "Add note",
};

type FormProps = { ws: Workspace; submit: (body: ManualItemBody) => void };

function Column({ ws, value, onChange, error }: { ws: Workspace; value: string; onChange: (v: string) => void; error?: string }) {
  const columns = ws.inputs?.columns ?? [];
  return (
    <NativeSelect label="Column" value={value} w={300} error={error} onChange={(e) => onChange(e.currentTarget.value)}
                  data={[{ value: "", label: "Select a column" }, ...columns.map((c) => ({ value: c, label: c }))]} />
  );
}

// T1: an action a person adds at the profile step; they will do it themselves ("I'll do it").
function ActionForm({ ws, submit }: FormProps) {
  const [action, setAction] = useState("exclude_column");
  const [column, setColumn] = useState("");
  const [reason, setReason] = useState("");
  const [tried, setTried] = useState(false);
  const problems = { column: column === "" ? "Choose a column" : undefined, reason: reason.trim() === "" ? "A reason is needed" : undefined };
  return (
    <>
      <NativeSelect label="Action" value={action} w={340} data={ACTIONS} onChange={(e) => setAction(e.currentTarget.value)} />
      <Column ws={ws} value={column} onChange={setColumn} error={tried ? problems.column : undefined} />
      <Textarea label="Why" rows={2} value={reason} error={tried ? problems.reason : undefined}
                onChange={(e) => setReason(e.currentTarget.value)} />
      <Button size="xs" mt="xs" onClick={() => {
        setTried(true);
        if (!problems.column && !problems.reason) submit({ kind: "profile_action", action, columns: [column], reason: reason.trim() });
      }}>Add item</Button>
    </>
  );
}

function GlossaryForm({ ws, submit }: FormProps) {
  const [column, setColumn] = useState("");
  const [kind, setKind] = useState<"new_element" | "alias">("new_element");
  const [key, setKey] = useState("");
  const [name, setName] = useState("");
  const [domain, setDomain] = useState("");
  const [dataType, setDataType] = useState("string");
  const [definition, setDefinition] = useState("");
  const [unit, setUnit] = useState("");
  const [alias, setAlias] = useState("");
  const [reason, setReason] = useState("");
  const [tried, setTried] = useState(false);
  const isNew = kind === "new_element";
  const problems = {
    column: column === "" ? "Choose a column" : undefined,
    key: SNAKE.test(key) ? undefined : SNAKE_HELP,
    name: isNew && name.trim() === "" ? "A name is needed" : undefined,
    domain: isNew && !SNAKE.test(domain) ? SNAKE_HELP : undefined,
    definition: isNew && definition.trim() === "" ? "A definition is needed" : undefined,
    alias: !isNew && !SNAKE.test(alias) ? SNAKE_HELP : undefined,
    reason: reason.trim() === "" ? "A reason is needed" : undefined,
  };
  const save = () => {
    setTried(true);
    if (Object.values(problems).some(Boolean)) return;
    const option = isNew
      ? { kind: "new_element" as const, key,
          element: { key, name: name.trim(), domain, data_type: dataType, definition: definition.trim(), aliases: [],
                     unit: unit.trim() === "" ? null : unit.trim() } }
      : { kind: "alias" as const, key, alias };
    submit({ kind: "glossary_element", source_field: column, reason: reason.trim(), option });
  };
  const shown = <K extends keyof typeof problems>(k: K) => (tried ? problems[k] : undefined);
  return (
    <>
      <Column ws={ws} value={column} onChange={setColumn} error={shown("column")} />
      <NativeSelect label="What to add" value={kind} w={300} onChange={(e) => setKind(e.currentTarget.value as typeof kind)}
                    data={[{ value: "new_element", label: "A new element" }, { value: "alias", label: "An alias of an element" }]} />
      <TextInput label={isNew ? "Element key" : "Element it is an alias of"} value={key} error={shown("key")}
                 onChange={(e) => setKey(e.currentTarget.value)} />
      {isNew ? (
        <>
          <TextInput label="Name" value={name} error={shown("name")} onChange={(e) => setName(e.currentTarget.value)} />
          <TextInput label="Domain" value={domain} error={shown("domain")} onChange={(e) => setDomain(e.currentTarget.value)} />
          <NativeSelect label="Type" value={dataType} data={DATA_TYPES} w={200} onChange={(e) => setDataType(e.currentTarget.value)} />
          <Textarea label="Definition" rows={2} value={definition} error={shown("definition")}
                    onChange={(e) => setDefinition(e.currentTarget.value)} />
          <TextInput label="Unit" value={unit} w={200} onChange={(e) => setUnit(e.currentTarget.value)} />
        </>
      ) : (
        <TextInput label="Alias" value={alias} error={shown("alias")} onChange={(e) => setAlias(e.currentTarget.value)} />
      )}
      <Textarea label="Why" rows={2} value={reason} error={shown("reason")} onChange={(e) => setReason(e.currentTarget.value)} />
      <Button size="xs" mt="xs" onClick={save}>Add item</Button>
    </>
  );
}

function MappingForm({ ws, submit }: FormProps) {
  const [column, setColumn] = useState("");
  const [excluded, setExcluded] = useState(false);
  const [element, setElement] = useState("");
  const [reason, setReason] = useState("");
  const [tried, setTried] = useState(false);
  const problems = {
    column: column === "" ? "Choose a column" : undefined,
    element: !excluded && !SNAKE.test(element) ? "Name a glossary element" : undefined,
    reason: reason.trim() === "" ? "A reason is needed" : undefined,
  };
  const save = () => {
    setTried(true);
    if (Object.values(problems).some(Boolean)) return;
    submit(excluded ? { kind: "mapping", source_field: column, excluded: true, reason: reason.trim() }
                    : { kind: "mapping", source_field: column, element_key: element, reason: reason.trim() });
  };
  return (
    <>
      <Column ws={ws} value={column} onChange={setColumn} error={tried ? problems.column : undefined} />
      <Checkbox mt="xs" label="Leave this column out (it is not loaded)" checked={excluded}
                onChange={(e) => setExcluded(e.currentTarget.checked)} />
      {!excluded && (
        <TextInput label="Glossary element" description="Any element in the glossary, not only the ones the agent offered"
                   value={element} error={tried ? problems.element : undefined} onChange={(e) => setElement(e.currentTarget.value)} />
      )}
      <Textarea label="Why" rows={2} value={reason} error={tried ? problems.reason : undefined}
                onChange={(e) => setReason(e.currentTarget.value)} />
      <Button size="xs" mt="xs" onClick={save}>Add item</Button>
    </>
  );
}

function TransformationForm({ ws, submit }: FormProps) {
  const [column, setColumn] = useState("");
  const [reason, setReason] = useState("");
  const [tried, setTried] = useState(false);
  const missing = { column: column === "" ? "Choose a column" : undefined, reason: reason.trim() === "" ? "A reason is needed" : undefined };
  return (
    <>
      <Column ws={ws} value={column} onChange={setColumn} error={tried ? missing.column : undefined} />
      <Textarea label="Why" rows={2} value={reason} error={tried ? missing.reason : undefined}
                onChange={(e) => setReason(e.currentTarget.value)} />
      <TransformationFields payload={{ steps: [] }} saveLabel="Add item" onSave={(payload) => {
        setTried(true);
        if (missing.column || missing.reason) return;
        submit({ kind: "transformation", source_field: column, reason: reason.trim(),
                 steps: payload.steps as Record<string, unknown>[] });
      }} />
    </>
  );
}

function RuleForm({ ws, submit }: FormProps) {
  const templates = ws.inputs?.rule_templates ?? [];
  const [own, setOwn] = useState(templates.length === 0);
  const [template, setTemplate] = useState("");
  const [key, setKey] = useState("");
  const [description, setDescription] = useState("");
  const [sql, setSql] = useState("");
  const [elements, setElements] = useState("");
  const [tried, setTried] = useState(false);
  const list = elements.split(",").map((e) => e.trim()).filter((e) => e !== "");
  const problems = own
    ? { key: SNAKE.test(key) ? undefined : SNAKE_HELP, description: description.trim() === "" ? "A description is needed" : undefined,
        sql: sql.trim() === "" ? "SQL is needed" : undefined, elements: list.length === 0 ? "Name at least one element" : undefined,
        template: undefined }
    : { template: template === "" ? "Choose a template" : undefined, key: undefined, description: undefined, sql: undefined,
        elements: undefined };
  const save = () => {
    setTried(true);
    if (Object.values(problems).some(Boolean)) return;
    submit(own ? { kind: "rule", rule_key: key, description: description.trim(), sql: sql.trim(), element_keys: list }
               : { kind: "rule", template });
  };
  const shown = <K extends keyof typeof problems>(k: K) => (tried ? problems[k] : undefined);
  return (
    <>
      {templates.length > 0 && (
        <NativeSelect label="How" value={own ? "own" : "template"} w={300} onChange={(e) => setOwn(e.currentTarget.value === "own")}
                      data={[{ value: "template", label: "Pick a template" }, { value: "own", label: "Write the SQL myself" }]} />
      )}
      {!own && (
        <NativeSelect label="Template" value={template} error={shown("template")} onChange={(e) => setTemplate(e.currentTarget.value)}
                      data={[{ value: "", label: "Select a template" },
                             ...templates.map((t) => ({ value: t.rule_key, label: `${t.rule_key}: ${t.description}` }))]} />
      )}
      {own && (
        <>
          <TextInput label="Rule key" value={key} error={shown("key")} onChange={(e) => setKey(e.currentTarget.value)} />
          <TextInput label="Description" value={description} error={shown("description")}
                     onChange={(e) => setDescription(e.currentTarget.value)} />
          <Textarea label="SQL" rows={4} value={sql} error={shown("sql")} styles={{ input: { fontFamily: "monospace" } }}
                    description="One SELECT that returns the _row of each row to flag, from the table curated"
                    onChange={(e) => setSql(e.currentTarget.value)} />
          <TextInput label="Elements it checks" description="Comma-separated element keys the feed maps" value={elements}
                     error={shown("elements")} onChange={(e) => setElements(e.currentTarget.value)} />
        </>
      )}
      <Button size="xs" mt="xs" onClick={save}>Add item</Button>
    </>
  );
}

function NoteForm({ submit }: FormProps) {
  const [note, setNote] = useState("");
  const [tried, setTried] = useState(false);
  return (
    <>
      <Textarea label="Note" rows={3} value={note} maxLength={1000} error={tried && note.trim() === "" ? "Write the note" : undefined}
                onChange={(e) => setNote(e.currentTarget.value)} />
      <Button size="xs" mt="xs" onClick={() => { setTried(true); if (note.trim() !== "") submit({ kind: "review_summary", note: note.trim() }); }}>
        Add item
      </Button>
    </>
  );
}

// "Add item" at a stage (T7): the person adds what the agent did not propose. It becomes a manual proposal like any other:
// pending until approved, author recorded, no model call. The API's own refusal (409 pending, 422 invalid, a rule's
// back-test failure) is shown in the form; the form closes only when the item was added.
export default function AddItem({ step, ws, actions }: { step: StepName; ws: Workspace; actions: ItemActions }) {
  const [open, setOpen] = useState(false);
  if (!actions.add || actions.readOnly || !ADDABLE.includes(step)) return null;
  const label = LABELS[step] ?? "Add item";
  const submit = async (body: ManualItemBody) => {
    if (await actions.add!(body)) setOpen(false);
  };
  if (!open) {
    return <Button size="xs" mt="sm" variant="default" disabled={actions.busy} onClick={() => setOpen(true)}>{label}</Button>;
  }
  const props = { ws, submit: (body: ManualItemBody) => void submit(body) };
  return (
    <div role="group" aria-label={label} style={{ marginTop: 12, border: "1px solid var(--amz-line)", borderRadius: 6, padding: "8px 12px" }}>
      {actions.addError && <Alert role="alert" color="red" mb="xs">{actions.addError}</Alert>}
      {step === "profile" && <ActionForm {...props} />}
      {step === "glossary" && <GlossaryForm {...props} />}
      {step === "mapping" && <MappingForm {...props} />}
      {step === "transformations" && <TransformationForm {...props} />}
      {step === "rules" && <RuleForm {...props} />}
      {step === "go_live" && <NoteForm {...props} />}
      <Button size="xs" mt="xs" variant="default" onClick={() => setOpen(false)}>Cancel</Button>
    </div>
  );
}
