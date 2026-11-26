import type { ReactNode } from "react";
import type { ManualItemBody, OnboardingItem, Workspace } from "../../api";

// What the workspace view hands to every step panel: the item decisions and the edit in progress.
export type ItemActions = {
  readOnly: boolean; // a done or stale step viewed from the stepper: no decision buttons, no Edit
  busy: boolean;
  marked: string[]; // proposal ids a 409 decision_needed named
  editing: string | null; // the proposal id being edited
  editError: string | null; // the API's message for the last refused edit
  people?: Record<string, string>; // user id -> display name, for "approved by"
  decide: (item: OnboardingItem, decision: "approved" | "rejected", choice?: number | string, note?: string) => void;
  startEdit: (item: OnboardingItem) => void;
  saveEdit: (item: OnboardingItem, payload: Record<string, unknown>) => void;
  cancelEdit: () => void;
  // T7: add an item by hand at this stage; true when it was added (the form closes), false when the API refused it
  add?: (body: ManualItemBody) => Promise<boolean>;
  addError?: string | null; // the API's message for the last refused add
  // phase 7.22: a choice for columns no glossary element fits (the mapping step)
  cannotMap?: (action: "ignore", columns: string[]) => void;
};

export type PanelProps = { ws: Workspace; items: OnboardingItem[]; actions: ItemActions };

export function asRecord(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

export function asText(value: unknown): string {
  return typeof value === "string" ? value : "";
}

export function asList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : [];
}

// A dl of label / value pairs; a pair with no value is left out.
export function Facts({ rows }: { rows: [string, ReactNode][] }) {
  const shown = rows.filter(([, value]) => value !== null && value !== undefined && value !== "");
  return (
    <dl style={{ margin: "4px 0", display: "grid", gridTemplateColumns: "max-content 1fr", gap: "2px 12px" }}>
      {shown.map(([label, value]) => (
        <div key={label} style={{ display: "contents" }}>
          <dt style={{ color: "var(--amz-muted)" }}>{label}</dt>
          <dd style={{ margin: 0 }}>{value}</dd>
        </div>
      ))}
    </dl>
  );
}
