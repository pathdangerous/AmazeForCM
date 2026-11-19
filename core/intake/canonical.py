"""The canonical CSV (UTF-8, no BOM, comma, minimal quoting, \\n, one header row) and the manifest JSON."""

from __future__ import annotations

import csv
import io
import json

from core.intake.model import Manifest, Table


def canonical_csv(table: Table) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, delimiter=",", quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
    writer.writerow(table.header)
    for row in table.rows:
        writer.writerow(["" if cell is None else cell for cell in row])
    return buffer.getvalue().encode("utf-8")


def manifest_json(manifest: Manifest) -> bytes:
    return json.dumps(manifest.model_dump(mode="json"), sort_keys=True, indent=2, ensure_ascii=False).encode("utf-8")
