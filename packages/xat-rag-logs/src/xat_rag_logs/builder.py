"""`LogQueryBuilder` (`log-v1`): log lines to a retrieval query, in a deterministic way."""

import posixpath
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import IntEnum

from xat_rag_core.errors import InvalidRequestError
from xat_rag_core.query import ExactLookup, Filter, RetrievalQuery
from xat_rag_core.sanitize import sanitize_text

from xat_rag_logs import patterns

_PLACEHOLDERS = frozenset({"TS", "UUID", "HEX", "SHA", "IP", "NUM"})
_SEVERE = frozenset({"ERROR", "SEVERE", "FATAL", "CRITICAL", "PANIC"})
_WARN = frozenset({"WARN", "WARNING"})


class Priority(IntEnum):
    """The rank group of a template. A lower value is more important."""

    SELECTED = 0
    SEVERE_OR_IDENTIFIER = 1
    WARNING = 2
    OTHER = 3


@dataclass(frozen=True, slots=True)
class LogInput:
    """The input of the builder: log lines, the indexes of the lines that the user selected, and a question."""

    lines: tuple[str, ...]
    selected: frozenset[int] = frozenset()
    question: str = ""


@dataclass(frozen=True, slots=True)
class LogTemplate:
    """Equal masked lines, with a count and the first raw line as an example."""

    template: str
    example: str
    count: int
    first_index: int
    priority: Priority

    @property
    def rank_key(self) -> tuple[int, int, int]:
        """The sort key: priority group, then rare before frequent, then the first line."""
        return (int(self.priority), self.count, self.first_index)


def _empty_strings() -> tuple[str, ...]:
    return ()


@dataclass(frozen=True, slots=True)
class LogQuery:
    """The result of the builder: the retrieval query, the templates in rank order, and the question."""

    query: RetrievalQuery
    templates: tuple[LogTemplate, ...]
    question: str = ""
    identifiers: tuple[str, ...] = field(default_factory=_empty_strings)


def _empty_list() -> list[str]:
    return []


@dataclass(slots=True)
class _Analysis:
    exceptions: list[str] = field(default_factory=_empty_list)
    jira_keys: list[str] = field(default_factory=_empty_list)
    files: list[str] = field(default_factory=_empty_list)
    codes: list[str] = field(default_factory=_empty_list)
    endpoints: list[str] = field(default_factory=_empty_list)
    shas: list[str] = field(default_factory=_empty_list)


def _add(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)


def _identifiers(line: str, found: _Analysis) -> bool:
    """Add the identifiers of `line` to `found`. Return `True` if the line has at least one."""
    matches = 0
    for match in patterns.EXCEPTION.finditer(line):
        matches += 1
        _add(found.exceptions, match.group(0))
    frame_patterns = [patterns.JAVA_FRAME, patterns.PYTHON_FRAME]
    if patterns.FILE_LINE_HINT.search(line):  # A cheap check before the costly pattern.
        frame_patterns.append(patterns.FILE_LINE)
    for pattern in frame_patterns:
        for match in pattern.finditer(line):
            matches += 1
            _add(found.files, posixpath.basename(match.group(1).replace("\\", "/")))
    without_uuids = patterns.UUID.sub(" ", line)  # Parts of a UUID look like SHAs.
    for target, pattern in (
        (found.jira_keys, patterns.JIRA_KEY),
        (found.codes, patterns.ERROR_CODE),
        (found.shas, patterns.SHA),
    ):
        for match in pattern.finditer(without_uuids):
            value = match.group(0)
            if pattern is patterns.JIRA_KEY and value.split("-", 1)[0] in patterns.NOT_JIRA_PREFIXES:
                continue
            matches += 1
            _add(target, value)
    for match in patterns.ENDPOINT.finditer(line):
        matches += 1
        _add(found.endpoints, match.group(1))
    return matches > 0


def mask(line: str) -> str:
    """Mask the variable parts of a line, and collapse white space."""
    for pattern, replacement in patterns.MASKS:
        line = pattern.sub(replacement, line)
    return " ".join(line.split())


