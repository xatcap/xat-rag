"""Checks of a zip container (DOCX or XLSX) before the parse."""

import re
import zipfile
from io import BytesIO

from xat_rag_core.errors import ParseError, ParseReason

from xat_rag_documents.limits import DocumentLimits, limit_exceeded

_TAG = re.compile(rb"<Relationship\b[^>]*>")
_ATTRIBUTE = re.compile(rb'(\w+)="([^"]*)"')
# The bounded repeat keeps the time of the search linear on a hostile file.
_GRID_SPAN = re.compile(rb"gridSpan[^>]{0,64}?val=\"(\d{1,12})\"")
_MAX_GRID_SPAN = 64


def _main_part(archive: zipfile.ZipFile, names: list[str]) -> str:
    """Return the name of the main document part, from `_rels/.rels`."""
    if "_rels/.rels" not in names:
        raise ParseError(ParseReason.INVALID, "The file has no package relationships.")
    for tag in _TAG.findall(archive.read("_rels/.rels")):
        attributes = dict(_ATTRIBUTE.findall(tag))
        if attributes.get(b"Type", b"").endswith(b"/officeDocument"):
            return attributes.get(b"Target", b"").decode("utf-8", "replace").lstrip("/")
    raise ParseError(ParseReason.INVALID, "The file has no main document part.")


def check_zip(data: bytes, limits: DocumentLimits) -> list[str]:
    """Check the zip container before the parse, and return the part names. Raise `ParseError` on a problem."""
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            infos = archive.infolist()
            if len(infos) > limits.max_zip_entries:
                raise limit_exceeded("max_zip_entries")
            if sum(info.file_size for info in infos) > limits.max_zip_uncompressed:
                raise limit_exceeded("max_zip_uncompressed")
            for info in infos:
                if info.file_size > 1_000_000 and info.file_size > limits.max_zip_ratio * max(info.compress_size, 1):
                    raise limit_exceeded("max_zip_ratio")
            names = [info.filename for info in infos]
            main = _main_part(archive, names)
            sizes = {info.filename: info.file_size for info in infos}
            if main not in sizes:
                raise ParseError(ParseReason.INVALID, "The main document part of the file is missing.")
            if sizes[main] > limits.max_docx_main_part:
                raise limit_exceeded("max_docx_main_part")
            content_types = archive.read("[Content_Types].xml") if "[Content_Types].xml" in names else b""
            if (
                any(name.lower().endswith("vbaproject.bin") for name in names)
                or b"macroenabled" in content_types.lower()
            ):
                raise ParseError(ParseReason.UNSUPPORTED_MEDIA_TYPE, "files with macros are refused.")
            for match in _GRID_SPAN.finditer(archive.read(main)):
                if int(match.group(1)) > _MAX_GRID_SPAN:
                    raise limit_exceeded("max_grid_span")
    except (zipfile.BadZipFile, zipfile.LargeZipFile, NotImplementedError, RuntimeError, OSError, KeyError) as error:
        raise ParseError(ParseReason.INVALID, "The file is not a valid zip file.") from error
    return names
