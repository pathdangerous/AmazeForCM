"""Task 2.2: the text-PDF reader into the intake model. Every PDF is made inside the test (synthetic)."""

from __future__ import annotations

import io

import duckdb
import pytest
from PIL import Image, ImageDraw

from core.intake import (
    NeedsImageExtraction,
    NotATable,
    SkippedRow,
    canonical_csv,
    inspect_pdf,
    manifest_json,
    read_pdf_bytes,
    render_pages_png,
)


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def grid_page(rows: list[list[str]], *, col_w: int = 150, row_h: int = 30, x0: int = 50, y_top: int = 750) -> bytes:
    """Content stream of a ruled table: text objects plus a line grid."""
    ncols = len(rows[0])
    out = ["0.5 w", f"BT /F1 12 Tf {x0} {y_top + 20} Td (Synthetic listing, ruled table) Tj ET"]
    width = col_w * ncols
    for r in range(len(rows) + 1):
        y = y_top - r * row_h
        out.append(f"{x0} {y} m {x0 + width} {y} l S")
    for c in range(ncols + 1):
        x = x0 + c * col_w
        out.append(f"{x} {y_top} m {x} {y_top - len(rows) * row_h} l S")
    for r, row in enumerate(rows):
        for c, cell in enumerate(row):
            out.append(f"BT /F1 10 Tf {x0 + c * col_w + 5} {y_top - r * row_h - 19} Td ({_esc(cell)}) Tj ET")
    return "\n".join(out).encode("latin-1")


def prose_page(lines: list[str]) -> bytes:
    out = []
    for i, line in enumerate(lines):
        out.append(f"BT /F1 11 Tf 50 {750 - i * 16} Td ({_esc(line)}) Tj ET")
    return "\n".join(out).encode("latin-1")


IMAGE_ONLY = b"IMAGE"  # a marker stream: make_pdf draws one small image and no text on that page


