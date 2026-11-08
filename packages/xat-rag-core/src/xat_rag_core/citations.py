"""The citation grammar, the JSON schema, and the check of an answer. See ADR 0008."""

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, cast

from xat_rag_core.errors import InternalError
from xat_rag_core.render import chunk_label

_STRING_RULE = (
    r'string ::= "\"" ( [^"\\\x7F\x00-\x1F] | "\\" ( ["\\/bfnrt] | "u" hex hex hex hex ) )* "\""'
    "\n"
    r"hex ::= [0-9a-fA-F]"
)


def citation_grammar(labels: Sequence[str]) -> str:
    """Return a GBNF grammar for `{"answer": "...", "citations": [...]}` that allows only `labels`."""
    for position, label in enumerate(labels, start=1):
        if label != chunk_label(position):
            raise InternalError("Citation labels must be C1, C2 and so on, in order.")
    lines = [
        'root ::= "{" ws "\\"answer\\"" ws ":" ws string ws "," ws "\\"citations\\"" ws ":" ws citations ws "}"',
    ]
    if labels:
        lines.append('citations ::= "[" ws ( label ( ws "," ws label )* )? ws "]"')
        lines.append("label ::= " + " | ".join(f'"\\"{label}\\""' for label in labels))
    else:
        lines.append('citations ::= "[" ws "]"')
    lines.append(_STRING_RULE)
    lines.append(r"ws ::= [ \t\n]*")
    return "\n".join(lines) + "\n"


def citation_json_schema(labels: Sequence[str]) -> dict[str, Any]:
    """Return a JSON schema for the answer. Use it only for a generator that does not accept a grammar."""
    citations: dict[str, Any] = (
        {"type": "array", "items": {"type": "string", "enum": list(labels)}}
        if labels
        else {"type": "array", "maxItems": 0}
    )
    return {
        "type": "object",
        "properties": {"answer": {"type": "string"}, "citations": citations},
        "required": ["answer", "citations"],
        "additionalProperties": False,
    }


class CitationProblem(StrEnum):
    """Why an answer is not valid."""

    NOT_JSON = "not_json"
    BAD_SHAPE = "bad_shape"
    UNKNOWN_LABEL = "unknown_label"
    MISSING_CHUNK = "missing_chunk"


@dataclass(frozen=True, slots=True)
class Citation:
    """A valid citation: a label and its chunk ID."""

    label: str
    chunk_id: str


@dataclass(frozen=True, slots=True)
class CitationCheck:
    """The result of a citation check."""

    valid: bool
    problem: CitationProblem | None = None
    answer: str | None = None
    citations: tuple[Citation, ...] = ()
    unknown_labels: tuple[str, ...] = field(default=())
    missing_chunks: tuple[str, ...] = field(default=())


def check_answer(  # noqa: PLR0911 - one early return for each kind of problem is the clearest form.
    answer_text: str,
    chunk_by_label: Mapping[str, str],
    chunk_exists: Callable[[str], bool] | None = None,
) -> CitationCheck:
    """Check the answer of the generator against the chunk list of its pack.

    The answer must be JSON with a string `answer` and a list `citations` of labels. Each label
    must be in `chunk_by_label`. If `chunk_exists` is given, each cited chunk must still exist.
    An answer that the generator cut at `max_tokens` is not valid JSON.
    """
    try:
        parsed: object = json.loads(answer_text)
    except (ValueError, RecursionError):  # Deep nesting in untrusted output gives RecursionError.
        return CitationCheck(valid=False, problem=CitationProblem.NOT_JSON)
    if not isinstance(parsed, dict):
        return CitationCheck(valid=False, problem=CitationProblem.BAD_SHAPE)
    body = cast(dict[str, object], parsed)
    answer = body.get("answer")
    raw_citations = body.get("citations")
    if not isinstance(answer, str) or not isinstance(raw_citations, list):
        return CitationCheck(valid=False, problem=CitationProblem.BAD_SHAPE)
    labels: list[str] = []
    for value in cast(list[object], raw_citations):
        if not isinstance(value, str):
            return CitationCheck(valid=False, problem=CitationProblem.BAD_SHAPE, answer=answer)
        if value not in labels:
            labels.append(value)

    unknown = tuple(label for label in labels if label not in chunk_by_label)
    citations = tuple(Citation(label, chunk_by_label[label]) for label in labels if label in chunk_by_label)
    if unknown:
        return CitationCheck(False, CitationProblem.UNKNOWN_LABEL, answer, citations, unknown_labels=unknown)
    if chunk_exists is not None:
        missing = tuple(citation.chunk_id for citation in citations if not chunk_exists(citation.chunk_id))
        if missing:
            return CitationCheck(False, CitationProblem.MISSING_CHUNK, answer, citations, missing_chunks=missing)
    return CitationCheck(True, None, answer, citations)
