import type { HTMLAttributes, ReactNode } from "react";

export function KpiCard({ label, value, detail }: { label: string; value: ReactNode; detail?: ReactNode }) {
  return <div className="amz-card amz-kpi"><span className="amz-kpi-label">{label}</span>
    <strong className="amz-kpi-value">{value}</strong>{detail != null && <span className="amz-kpi-detail">{detail}</span>}</div>;
}

export function SectionCard({ title, children, actions, tinted = false, bare = false, className = "" }: {
  title: string; children: ReactNode; actions?: ReactNode; tinted?: boolean; bare?: boolean; className?: string;
}) {
  return <section className={`amz-card ${className}`}>
    <div className={`amz-card-heading${tinted ? " amz-card-heading-tinted" : ""}`}><h2>{title}</h2>{actions}</div>
    {bare ? children : <div className="amz-card-body">{children}</div>}
  </section>;
}

export function StatusChip({ tone = "neutral", children }: { tone?: "good" | "attention" | "neutral"; children: ReactNode }) {
  return <span className={`amz-chip amz-chip-${tone}`}>{children}</span>;
}

export function ConfidenceBar({ value, label = "Confidence" }: { value: number | null; label?: string }) {
  if (value === null || !Number.isFinite(value)) return <span aria-label={label} className="muted">—</span>;
  const safe = Math.max(0, Math.min(1, value));
  return <div className="amz-confidence" role="meter" aria-label={label} aria-valuemin={0} aria-valuemax={100}
    aria-valuenow={Number((safe * 100).toFixed(2))}>
    <span className="amz-confidence-track" aria-hidden="true"><span style={{ width: `${safe * 100}%`, background: safe >= .85 ? "#2563EB" : "#EA580C" }} /></span>
    <span>{safe.toFixed(2)}</span>
  </div>;
}

export function CssDonut({ value, label, detail = "pass" }: { value: number | null; label: string; detail?: string }) {
  if (value === null || !Number.isFinite(value)) return <span aria-label={`${label}: not recorded`} className="muted">Not recorded</span>;
  const safe = Math.max(0, Math.min(100, value));
  return <div className="amz-donut" role="img" aria-label={`${label}: ${safe} percent`}
    style={{ background: `conic-gradient(#2563EB 0 ${safe}%, #EA580C ${safe}% 100%)` }}>
    <div className="amz-donut-hole"><span>{Number(safe.toFixed(1))}%</span>{detail && <small>{detail}</small>}</div>
  </div>;
}

export function StickyFooter({ children, className = "", ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div {...props} className={`amz-sticky-footer ${className}`}>{children}</div>;
}

export function LoadingSkeleton({ label, testId }: { label: string; testId?: string }) {
  return <div className="amz-skeleton" role="status" aria-label={label} data-testid={testId}>
    <span /><span /><span /><span /><span /><span />
    <span className="sr-only">{label}</span>
  </div>;
}
