"""Read a `multipart/form-data` upload in memory, with a small linear parser.

The document bytes never go to temporary files. The parser finds the boundaries with `bytes.find`,
reads only the small part headers, and slices each payload once. The part count is checked before
any part is read. The request size is checked before this runs.
"""

import json
import re
from dataclasses import dataclass
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from xat_rag_core.errors import InvalidRequestError
from xat_rag_core.model import RawDocument

_BOUNDARY = re.compile(r'boundary="?([^";\s]{1,200})"?', re.IGNORECASE)
_NAME = re.compile(rb'\bname="([^"\r\n]{1,200})"', re.IGNORECASE)
_MAX_HEADER = 8192

MAX_DOCUMENTS = 256
type MetadataValue = str | int | float | bool


class DocumentFields(BaseModel):
    """The fields of one document in the `documents` part."""

    model_config = ConfigDict(extra="forbid")

    doc_id: str = Field(min_length=1, max_length=512)
    kind: str
    part: str = Field(min_length=1, max_length=128)
    uri: str = Field(default="", max_length=2048)
    title: str = Field(default="", max_length=1024)
    version: str = Field(default="", max_length=256)
    metadata: dict[str, MetadataValue] = Field(default_factory=dict[str, MetadataValue])


@dataclass(frozen=True, slots=True)
class Upload:
    """The documents of an upload."""

    fields: list[DocumentFields]
    files: dict[str, bytes]


def _parts(content_type: str, body: bytes) -> dict[str, bytes]:
    match = _BOUNDARY.search(content_type)
    if not content_type.lower().startswith("multipart/form-data") or match is None:
        raise InvalidRequestError("The upload must use multipart/form-data with a boundary.")
    delimiter = b"--" + match.group(1).encode("latin-1")
    if body.count(delimiter) > MAX_DOCUMENTS + 2:
        raise InvalidRequestError(f"An upload can have at most {MAX_DOCUMENTS} documents.")
    start = body.find(delimiter)
    if start < 0:
        raise InvalidRequestError("The upload has no parts.")
    parts: dict[str, bytes] = {}
    position = start + len(delimiter)
    while not body.startswith(b"--", position):
        if not body.startswith(b"\r\n", position):
            raise InvalidRequestError("The upload is not valid multipart data.")
        header_end = body.find(b"\r\n\r\n", position + 2, position + 2 + _MAX_HEADER)
        if header_end < 0:
            raise InvalidRequestError("A part of the upload has no valid headers.")
        headers = body[position + 2 : header_end]
        following = body.find(b"\r\n" + delimiter, header_end + 4)
        if following < 0:
            raise InvalidRequestError("The upload is not complete. The closing boundary is missing.")
        name = _NAME.search(headers)
        if name is None or b"content-transfer-encoding" in headers.lower():
            raise InvalidRequestError("Each part needs a name, and no Content-Transfer-Encoding.")
        key = name.group(1).decode("utf-8", "replace")
        if key in parts:
            raise InvalidRequestError("Each part needs a unique name.")
        parts[key] = body[header_end + 4 : following]
        position = following + 2 + len(delimiter)
    return parts


def read_upload(content_type: str, body: bytes) -> Upload:
    """Read the `documents` JSON part and the file parts. Raise `InvalidRequestError` on a problem."""
    parts = _parts(content_type, body)
    if "documents" not in parts:
        raise InvalidRequestError("The upload needs a 'documents' part with the document fields as JSON.")
    try:
        raw: object = json.loads(parts.pop("documents"))
        if not isinstance(raw, list):
            raise ValueError("not a list")
        fields = [DocumentFields.model_validate(item) for item in cast(list[Any], raw)]
    except (ValueError, TypeError, RecursionError, ValidationError) as error:
        raise InvalidRequestError("The 'documents' part is not a valid list of document fields.") from error
    if len(fields) > MAX_DOCUMENTS:
        raise InvalidRequestError(f"An upload can have at most {MAX_DOCUMENTS} documents.")
    if any(item.part not in parts for item in fields):
        raise InvalidRequestError("A document names a file part that is not in the upload.")
    return Upload(fields, parts)


def raw_documents(upload: Upload, rag: str) -> list[RawDocument]:
    """Return the raw documents of an upload for one RAG."""
    from xat_rag.index import source_kind  # noqa: PLC0415 - keeps this module light.

    return [
        RawDocument(
            doc_id=item.doc_id,
            rag=rag,
            source_kind=source_kind(item.kind),
            data=upload.files[item.part],
            uri=item.uri,
            title=item.title,
            version=item.version,
            metadata=dict(item.metadata),
        )
        for item in upload.fields
    ]