def make_pdf(streams: list[bytes]) -> bytes:
    """A minimal PDF: one Helvetica font, one page per content stream, a correct xref table."""
    n = len(streams)
    image = 4 + 2 * n
    objs: dict[int, bytes] = {}
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(n))
    objs[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objs[2] = f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode()
    font = 3 + 2 * n
    for i, stream in enumerate(streams):
        page, content = 3 + 2 * i, 4 + 2 * i
        if stream == IMAGE_ONLY:
            stream = b"q 200 0 0 200 50 500 cm /Im1 Do Q"
        objs[page] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {content} 0 R "
            f"/Resources << /Font << /F1 {font} 0 R >> /XObject << /Im1 {image} 0 R >> >> >>"
        ).encode()
        objs[content] = f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream"
    objs[font] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    pixels = bytes([0, 255, 255, 0])
    objs[image] = (
        b"<< /Type /XObject /Subtype /Image /Width 2 /Height 2 /ColorSpace /DeviceGray "
        b"/BitsPerComponent 8 /Length 4 >>\nstream\n" + pixels + b"\nendstream"
    )
    buf = bytearray(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for num in sorted(objs):
        offsets[num] = len(buf)
        buf += f"{num} 0 obj\n".encode() + objs[num] + b"\nendobj\n"
    xref = len(buf)
    buf += f"xref\n0 {len(objs) + 1}\n".encode() + b"0000000000 65535 f \n"
    for num in sorted(objs):
        buf += f"{offsets[num]:010d} 00000 n \n".encode()
    buf += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(buf)


def image_pdf(pages: int = 1) -> bytes:
    imgs = []
    for p in range(pages):
        img = Image.new("RGB", (400, 300), "white")
        d = ImageDraw.Draw(img)
        for i in range(4):
            d.rectangle([20, 20 + i * 60, 380, 70 + i * 60], outline="black", width=2)
        imgs.append(img)
    buf = io.BytesIO()
    imgs[0].save(buf, "PDF", save_all=True, append_images=imgs[1:])
    return buf.getvalue()


HEADER = ["id", "name", "qty"]


def read(data: bytes, **kw):
    return read_pdf_bytes(data, original_name="t.pdf", **kw)


def two_page_pdf() -> bytes:
    p1 = grid_page([HEADER, ["1", "Ann", "5"], ["2", "Bob", "6"]])
    p2 = grid_page([HEADER, ["3", "Cy", "7"], ["4", "Di", "8"]])
    return make_pdf([p1, p2])


def test_two_page_table_header_repeated_on_page_two():
    data = two_page_pdf()
    info = inspect_pdf(data)
    assert info.pages == 2 and info.text_pages == [1, 2] and info.image_pages == [] and info.kind == "text"
    table, m = read(data)
    assert table.header == HEADER
    assert table.rows == [["1", "Ann", "5"], ["2", "Bob", "6"], ["3", "Cy", "7"], ["4", "Di", "8"]]
    assert m.pages == [1, 2] and m.ignored_pages == []
    assert m.format == "pdf" and m.extraction == "text_pdf" and m.reader.startswith("pdfplumber ")
    assert m.row_count == 4 and m.header_row == 1
    assert any(s.reason == "repeated_header" for s in m.skipped)
    assert all(c.cell_types.get("text") for c in m.columns)


def test_header_row_chosen_inside_first_table():
    p1 = grid_page([["Synthetic title", "", ""], HEADER, ["1", "Ann", "5"]])
    table, m = read(make_pdf([p1]), header_row=2)
    assert table.header == HEADER and table.rows == [["1", "Ann", "5"]] and m.header_row == 2
    assert SkippedRow(row=1, reason="before_header") in table.skipped


def test_empty_cell_stays_none():
    p1 = grid_page([HEADER, ["1", "Ann", ""], ["2", "Bob", "6"]])
    table, _ = read(make_pdf([p1]))
    assert table.rows[0] == ["1", "Ann", None]
    assert table.columns[2].cell_types.get("empty") == 1


def test_page_with_other_width_is_ignored():
    p1 = grid_page([HEADER, ["1", "Ann", "5"]])
    p2 = grid_page([["a", "b"], ["c", "d"]])
    table, m = read(make_pdf([p1, p2]))
    assert table.rows == [["1", "Ann", "5"]]
    assert m.pages == [1] and m.ignored_pages == [2]


def test_prose_page_is_not_a_table():
    data = make_pdf([prose_page(["This synthetic page holds only running prose.", "It has no ruled table at all."])])
    assert inspect_pdf(data).kind == "text"
    with pytest.raises(NotATable) as e:
        read(data)
    assert "no table found" in e.value.detail


def test_image_only_pdf():
    data = image_pdf()
    info = inspect_pdf(data)
    assert info.kind == "image" and info.image_pages == [1] and info.text_pages == []
    with pytest.raises(NeedsImageExtraction) as e:
        read(data)
    assert e.value.pages == [1]


def test_mixed_pdf_reads_text_pages_and_lists_image_pages():
    data = make_pdf([grid_page([HEADER, ["1", "Ann", "5"]]), IMAGE_ONLY])
    info = inspect_pdf(data)
    assert info.kind == "mixed" and info.text_pages == [1] and info.image_pages == [2]
    table, m = read(data)
    assert table.rows == [["1", "Ann", "5"]] and m.pages == [1] and m.ignored_pages == [2]


def test_empty_pdf_kind():
    data = make_pdf([b"0.5 w 10 10 m 20 20 l S"])
    info = inspect_pdf(data)
    assert info.kind == "empty" and info.text_pages == [] and info.image_pages == []
    with pytest.raises(NotATable):
        read(data)


def test_render_pages_png():
    out = render_pages_png(image_pdf(2), [1, 2])
    assert len(out) == 2 and all(b.startswith(b"\x89PNG") for b in out)
    with pytest.raises(NotATable) as e:
        render_pages_png(image_pdf(11), list(range(1, 12)))
    assert "more than 10 pages" in e.value.detail
    with pytest.raises(NotATable):
        render_pages_png(image_pdf(1), [5])


def test_random_bytes_and_truncated_pdf():
    for junk in (b"not a pdf at all" * 20, b"", two_page_pdf()[:60]):
        with pytest.raises(NotATable) as e:
            inspect_pdf(junk)
        assert e.value.detail == "not a readable PDF"
        with pytest.raises(NotATable):
            read(junk)
    with pytest.raises(NotATable):
        render_pages_png(b"junk", [1])


def test_canonical_round_trip_through_duckdb(tmp_path):
    table, m = read(two_page_pdf())
    path = tmp_path / "c.csv"
    path.write_bytes(canonical_csv(table))
    con = duckdb.connect(":memory:")
    rel = con.execute("SELECT * FROM read_csv($path, header = true, sample_size = -1)", {"path": str(path)})
    assert [d[0] for d in rel.description] == table.header
    assert len(rel.fetchall()) == 4
    assert '"format": "pdf"' in manifest_json(m).decode()
