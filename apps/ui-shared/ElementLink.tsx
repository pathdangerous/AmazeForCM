import { useId, useState } from "react";
import { apiGet, describeError, type ElementDetail } from "../backend-ui/src/api";

// Phase 7.22, T4: wherever a mapping shows its glossary element, the element is a link. Hover or keyboard focus opens a
// card with the element's name, definition, data type and aliases; clicking (or Enter on the link) opens the element on
// the Glossary page (#/glossary/<key>). The definition is read once per element. A plain <a>, so Tab reaches it.

const cache = new Map<string, Promise<ElementDetail | string>>();

export function clearElementCache(): void {
  cache.clear();
}

function definitionOf(key: string, devUser: string): Promise<ElementDetail | string> {
  const id = `${devUser}/${key}`;
  let found = cache.get(id);
  if (!found) {
    found = apiGet<ElementDetail>(`/api/amaze/glossary/elements/${encodeURIComponent(key)}`, devUser)
      .catch((err: unknown) => describeError(err));
    cache.set(id, found);
  }
  return found;
}

export default function ElementLink({ elementKey, devUser, label }: { elementKey: string; devUser: string; label?: string }) {
  const [open, setOpen] = useState(false);
  const [detail, setDetail] = useState<ElementDetail | string | null>(null);
  const cardId = useId();

  const show = () => {
    setOpen(true);
    void definitionOf(elementKey, devUser).then(setDetail);
  };
  const hide = () => setOpen(false);
  const element = typeof detail === "object" && detail !== null ? detail.element : null;

  return (
    <span style={{ position: "relative", display: "inline-block" }}>
      <a href={`#/glossary/${encodeURIComponent(elementKey)}`} aria-describedby={open ? cardId : undefined}
         onMouseEnter={show} onMouseLeave={hide} onFocus={show} onBlur={hide}
         onKeyDown={(event) => { if (event.key === "Escape") hide(); }}
         style={{ color: "var(--amz-link)", textDecoration: "underline" }}>
        <code>{label ?? elementKey}</code>
      </a>
      {open && (
        <span role="tooltip" id={cardId}
              style={{ position: "absolute", zIndex: 30, top: "100%", left: 0, minWidth: 260, maxWidth: 360, padding: "8px 12px",
                       background: "var(--amz-panel)", border: "1px solid var(--amz-line)", borderRadius: 8, textAlign: "left",
                       display: "block", whiteSpace: "normal" }}>
          {element ? (
            <>
              <strong style={{ display: "block" }}>{element.name}</strong>
              <span style={{ display: "block" }}>{element.definition}</span>
              <span style={{ display: "block" }}>{`Type: ${element.data_type}`}</span>
              <span style={{ display: "block" }}>
                {element.aliases.length > 0 ? `Also called: ${element.aliases.join(", ")}` : "No other names"}
              </span>
            </>
          ) : (
            <span>{detail === null ? "Reading the definition" : `The definition could not be read: ${detail}`}</span>
          )}
        </span>
      )}
    </span>
  );
}
