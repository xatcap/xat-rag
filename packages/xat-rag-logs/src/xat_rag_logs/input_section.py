"""The input section of a context pack: log templates and the question, within a token cap."""

from collections.abc import Callable
from dataclasses import dataclass

from xat_rag_core.errors import ContextTooLargeError

from xat_rag_logs.builder import LogQuery, LogTemplate


@dataclass(frozen=True, slots=True)
class FittedInput:
    """The input text, its token count, the number of dropped templates, and if one example was cut."""

    text: str
    tokens: int
    dropped_templates: int
    truncated: bool


def _line(template: LogTemplate, example: str | None = None) -> str:
    text = template.example if example is None else example
    return f"[x{template.count}] {text}" if template.count > 1 else text


def _render(templates: list[LogTemplate], question: str, override: tuple[LogTemplate, str] | None = None) -> str:
    lines = [
        _line(t, override[1] if override is not None and t is override[0] else None)
        for t in sorted(templates, key=lambda t: t.first_index)
    ]
    parts = ["Log lines:\n" + "\n".join(lines)] if lines else []
    if question:
        parts.append("Question: " + question)
    return "\n\n".join(parts)


def fit_input(query: LogQuery, max_tokens: int, count: Callable[[str], int]) -> FittedInput:
    """Build the input text within `max_tokens`. Drop the templates with the lowest rank first.

    The kept lines stay in their log order. A frequent template shows its count. If the best
    template alone is too large, its example is cut. The result is never larger than `max_tokens`.
    Raise `ContextTooLargeError` if the question alone does not fit.
    """
    question_only = _render([], query.question)
    question_tokens = count(question_only)
    if question_tokens > max_tokens:
        raise ContextTooLargeError("The question is larger than the input cap. Use a shorter question.")
    templates = list(query.templates)  # In rank order: the last one is the least important.
    total = len(templates)

    def fits(kept: int) -> tuple[bool, str, int]:
        text = _render(templates[:kept], query.question)
        tokens = count(text)
        return tokens <= max_tokens, text, tokens

    # Binary search for the largest prefix that fits, with O(log n) counts.
    low, high = 0, total
    while low < high:
        middle = (low + high + 1) // 2
        if fits(middle)[0]:
            low = middle
        else:
            high = middle - 1
    if low > 0:
        _, text, tokens = fits(low)
        return FittedInput(text, tokens, total - low, truncated=False)
    if total == 0:
        return FittedInput(question_only, question_tokens, 0, truncated=False)

    best = templates[0]
    low, high = 0, len(best.example)
    while low < high:
        middle = (low + high + 1) // 2
        if count(_render([best], query.question, (best, best.example[:middle]))) <= max_tokens:
            low = middle
        else:
            high = middle - 1
    text = _render([best], query.question, (best, best.example[:low]))
    tokens = count(text)
    if low == 0 or tokens > max_tokens:
        return FittedInput(question_only, question_tokens, total, truncated=False)
    return FittedInput(text, tokens, total - 1, truncated=True)
