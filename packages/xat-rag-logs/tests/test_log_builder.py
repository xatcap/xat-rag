import time

from xat_rag_core.errors import ContextTooLargeError
from xat_rag_core.query import ExactLookup, TextQueryBuilder
from xat_rag_logs import LogInput, LogQueryBuilder, fit_input
from xat_rag_logs.builder import Priority, mask

LINES = (
    "2026-11-07T10:00:01.123Z INFO  [http] GET /api/orders/42 200 in 12 ms",
    "2026-11-07T10:00:02.456Z INFO  [http] GET /api/orders/43 200 in 15 ms",
    "2026-11-07T10:00:03.001Z ERROR [orders] com.shop.OrderNotFoundException: order 9f1c not found (PROJ-123)",
    "    at com.shop.OrderService.find(OrderService.java:88)",
    '  File "/srv/app/billing/client.py", line 41, in charge',
    "2026-11-07T10:00:04.000Z WARN  [pool] retry 3 for 10.0.0.12:5432 req 550e8400-e29b-41d4-a716-446655440000",
    "\x1b[31m2026-11-07T10:00:05.000Z ERROR fix in 4f2a9c1 broke E11000\x1b[0m",
)


def test_mask_removes_variable_parts() -> None:
    assert mask(
        "2026-11-07T10:00:01.123Z GET /x/42 from 10.0.0.1:80 0xdeadbeef id 550e8400-e29b-41d4-a716-446655440000"
    ) == ("<TS> GET /x/<NUM> from <IP> <HEX> id <UUID>")


def test_equal_masked_lines_become_one_template_with_a_count() -> None:
    result = LogQueryBuilder().analyze(LogInput(LINES))
    http = [t for t in result.templates if "[http]" in t.template]
    assert len(http) == 1
    assert http[0].count == 2
    assert http[0].example == LINES[0]


def test_identifiers_and_lookups() -> None:
    result = LogQueryBuilder().analyze(LogInput(LINES, question="Why do orders fail?"))
    terms = result.query.lexical_terms
    assert "com.shop.OrderNotFoundException" in terms
    assert "PROJ-123" in terms
    assert "OrderService.java" in terms
    assert "client.py" in terms
    assert "4f2a9c1" in terms
    assert "E11000" in terms
    assert "/api/orders/42" in terms
    assert ExactLookup("jira_key", "PROJ-123") in result.query.exact_lookups
    assert ExactLookup("path_name", "orderservice.java") in result.query.exact_lookups
    assert result.query.dense_text.startswith("Why do orders fail?")


def test_rank_order_selected_then_errors_then_warnings() -> None:
    result = LogQueryBuilder().analyze(LogInput(LINES, selected=frozenset({0})))
    priorities = [t.priority for t in result.templates]
    assert priorities[0] is Priority.SELECTED
    assert priorities == sorted(priorities)
    assert Priority.WARNING in priorities


def test_the_builder_is_deterministic() -> None:
    builder = LogQueryBuilder()
    assert builder.analyze(LogInput(LINES)) == builder.analyze(LogInput(LINES))


def test_fit_input_drops_the_least_important_templates() -> None:
    result = LogQueryBuilder().analyze(LogInput(LINES, question="Why?"))
    full = fit_input(result, 10_000, count=len)
    assert full.dropped_templates == 0
    assert "[x2] " in full.text
    small = fit_input(result, 200, count=len)
    assert small.tokens <= 200
    assert small.dropped_templates > 0
    assert "OrderNotFoundException" in small.text or small.truncated
    assert small.text.endswith("Question: Why?")


def test_fit_input_cuts_one_large_template() -> None:
    result = LogQueryBuilder().analyze(LogInput(("ERROR " + "x" * 500,)))
    fitted = fit_input(result, 100, count=len)
    assert fitted.truncated
    assert fitted.tokens <= 100


def test_question_larger_than_the_cap_is_refused() -> None:
    result = LogQueryBuilder().analyze(LogInput((), question="q" * 50))
    try:
        fit_input(result, 10, count=len)
    except ContextTooLargeError:
        return
    raise AssertionError("expected ContextTooLargeError")


def test_text_query_builder() -> None:
    query = TextQueryBuilder().build("  How  to deploy  on linux? Deploy com.shop.Order failed.")
    assert query.lexical_terms == ("How", "to", "deploy", "on", "linux", "com.shop.Order", "failed")
    assert query.dense_text == "How to deploy on linux? Deploy com.shop.Order failed."


def test_not_jira_prefixes_and_unicode_question() -> None:
    result = LogQueryBuilder().analyze(LogInput(("ERROR bad UTF-8 in SHA-256 for PROJ-9",), question="Почему ошибка?"))
    keys = [lookup.value for lookup in result.query.exact_lookups if lookup.field == "jira_key"]
    assert keys == ["PROJ-9"]
    assert "Почему" in result.query.lexical_terms
    assert len(result.identifiers) == len(set(result.identifiers))


def test_fit_input_never_exceeds_the_cap() -> None:
    result = LogQueryBuilder().analyze(LogInput(("one log line",), question="a b c"))
    fitted = fit_input(result, 4, count=lambda text: len(text.split()))
    assert fitted.tokens <= 4
    assert fitted.dropped_templates == 1


def test_fit_input_counts_few_times_for_many_lines() -> None:
    calls = 0

    def counting(text: str) -> int:
        nonlocal calls
        calls += 1
        return len(text)

    lines = tuple(f"INFO event {chr(65 + i % 26)}{i % 7} worker{i}" for i in range(2000))
    fit_input(LogQueryBuilder().analyze(LogInput(lines)), 5000, count=counting)
    assert calls < 60


def test_worst_case_lines_are_fast() -> None:
    lines = ("a" * 4096, "a-" * 2048, "a$" * 2048, "a." * 2048, ("x/" * 100 + "a.py:1 ") * 10) * 40
    start = time.perf_counter()
    LogQueryBuilder().analyze(LogInput(lines))
    assert time.perf_counter() - start < 2.0
