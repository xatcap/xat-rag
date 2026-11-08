"""Extra tests for the log query builder: limits, speed, hostile input and the input section."""

import time

import pytest
from xat_rag_core.errors import ContextTooLargeError, InvalidRequestError
from xat_rag_logs import LogInput, LogQuery, LogQueryBuilder, fit_input
from xat_rag_logs.builder import Priority
from xat_rag_logs.patterns import MAX_LINE_CHARS, MAX_LINES


def words(text: str) -> int:
    """A counter that is close to a real tokenizer: one token for each word."""
    return len(text.split())


def analyze(*lines: str, selected: frozenset[int] = frozenset(), question: str = "") -> LogQuery:
    return LogQueryBuilder().analyze(LogInput(tuple(lines), selected=selected, question=question))


# Limits.


def test_more_than_the_maximum_number_of_lines_is_refused() -> None:
    with pytest.raises(InvalidRequestError, match=str(MAX_LINES)):
        LogQueryBuilder().analyze(LogInput(("x",) * (MAX_LINES + 1)))
    with pytest.raises(InvalidRequestError):
        LogQueryBuilder().build(LogInput(("x",) * (MAX_LINES + 1)))


def test_the_maximum_number_of_lines_is_accepted() -> None:
    result = LogQueryBuilder().analyze(LogInput(("x",) * MAX_LINES))
    assert result.templates[0].count == MAX_LINES


def test_blank_and_control_only_lines_are_skipped() -> None:
    result = analyze("", "   ", "\x00\x07", "\u200b\u200b", "real line")
    assert [t.example for t in result.templates] == ["real line"]
    assert result.templates[0].first_index == 4


def test_only_blank_lines_give_an_empty_query() -> None:
    result = analyze("", "  ")
    assert result.templates == ()
    assert result.query.lexical_terms == ()
    assert result.query.dense_text == ""


def test_a_line_is_cut_at_the_maximum_length() -> None:
    result = analyze("a" * (MAX_LINE_CHARS * 3))
    assert len(result.templates[0].example) == MAX_LINE_CHARS


def test_identifier_after_the_cut_is_ignored() -> None:
    result = analyze(" " * (MAX_LINE_CHARS + 10) + "PROJ-123 com.shop.HiddenException")
    assert result.identifiers == ()


def test_the_query_texts_are_capped() -> None:
    lines = tuple(f"ERROR distinct message number {i} " + "word " * 100 for i in range(300))
    query = analyze(*lines, question="Why?").query
    assert len(query.dense_text) <= 2000
    assert len(query.rerank_text) <= 600
    assert query.dense_text.startswith("Why?")


def test_term_and_lookup_caps() -> None:
    lines = tuple(f"ERROR PROJ-{i} failed in File{i}.java:{i}" for i in range(100))
    builder = LogQueryBuilder(max_lexical_terms=5, max_lookups=3)
    query = builder.analyze(LogInput(lines)).query
    assert len(query.lexical_terms) == 5
    assert len(query.exact_lookups) == 3


def test_a_repeated_template_takes_the_best_priority() -> None:
    result = analyze("worker 1 ready", "worker 2 ready", selected=frozenset({1}))
    assert len(result.templates) == 1
    assert result.templates[0].priority is Priority.SELECTED
    assert result.templates[0].count == 2
    assert result.templates[0].example == "worker 1 ready"


def test_ansi_and_zero_width_characters_do_not_split_templates() -> None:
    result = analyze("\x1b[31mERROR boom 5\x1b[0m", "ERROR\u200b boom 6")
    assert len(result.templates) == 1
    assert result.templates[0].count == 2


def test_selected_index_out_of_range_is_ignored() -> None:
    result = analyze("a line", selected=frozenset({-1, 99}))
    assert result.templates[0].priority is Priority.OTHER


# Speed and hostile input.


def _best_time(lines: tuple[str, ...], repeats: int = 1) -> float:
    best = float("inf")
    for _ in range(repeats):
        start = time.perf_counter()
        LogQueryBuilder().analyze(LogInput(lines))
        best = min(best, time.perf_counter() - start)
    return best


def test_the_largest_realistic_request_is_fast() -> None:
    # 2000 lines of 4096 characters, with words, numbers, levels and a few identifiers.
    pool = ("order", "service", "timeout", "java", "retry", "connection", "pool", "user", "id", "failed")
    lines = tuple(
        (f"2026-11-07T10:00:00Z ERROR PROJ-{i % 7} " + " ".join(pool[(i + j) % len(pool)] for j in range(700)))[
            :MAX_LINE_CHARS
        ]
        for i in range(MAX_LINES)
    )
    assert all(len(line) == MAX_LINE_CHARS for line in lines)
    assert _best_time(lines, repeats=2) < 2.0


