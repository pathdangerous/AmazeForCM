import { Text } from "@mantine/core";
import { useEffect, useState } from "react";
import {
  describeError, getCodeDiff, getCodePreview, getCodeVersion, getCodeVersions,
  type CodeDiff, type CodePreview, type CodeVersionDetail, type CodeVersionSummary,
} from "../api";

const NOW = "now"; // the configuration as it stands: what would be approved next, never stored by this view

// Generated code (T3): the versions a go-live approval stored, each file as text, and the difference between two
// versions. Read only: generated code is never hand-edited; a person changes the configuration, and a new version
// is generated at the next approval. Diff lines are marked by a fill and a sign, never by colour alone.
export default function CodeView({ feedId, devUser }: { feedId: string; devUser: string }) {
  const [versions, setVersions] = useState<CodeVersionSummary[] | null>(null);
  const [preview, setPreview] = useState<CodePreview | null>(null);
  const [selected, setSelected] = useState<string>("");
  const [detail, setDetail] = useState<CodeVersionDetail | null>(null);
  const [path, setPath] = useState<string>("");
  const [compare, setCompare] = useState<string>("");
  const [diff, setDiff] = useState<CodeDiff | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    Promise.all([getCodeVersions(feedId, devUser), getCodePreview(feedId, devUser)])
      .then(([list, now]) => {
        if (!live) return;
        setVersions(list);
        setPreview(now);
        setSelected(list.length > 0 ? String(list[list.length - 1].version) : NOW);
      })
      .catch((err: unknown) => live && setError(describeError(err)));
    return () => { live = false; };
  }, [feedId, devUser]);

  useEffect(() => {
    if (!selected || selected === NOW) { setDetail(null); return; }
    let live = true;
    getCodeVersion(feedId, Number(selected), devUser).then((d) => live && setDetail(d))
      .catch((err: unknown) => live && setError(describeError(err)));
    return () => { live = false; };
  }, [feedId, devUser, selected]);

  useEffect(() => {
    if (!compare || selected === NOW || compare === selected) { setDiff(null); return; }
    const [a, b] = [Number(selected), compare === NOW ? null : Number(compare)];
    const [from, to] = b !== null && b < a ? [b, a] : [a, b];
    let live = true;
    getCodeDiff(feedId, from, to, devUser).then((d) => live && setDiff(d))
      .catch((err: unknown) => live && setError(describeError(err)));
    return () => { live = false; };
  }, [feedId, devUser, selected, compare]);

  const files = selected === NOW ? preview?.files ?? {} : detail?.files ?? {};
  const paths = Object.keys(files).sort();
  const shownPath = paths.includes(path) ? path : paths[0] ?? "";

  if (error) return <Text role="alert" className="bad">{error}</Text>;
  if (!versions || !preview) return <Text className="muted">Loading the code.</Text>;
  const options = [...versions.map((v) => ({ value: String(v.version), label: `Version ${v.version}` })),
                   { value: NOW, label: "The configuration now" }];
  const meta = versions.find((v) => String(v.version) === selected);

  return (
    <div data-testid="code-view">
      {versions.length === 0 && (
        <Text className="muted">No code version yet: the first is stored when the data owner approves go-live.</Text>
      )}
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "flex-end" }}>
        <label>Code version{" "}
          <select aria-label="Code version" value={selected} onChange={(e) => { setSelected(e.currentTarget.value); setCompare(""); }}>
            {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </label>
        <label>Compare with{" "}
          <select aria-label="Compare with" value={compare} disabled={selected === NOW} onChange={(e) => setCompare(e.currentTarget.value)}>
            <option value="">Nothing</option>
            {options.filter((o) => o.value !== selected).map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </label>
      </div>
      {meta && (
        <Text size="sm" mt="xs" data-testid="code-meta">
          {`Version ${meta.version}, approved by ${meta.approved_by}; fingerprint ${meta.sha256.slice(0, 12)}; test load ${String(meta.test_load_result.status ?? "")}, ${String(meta.test_load_result.rows ?? "")} rows.`}
        </Text>
      )}
      {selected === NOW && (
        <Text size="sm" mt="xs">
          {preview.differs_from_latest ? "This differs from the latest approved version." : "This is the same as the latest approved version."}
        </Text>
      )}
      {diff ? (
        <div data-testid="code-diff" style={{ marginTop: 8 }}>
          {diff.files.length === 0 && <Text>The two versions are the same.</Text>}
          {diff.files.map((f) => (
            <div key={f.path} style={{ marginBottom: 8 }}>
              <strong>{f.path}</strong> <span className="muted">({f.change})</span>
              <pre style={{ margin: "4px 0", overflowX: "auto", background: "var(--amz-subtle)", color: "var(--amz-text)", padding: 8, borderRadius: 4 }}>
                {f.diff.split("\n").map((line, i) => (
                  <div key={i} style={line.startsWith("+") && !line.startsWith("+++") ? { background: "var(--amz-info)", color: "var(--amz-on-bright)" }
                    : line.startsWith("-") && !line.startsWith("---") ? { background: "var(--amz-warning)", color: "var(--amz-on-bright)" } : undefined}>
                    {line || " "}
                  </div>
                ))}
              </pre>
            </div>
          ))}
        </div>
      ) : paths.length > 0 && (
        <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginTop: 8 }}>
          <ul aria-label="Files" style={{ listStyle: "none", padding: 0, margin: 0, minWidth: 180 }}>
            {paths.map((p) => (
              <li key={p}>
                <button type="button" aria-pressed={p === shownPath} onClick={() => setPath(p)}
                        style={{ fontWeight: p === shownPath ? 700 : 400, background: "none", border: 0, cursor: "pointer", padding: "2px 0", font: "inherit" }}>
                  {p}
                </button>
              </li>
            ))}
          </ul>
          <pre data-testid="code-file" aria-label={`File ${shownPath}`} style={{
            flex: "1 1 360px", margin: 0, overflowX: "auto", background: "var(--amz-subtle)", color: "var(--amz-text)", padding: 8, borderRadius: 4 }}>
            {files[shownPath]}
          </pre>
        </div>
      )}
    </div>
  );
}
