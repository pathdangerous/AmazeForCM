import { useMemo, useState, type HTMLAttributes, type ReactNode } from "react";

export type DataGridColumn<T> = {
  key: string;
  label: string;
  value: (row: T) => string | number | null | undefined;
  render?: (row: T) => ReactNode;
  numeric?: boolean;
  sortable?: boolean;
  filterable?: boolean;
};
type RowAttributes = HTMLAttributes<HTMLTableRowElement> & { "data-testid"?: string };
type Props<T> = {
  rows: T[];
  columns: DataGridColumn<T>[];
  rowKey: (row: T) => string | number;
  pageSize?: number;
  filters?: boolean;
  sortable?: boolean;
  showPagination?: boolean;
  rowProps?: (row: T) => RowAttributes;
  emptyMessage?: string;
  className?: string;
  "aria-label": string;
  "data-testid"?: string;
};

// Filtering and sorting always happen before slicing. Each grid owns its view state only;
// source data and every action remain with the caller.
export default function DataGrid<T>({
  rows, columns, rowKey, pageSize = 10, filters = true, sortable = true, showPagination = true,
  rowProps, emptyMessage = "No matching rows", ...attributes
}: Props<T>) {
  const [query, setQuery] = useState<Record<string, string>>({});
  const [sort, setSort] = useState<{ key: string; descending: boolean } | null>(null);
  const [page, setPage] = useState(1);
  const size = Math.max(1, pageSize);
  const result = useMemo(() => {
    const filtered = rows.filter((row) => columns.every((column) => {
      const needle = query[column.key]?.trim().toLowerCase();
      return !needle || String(column.value(row) ?? "").toLowerCase().includes(needle);
    }));
    const column = columns.find((c) => c.key === sort?.key);
    if (column && sort) filtered.sort((a, b) => {
      const x = column.value(a);
      const y = column.value(b);
      // Missing measurements are unknown, never numeric zero, and sort last.
      if (x == null && y == null) return 0;
      if (x == null) return 1;
      if (y == null) return -1;
      const comparison = typeof x === "number" && typeof y === "number" ? x - y
        : String(x).localeCompare(String(y), undefined, { numeric: true, sensitivity: "base" });
      return sort.descending ? -comparison : comparison;
    });
    return filtered;
  }, [rows, columns, query, sort]);
  const pages = Math.max(1, Math.ceil(result.length / size));
  const current = Math.min(page, pages);
  const start = (current - 1) * size;
  const visible = result.slice(start, start + size);

  return <div className="amz-grid-container">
    <div className="amz-grid-wrap" tabIndex={0} role="region" aria-label={`${attributes["aria-label"]} scroll area`}>
      <table className="amz-grid" {...attributes}>
        <thead>
          <tr>{columns.map((column) => <th key={column.key} scope="col"
            aria-sort={sort?.key === column.key ? sort.descending ? "descending" : "ascending" : undefined}
            style={column.numeric ? { textAlign: "right" } : undefined}>
            {sortable && column.sortable !== false ? <button type="button" onClick={() => {
              setSort({ key: column.key, descending: sort?.key === column.key ? !sort.descending : false });
              setPage(1);
            }}>
              {column.label}<span className="amz-sort-mark" aria-hidden="true">{sort?.key === column.key ? sort.descending ? "↓" : "↑" : "↕"}</span>
            </button> : column.label}
          </th>)}</tr>
          {filters && <tr className="amz-grid-filters">{columns.map((column) => <td key={column.key}>
            {column.filterable !== false && <input aria-label={`Filter ${column.label.toLowerCase()}`} placeholder="Filter"
              value={query[column.key] ?? ""} onChange={(event) => {
                setQuery({ ...query, [column.key]: event.currentTarget.value });
                setPage(1);
              }} />}
          </td>)}</tr>}
        </thead>
        <tbody>{visible.length ? visible.map((row) => <tr key={rowKey(row)} {...rowProps?.(row)}>
          {columns.map((column) => <td key={column.key} className={column.numeric ? "numeric" : undefined}>
            {column.render ? column.render(row) : column.value(row) ?? "—"}
          </td>)}
        </tr>) : <tr><td className="amz-grid-empty" colSpan={columns.length}>{emptyMessage}</td></tr>}</tbody>
      </table>
    </div>
    {showPagination && <div className="amz-grid-pager">
      <span aria-live="polite"><span>{result.length ? start + 1 : 0} to {Math.min(start + size, result.length)} of {result.length}</span>
        {" · "}<span>Page {current} of {pages}</span></span>
      <div className="amz-pager-controls">
        <button type="button" aria-label="Previous page" disabled={current === 1} onClick={() => setPage(current - 1)}>Previous</button>
        <button type="button" aria-label="Next page" disabled={current >= pages} onClick={() => setPage(current + 1)}>Next</button>
      </div>
    </div>}
  </div>;
}