_FAST_HOSTILE = {
    "dots": "." * MAX_LINE_CHARS,
    "spaces": " " * MAX_LINE_CHARS,
    "dotted_words": "a." * (MAX_LINE_CHARS // 2),
    "slashes": "a/" * (MAX_LINE_CHARS // 2),
    "upper_dots": "A." * (MAX_LINE_CHARS // 2),
    "endpoint": "GET /" + "a/" * 2000,
    "long_float": "1.1" * 1300,
    "python_frame": 'File "' + "a" * (MAX_LINE_CHARS - 10),
    "java_frame": "at " + "a" * (MAX_LINE_CHARS - 10),
    "jira_like": "A1-1 " * 800,
    "hex_numbers": "0x1 " * 1000,
    "timestamps": "2026-11-07T10:00:01" * 200,
    "ips": "1.1.1.1 " * 500,
    "quotes": '"' * MAX_LINE_CHARS,
    "unicode": "é注з " * 1000,
    "exception_chain": "a.b.c.d.e.f.g.h.i.j.k.l.m.n.o.p." * 100 + "Exception",
}

_LONG_WORD_HOSTILE = {
    "one_long_word": "a" * MAX_LINE_CHARS,
    "digits": "0" * MAX_LINE_CHARS,
    "upper": "A" * MAX_LINE_CHARS,
    "dashes": "ab-" * (MAX_LINE_CHARS // 3),
    "hex": "deadbeef" * (MAX_LINE_CHARS // 8),
    "exception_words": "Error" * 800,
}

# One hostile line must take less than 0.1 s. Then a request with the maximum number of lines takes less than 200 s.
_ONE_LINE_LIMIT = 0.1


@pytest.mark.parametrize("name", sorted(_FAST_HOSTILE))
def test_hostile_lines_do_not_make_the_regexes_slow(name: str) -> None:
    assert _best_time((_FAST_HOSTILE[name],), repeats=2) < _ONE_LINE_LIMIT


@pytest.mark.parametrize("name", sorted(_LONG_WORD_HOSTILE))
def test_long_word_lines_do_not_make_the_regexes_slow(name: str) -> None:
    assert _best_time((_LONG_WORD_HOSTILE[name],), repeats=2) < _ONE_LINE_LIMIT


def test_the_output_does_not_depend_on_hostile_line_order_or_repeats() -> None:
    lines = tuple(_FAST_HOSTILE[name] for name in sorted(_FAST_HOSTILE))
    builder = LogQueryBuilder()
    assert builder.analyze(LogInput(lines)) == builder.analyze(LogInput(lines))


# The input section.


def _many_lines() -> tuple[str, ...]:
    return tuple(f"INFO step {'abcdefghij'[i % 10]}{i} finished ok with value {i * 1000 + 7}" for i in range(0, 30))


def test_fit_input_with_a_word_counter_respects_the_cap() -> None:
    result = analyze(*_many_lines(), question="What happened?")
    for cap in (10, 25, 60, 120, 5000):
        fitted = fit_input(result, cap, words)
        assert fitted.tokens == words(fitted.text)
        assert fitted.tokens <= cap
        assert fitted.text.endswith("Question: What happened?")


def test_fit_input_keeps_everything_when_it_fits() -> None:
    result = analyze("ERROR a boom", "INFO b ok", question="Why?")
    fitted = fit_input(result, 1000, words)
    assert fitted.dropped_templates == 0
    assert not fitted.truncated
    assert fitted.text == "Log lines:\nERROR a boom\nINFO b ok\n\nQuestion: Why?"


def test_fit_input_keeps_the_selected_lines_first() -> None:
    lines = [f"INFO routine message {chr(97 + i)} done" for i in range(8)]
    lines[5] = "INFO the selected message about retries"
    lines[2] = "ERROR com.shop.BoomException happened"
    result = analyze(*lines, selected=frozenset({5}))
    assert result.templates[0].first_index == 5
    full = fit_input(result, 1000, words)
    cap = words(full.text) // 2
    fitted = fit_input(result, cap, words)
    assert fitted.dropped_templates > 0
    assert "the selected message about retries" in fitted.text
    assert "routine message h done" not in fitted.text


def test_fit_input_drops_by_rank_and_keeps_the_log_order() -> None:
    lines = ["INFO z first", "WARN pool low", "ERROR boom here", "INFO a last"]
    result = analyze(*lines)
    fitted = fit_input(result, 9, words)
    kept = [line for line in lines if line in fitted.text]
    assert "ERROR boom here" in kept
    assert kept == sorted(kept, key=lines.index)
    assert fitted.dropped_templates == len(lines) - len(kept)


def test_fit_input_shows_the_count_of_a_frequent_template() -> None:
    result = analyze(*(f"ERROR retry {i} failed" for i in range(5)))
    fitted = fit_input(result, 100, words)
    assert "[x5] ERROR retry 0 failed" in fitted.text


def test_fit_input_cuts_one_large_example_with_a_word_counter() -> None:
    result = analyze("ERROR " + " ".join(f"w{i}" for i in range(200)))
    fitted = fit_input(result, 20, words)
    assert fitted.truncated
    assert fitted.dropped_templates == 0
    assert fitted.tokens <= 20
    assert words(fitted.text) >= 15


def test_fit_input_with_only_a_question() -> None:
    result = analyze(question="Why does it fail?")
    fitted = fit_input(result, 10, words)
    assert fitted.text == "Question: Why does it fail?"
    assert fitted.dropped_templates == 0


def test_fit_input_with_no_question_and_no_lines_is_empty() -> None:
    fitted = fit_input(analyze(), 10, words)
    assert fitted.text == ""
    assert fitted.tokens == 0


def test_fit_input_refuses_a_question_over_the_cap() -> None:
    result = analyze("ERROR x", question="one two three four five six")
    with pytest.raises(ContextTooLargeError):
        fit_input(result, 5, words)


def test_fit_input_accepts_a_question_exactly_at_the_cap() -> None:
    result = analyze("ERROR x", question="one two three")
    cap = words("Question: one two three")
    fitted = fit_input(result, cap, words)
    assert fitted.tokens <= cap
    assert fitted.dropped_templates == 1


def test_fit_input_never_returns_more_than_the_cap() -> None:
    result = analyze("ERROR boom happened here")
    fitted = fit_input(result, 1, words)
    assert fitted.tokens <= 1
