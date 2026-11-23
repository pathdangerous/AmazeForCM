"""Writes fixtures/sources/Synthetic_Security_Master_v2.xlsx (phase 7.12, T4): the second delivery of the synthetic
security master. It is version 1 (fixtures/synthetic/Synthetic_Security_Master.xlsx) with

- one new column, BEST_FCF (free cash flow estimate), at the end: step 12 reports it as a new column;
- two columns swapped, BEST_BPS and BEST_CAPEX: only the order changes (the engine maps by name), shown as information;
- one column whose type changes, BEST_DPS: its numbers are sent as text with a trailing space ("2.5 "). DuckDB reads the
  column as VARCHAR instead of DOUBLE, so step 12 reports a type change (DOUBLE to VARCHAR); the load still casts it;
- a rule breach, BEST_SALES: eight rows are negative, so the rule "best sales is not negative" flags more rows than it did
  in the first delivery, and the rule-rate check after the run proposes a review of that rule.

Run once: python scripts/make_security_master_v2.py. The output is committed; this script says how it was made.
"""

from __future__ import annotations

from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parents[1]
V1 = ROOT / "fixtures" / "synthetic" / "Synthetic_Security_Master.xlsx"
V2 = ROOT / "fixtures" / "sources" / "Synthetic_Security_Master_v2.xlsx"
SWAP = ("BEST_BPS", "BEST_CAPEX")
NEW_COLUMN = "BEST_FCF"
TEXT_COLUMN = "BEST_DPS"  # numbers sent as text
BREACH_COLUMN = "BEST_SALES"
BREACH_ROWS = 8  # how many rows are made negative


def main() -> None:
    source = openpyxl.load_workbook(V1)
    sheet = source[source.sheetnames[0]]
    rows = [[cell.value for cell in row] for row in sheet.iter_rows()]
    header = rows[0]
    a, b = header.index(SWAP[0]), header.index(SWAP[1])
    out = openpyxl.Workbook()
    target = out.active
    target.title = sheet.title
    text, breach = header.index(TEXT_COLUMN), header.index(BREACH_COLUMN)
    for number, row in enumerate(rows):
        row = list(row)
        if number > 0:
            if isinstance(row[text], (int, float)):
                row[text] = f"{row[text]} "  # text, with a trailing space: read as VARCHAR, still castable
            if number <= BREACH_ROWS and isinstance(row[breach], (int, float)):
                row[breach] = -abs(row[breach])
        row[a], row[b] = row[b], row[a]
        row.append(NEW_COLUMN if number == 0 else round(1000 + 37.5 * number, 1))
        target.append(row)
    V2.parent.mkdir(parents=True, exist_ok=True)
    out.save(V2)
    print(f"wrote {V2.relative_to(ROOT)}: {len(rows) - 1} rows, {len(header) + 1} columns")


if __name__ == "__main__":
    main()
