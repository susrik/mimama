"""Text extraction from binary document formats (PowerPoint, Word, PDF, Excel).

These extractors run on raw bytes read from an attached context file so the
same logic backs both the GUI upload path and the server-side ``read_files``
path. Every extractor is best-effort: on any parse failure it raises
:class:`DocumentError` (or returns empty text), never a bare library exception,
so a malformed attachment degrades to a clean rejection instead of a 500.
"""

from __future__ import annotations

import io
import struct
from collections.abc import Callable
from typing import Any

__all__ = ["DocumentError", "extract_text", "is_document"]


class DocumentError(Exception):
    """A binary attachment could not be identified or had no extractable text."""


def _extension(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def _extract_pptx(data: bytes) -> str:
    from pptx import Presentation

    try:
        prs = Presentation(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001 - any parse failure is a clean rejection
        raise DocumentError("could not read PowerPoint file") from exc
    parts: list[str] = []
    for slide in prs.slides:
        for shape in slide.shapes:
            if shape.has_text_frame:
                text = shape.text_frame.text
                if text.strip():
                    parts.append(text)
            elif getattr(shape, "has_table", False):
                for row in shape.table.rows:
                    cells = [cell.text for cell in row.cells if cell.text.strip()]
                    if cells:
                        parts.append("\t".join(cells))
    return "\n\n".join(parts)


def _extract_docx(data: bytes) -> str:
    from docx import Document

    try:
        doc = Document(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001
        raise DocumentError("could not read Word file") from exc
    parts = [p.text for p in doc.paragraphs if p.text.strip()]
    for table in doc.tables:
        for row in table.rows:
            cells = [cell.text for cell in row.cells if cell.text.strip()]
            if cells:
                parts.append("\t".join(cells))
    return "\n\n".join(parts)


def _extract_pdf(data: bytes) -> str:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001
        raise DocumentError("could not read PDF file") from exc
    parts: list[str] = []
    for page in reader.pages:
        text = page.extract_text() or ""
        if text.strip():
            parts.append(text)
    return "\n\n".join(parts)


def _extract_xlsx(data: bytes) -> str:
    from openpyxl import load_workbook  # type: ignore[import-untyped]

    try:
        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001
        raise DocumentError("could not read Excel file") from exc
    parts: list[str] = []
    for sheet in workbook.worksheets:
        lines: list[str] = []
        for row in sheet.iter_rows(values_only=True):
            cells = [str(cell).strip() for cell in row if _nonempty(cell)]
            if cells:
                lines.append("\t".join(cells))
        if lines:
            parts.append(f"[{sheet.title}]\n" + "\n".join(lines))
    return "\n\n".join(parts)


def _nonempty(value: object) -> bool:
    return value is not None and str(value).strip() != ""


def _extract_doc(data: bytes) -> str:
    """Best-effort text extraction from a legacy Word .doc (OLE compound file)."""
    import olefile  # type: ignore[import-untyped]

    try:
        ole = olefile.OleFileIO(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001
        raise DocumentError("could not read legacy .doc file") from exc
    return _parse_doc_text(*_read_doc_streams(ole))  # pragma: no cover


def _read_doc_streams(ole: Any) -> tuple[bytes, bytes]:  # pragma: no cover
    """Read the WordDocument and table streams from an open OLE container.

    Not unit-tested: exercising it needs a real Word-97+ .doc container, which
    no available test dependency can produce (textutil emits Word 6/95; olefile
    is read-only). The pure piece-table parser (:func:`_parse_doc_text`) is
    covered directly.
    """
    if not ole.exists("WordDocument"):
        raise DocumentError("not a Word document (missing WordDocument stream)")
    word = ole.openstream("WordDocument").read()
    nfib = struct.unpack_from("<H", word, 0x02)[0]
    flags = struct.unpack_from("<H", word, 0x0A)[0]
    if (flags >> 9) & 1:
        name = "1Table" if nfib >= 0x00C1 else "0Table"
        if ole.exists(name):
            return word, ole.openstream(name).read()
    return word, word


def _parse_doc_text(word: bytes, table: bytes) -> str:
    """Reconstruct text from the Word piece table (FIB + CLX).

    ``word`` is the full ``WordDocument`` stream (holds both the FIB and the
    piece text); ``table`` is the stream holding the piece-table CLX (the table
    stream, or ``word`` itself). Raises :class:`DocumentError` on any anomaly.
    """
    try:
        if len(word) < 0x20 or word[0:2] != b"\xec\xa5":
            raise DocumentError("unrecognized .doc FIB header")
        csw = struct.unpack_from("<H", word, 0x20)[0]
        cslw = struct.unpack_from("<H", word, 0x22 + csw * 2)[0]
        fib_fc_lcb = 0x22 + csw * 2 + 2 + cslw * 4 + 2
        # fcClx/lcbClx are the two fields before the trailing extensions, i.e.
        # at a fixed 264/268-byte offset within FibRgFcLcb for all >= 97 variants.
        fc_clx = struct.unpack_from("<I", word, fib_fc_lcb + 264)[0]
        lcb_clx = struct.unpack_from("<I", word, fib_fc_lcb + 268)[0]
        clx = table[fc_clx : fc_clx + lcb_clx]
        offset = 0
        while offset < len(clx):
            clxt = clx[offset]
            if clxt == 0x01:  # Prc: skip its GrpPrl
                cb_grpprl = struct.unpack_from("<H", clx, offset + 1)[0]
                offset += 3 + cb_grpprl
                continue
            if clxt == 0x02:  # Pcdt: the piece table
                break
            raise DocumentError("piece table not found in .doc")
        else:
            raise DocumentError("piece table not found in .doc")

        lcb = struct.unpack_from("<I", clx, offset + 1)[0]
        plc_pcd = clx[offset + 5 : offset + 5 + lcb]
        n = (lcb + 8) // 12  # n CPs (4 bytes) then n-1 Pcds (8 bytes)
        pieces: list[str] = []
        for i in range(n - 1):
            cp_start = struct.unpack_from("<I", plc_pcd, i * 4)[0]
            cp_end = struct.unpack_from("<I", plc_pcd, (i + 1) * 4)[0]
            # The CP array is n * 4 bytes; the Pcd array (8 bytes each) follows.
            fc = struct.unpack_from("<I", plc_pcd, n * 4 + i * 8)[0]
            compressed = (fc >> 30) & 1
            fc_offset = fc & 0x3FFFFFFF
            byte_len = (cp_end - cp_start) * (1 if compressed else 2)
            piece = word[fc_offset : fc_offset + byte_len]
            pieces.append(
                piece.decode("cp1252" if compressed else "utf-16-le", errors="replace")
            )
    except DocumentError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise DocumentError("could not parse .doc text") from exc

    return _clean_doc_text("".join(pieces))


def _clean_doc_text(raw: str) -> str:
    out: list[str] = []
    for ch in raw:
        code = ord(ch)
        if code == 0x0D:  # paragraph mark
            out.append("\n")
            continue
        if code in (0x07, 0x0B):  # table cell / line break
            out.append("\t")
            continue
        if code in (0x13, 0x14, 0x15, 0x01):  # field begin/separate/end markers
            continue
        if code < 0x20:
            continue
        out.append(ch)
    return "".join(out)


_EXTRACTORS: dict[str, Callable[[bytes], str]] = {
    "pptx": _extract_pptx,
    "docx": _extract_docx,
    "pdf": _extract_pdf,
    "xlsx": _extract_xlsx,
    "doc": _extract_doc,
}


def is_document(name: str) -> bool:
    """Whether ``name`` identifies a binary document we know how to extract."""
    return _extension(name) in _EXTRACTORS


def extract_text(name: str, data: bytes) -> str:
    """Extract text from a supported binary document, keyed by file extension.

    Raises :class:`DocumentError` for unsupported types or unreadable content.
    May return an empty string when the document has no extractable text.
    """
    extractor = _EXTRACTORS.get(_extension(name))
    if extractor is None:
        raise DocumentError(f"unsupported document type: {name!r}")
    return extractor(data)
