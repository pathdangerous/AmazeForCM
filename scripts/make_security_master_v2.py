"""Writes fixtures/sources/Synthetic_Security_Master_v2.xlsx (phase 7.12, T4): the second delivery of the synthetic
security master. It is version 1 (fixtures/synthetic/Synthetic_Security_Master.xlsx) with

- one new column, BEST_FCF (free cash flow estimate), at the end: step 12 reports it as a new column;
- two columns swapped, BEST_BPS and BEST_CAPEX: only the order changes (the engine maps by name), shown as information.

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


def main() -> None:
    source = openpyxl.load_workbook(V1)
    sheet = source[source.sheetnames[0]]
    rows = [[cell.value for cell in row] for row in sheet.iter_rows()]
    header = rows[0]
    a, b = header.index(SWAP[0]), header.index(SWAP[1])
    out = openpyxl.Workbook()
    target = out.active
    target.title = sheet.title
    for number, row in enumerate(rows):
        row = list(row)
        row[a], row[b] = row[b], row[a]
        row.append(NEW_COLUMN if number == 0 else round(1000 + 37.5 * number, 1))
        target.append(row)
    V2.parent.mkdir(parents=True, exist_ok=True)
    out.save(V2)
    print(f"wrote {V2.relative_to(ROOT)}: {len(rows) - 1} rows, {len(header) + 1} columns")


if __name__ == "__main__":
    main()
