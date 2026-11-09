import pytest
from xat_rag_core.errors import InternalError
from xat_rag_core.model import BlockSpan, LineSpan, PageSpan
from xat_rag_core.spans import span_from_meta, span_to_meta


def test_spans_round_trip() -> None:
    for span in (LineSpan(1, 2), PageSpan(3, 4, "iii", "iv"), PageSpan(1, 2), BlockSpan(2, 5)):
        assert span_from_meta(span_to_meta(span)) == span


def test_spans_of_each_type_have_the_type_name() -> None:
    assert span_to_meta(LineSpan(1, 2))["type"] == "line"
    assert span_to_meta(PageSpan(1, 2))["type"] == "page"
    assert span_to_meta(BlockSpan(1, 2))["type"] == "block"


def test_bad_spans_are_refused() -> None:
    with pytest.raises(InternalError, match="unknown type"):
        span_from_meta({"type": "x", "start": 1, "end": 2})
    with pytest.raises(InternalError, match="not complete"):
        span_from_meta({"type": "line", "start": 1})  # pyright: ignore[reportArgumentType]
    with pytest.raises(InternalError, match="not an object"):
        span_from_meta("line")  # pyright: ignore[reportArgumentType]
