"""The retrieval query, exact lookups and hard filters."""

import re
from dataclasses import dataclass, field

from xat_rag_core.errors import InvalidRequestError
from xat_rag_core.model import Scalar
from xat_rag_core.sanitize import sanitize_text

_FIELD = re.compile(r"[a-z_][a-z0-9_]{0,63}")


def check_field_name(name: str) -> str:
    """Return `name` if it is a valid metadata field name. Otherwise raise `InvalidRequestError`."""
    if not _FIELD.fullmatch(name):
        raise InvalidRequestError(
            "A metadata field name must have 1 to 64 lowercase letters, digits or underscores, "
            "and must not start with a digit."
        )
    return name


@dataclass(frozen=True, slots=True)
class Equals:
    """The field is equal to the value."""

    field: str
    value: Scalar

    def __post_init__(self) -> None:
        """Check the field name."""
        check_field_name(self.field)


@dataclass(frozen=True, slots=True)
class OneOf:
    """The field is equal to one of the values."""

    field: str
    values: tuple[Scalar, ...]

    def __post_init__(self) -> None:
        """Check the field name and the values."""
        check_field_name(self.field)
        if not self.values:
            raise InvalidRequestError("A 'one of' filter needs at least one value.")


@dataclass(frozen=True, slots=True)
class Range:
    """The field is in a range. A missing bound means no limit on that side. Both bounds are included."""

    field: str
    minimum: int | float | str | None = None
    maximum: int | float | str | None = None

    def __post_init__(self) -> None:
        """Check the field name and the bounds."""
        check_field_name(self.field)
        if self.minimum is None and self.maximum is None:
            raise InvalidRequestError("A range filter needs a minimum, a maximum, or both.")


type Condition = Equals | OneOf | Range


@dataclass(frozen=True, slots=True)
class Filter:
    """Hard filters. All conditions must be true. They remove candidates before the top-k cut."""

    conditions: tuple[Condition, ...] = ()

    def __bool__(self) -> bool:
        """Return `False` when the filter has no conditions."""
        return bool(self.conditions)


@dataclass(frozen=True, slots=True)
class ExactLookup:
    """An exact metadata lookup. It adds candidates. It never removes candidates."""

    field: str
    value: str

    def __post_init__(self) -> None:
        """Check the field name."""
        check_field_name(self.field)


@dataclass(frozen=True, slots=True)
class RetrievalQuery:
    """The query parts. Each retrieval stage gets its own text."""

    lexical_terms: tuple[str, ...] = ()
    dense_text: str = ""
    rerank_text: str = ""
    exact_lookups: tuple[ExactLookup, ...] = ()
    filters: Filter = field(default_factory=Filter)


_TERM = re.compile(r"[^\W_][\w.$/:#@-]{0,80}")
_EDGE_PUNCTUATION = ".:/#@-"


class TextQueryBuilder:
    """Builds a retrieval query from a question (`text-v1`). The same question gives the same query."""

    def __init__(self, filters: Filter | None = None, max_lexical_terms: int = 32) -> None:
        """Make a builder. `filters` are the hard filters from the recipe, if any."""
        if not 1 <= max_lexical_terms <= 256:
            raise InvalidRequestError("max_lexical_terms must be between 1 and 256.")
        self._filters = filters if filters is not None else Filter()
        self._max_terms = max_lexical_terms

    @property
    def name(self) -> str:
        """The name of the builder in recipes."""
        return "text-v1"

    def build(self, query_input: str) -> RetrievalQuery:
        """Return the retrieval query for a question. Terms are unique without case, and keep code names whole."""
        question = " ".join(sanitize_text(query_input).split())
        terms: dict[str, str] = {}
        for match in _TERM.finditer(question):
            term = match.group(0).rstrip(_EDGE_PUNCTUATION)
            terms.setdefault(term.lower(), term)
        return RetrievalQuery(
            lexical_terms=tuple(terms.values())[: self._max_terms],
            dense_text=question[:2000],
            rerank_text=question[:600],
            filters=self._filters,
        )