def _priority(line: str, selected: bool, has_identifier: bool) -> Priority:
    if selected:
        return Priority.SELECTED
    levels = {match.group(1) for match in patterns.LEVEL.finditer(line)}
    if levels & _SEVERE or has_identifier:
        return Priority.SEVERE_OR_IDENTIFIER
    if levels & _WARN:
        return Priority.WARNING
    return Priority.OTHER


class LogQueryBuilder:
    """Builds a retrieval query from log lines. The same input gives the same query."""

    def __init__(self, max_lexical_terms: int = 32, max_lookups: int = 16, filters: Filter | None = None) -> None:
        """Make a builder. `filters` are the hard filters from the recipe, if any."""
        self._max_terms = max_lexical_terms
        self._max_lookups = max_lookups
        self._filters = filters if filters is not None else Filter()

    @property
    def name(self) -> str:
        """The name of the builder in recipes."""
        return "log-v1"

    def build(self, query_input: LogInput) -> RetrievalQuery:
        """Return the retrieval query."""
        return self.analyze(query_input).query

    def analyze(self, query_input: LogInput) -> LogQuery:
        """Return the retrieval query, the ranked templates and the identifiers."""
        if len(query_input.lines) > patterns.MAX_LINES:
            raise InvalidRequestError(f"A request can have at most {patterns.MAX_LINES} log lines.")
        found = _Analysis()
        groups: dict[str, LogTemplate] = {}
        for index, raw in enumerate(query_input.lines):
            line = sanitize_text(raw[: patterns.MAX_LINE_CHARS]).replace("\n", " ").strip()
            if not line:
                continue
            has_identifier = _identifiers(line, found)
            priority = _priority(line, index in query_input.selected, has_identifier)
            template = mask(line)
            old = groups.get(template)
            if old is None:
                groups[template] = LogTemplate(template, line, 1, index, priority)
            else:
                groups[template] = LogTemplate(
                    template, old.example, old.count + 1, old.first_index, min(old.priority, priority)
                )
        templates = tuple(sorted(groups.values(), key=lambda t: t.rank_key))
        question = sanitize_text(query_input.question).strip()
        identifiers = tuple(
            dict.fromkeys(
                (*found.exceptions, *found.jira_keys, *found.files, *found.codes, *found.endpoints, *found.shas)
            )
        )
        query = RetrievalQuery(
            lexical_terms=self._lexical_terms(identifiers, question, templates),
            dense_text=_join_capped([question, *(t.template for t in templates)], 2000),
            rerank_text=_join_capped([question, *(t.template for t in templates[:3])], 600),
            exact_lookups=self._lookups(found),
            filters=self._filters,
        )
        return LogQuery(query=query, templates=templates, question=question, identifiers=tuple(identifiers))

    def _lexical_terms(
        self, identifiers: Sequence[str], question: str, templates: Sequence[LogTemplate]
    ) -> tuple[str, ...]:
        question_words = [word for word in patterns.WORD.findall(question) if word.lower() not in patterns.STOPWORDS]
        # Keep room for the question, so that many identifiers cannot push it out.
        reserved = min(len(question_words), self._max_terms // 4)
        terms: list[str] = []
        for value in identifiers[: self._max_terms - reserved]:
            _add(terms, value)
        for word in question_words:
            _add(terms, word)
        if len(terms) < 8:
            for template in templates:
                for word in patterns.WORD.findall(template.template):
                    if word.lower() not in patterns.STOPWORDS and word.upper() not in {
                        "TS",
                        "UUID",
                        "HEX",
                        "IP",
                        "NUM",
                    }:
                        _add(terms, word)
        return tuple(terms[: self._max_terms])

    def _lookups(self, found: _Analysis) -> tuple[ExactLookup, ...]:
        lookups = [ExactLookup("jira_key", key) for key in found.jira_keys]
        lookups += [ExactLookup("path_name", name.lower()) for name in found.files]
        return tuple(lookups[: self._max_lookups])


def _join_capped(parts: Sequence[str], max_chars: int) -> str:
    text = "\n".join(part for part in parts if part)
    return text[:max_chars]
