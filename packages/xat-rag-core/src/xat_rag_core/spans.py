"""The stored form of a chunk span."""

from typing import NotRequired, TypedDict

from xat_rag_core.errors import InternalError
from xat_rag_core.model import BlockSpan, CellSpan, LineSpan, PageSpan, Span


class SpanMeta(TypedDict):
    """The span of a chunk, as metadata. `start` and `end` are lines, pages or blocks."""

    type: str
    start: int
    end: int
    label_start: NotRequired[str | None]
    label_end: NotRequired[str | None]
    sheet: NotRequired[str]


def span_to_meta(span: Span) -> SpanMeta:
    """Convert a span to metadata."""
    match span:
        case LineSpan(line_start=start, line_end=end):
            return {"type": "line", "start": start, "end": end}
        case PageSpan(page_start=start, page_end=end, label_start=label_start, label_end=label_end):
            return {"type": "page", "start": start, "end": end, "label_start": label_start, "label_end": label_end}
        case BlockSpan(block_start=start, block_end=end):
            return {"type": "block", "start": start, "end": end}
        case CellSpan(sheet=sheet, row_start=start, row_end=end):
            return {"type": "cell", "start": start, "end": end, "sheet": sheet}
        case _:
            raise InternalError("A chunk span has an unknown type.")


def span_from_meta(meta: SpanMeta) -> Span:
    """Convert metadata to a span. Raise `InternalError` for a bad value."""
    if not isinstance(meta, dict):  # pyright: ignore[reportUnnecessaryIsInstance] - metadata is untyped at runtime.
        raise InternalError("A chunk span in the metadata is not an object.")
    kind = meta.get("type")
    try:
        if kind == "line":
            return LineSpan(meta["start"], meta["end"])
        if kind == "page":
            return PageSpan(meta["start"], meta["end"], meta.get("label_start"), meta.get("label_end"))
        if kind == "block":
            return BlockSpan(meta["start"], meta["end"])
        if kind == "cell":
            sheet = meta.get("sheet")
            if sheet is None:
                raise KeyError("sheet")
            return CellSpan(sheet, meta["start"], meta["end"])
    except KeyError as error:
        raise InternalError("A chunk span in the metadata is not complete.") from error
    raise InternalError("A chunk span in the metadata has an unknown type.")
