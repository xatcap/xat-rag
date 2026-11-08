"""Edge cases of the citation grammar and of `check_answer`.

The grammar test uses a small GBNF reader that supports only what xat-rag writes: rules, quoted strings,
character classes, groups, alternatives and the `*`, `+` and `?` marks. It turns the grammar into a regular
expression. The grammar has no recursion, so this is exact.
"""

import json
import re

import pytest
from xat_rag_core.citations import CitationProblem, check_answer, citation_grammar, citation_json_schema
from xat_rag_core.errors import InternalError

type _Token = tuple[str, str]

_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", '"': '"'}


def _tokenize(text: str) -> list[_Token]:
    tokens: list[_Token] = []
    position = 0
    while position < len(text):
        char = text[position]
        if char in " \t":
            position += 1
        elif text.startswith("::=", position):
            tokens.append(("assign", "::="))
            position += 3
        elif char in "|()*+?":
            tokens.append(("punct", char))
            position += 1
        elif char == '"':
            value: list[str] = []
            position += 1
            while text[position] != '"':
                if text[position] == "\\":
                    position += 1
                    if text[position] == "x":
                        value.append(chr(int(text[position + 1 : position + 3], 16)))
                        position += 2
                    else:
                        value.append(_ESCAPES[text[position]])
                else:
                    value.append(text[position])
                position += 1
            position += 1
            tokens.append(("string", "".join(value)))
        elif char == "[":
            end = position + 1
            while text[end] != "]":
                end += 2 if text[end] == "\\" else 1
            tokens.append(("class", text[position : end + 1]))
            position = end + 1
        else:
            match = re.compile(r"[A-Za-z][A-Za-z0-9-]*").match(text, position)
            assert match is not None, f"Unexpected character {char!r} in the grammar."
            tokens.append(("name", match.group()))
            position = match.end()
    return tokens


class _Translator:
    def __init__(self, rules: dict[str, list[_Token]]) -> None:
        self._rules = rules
        self._tokens: list[_Token] = []
        self._position = 0
        self._active: list[str] = []

    def rule_regex(self, name: str) -> str:
        assert name in self._rules, f"The rule {name} is not defined."
        assert name not in self._active, f"The rule {name} is recursive."
        saved = (self._tokens, self._position)
        self._active.append(name)
        self._tokens, self._position = self._rules[name], 0
        result = self._alternatives()
        assert self._position == len(self._tokens), f"The rule {name} has extra tokens."
        self._active.pop()
        self._tokens, self._position = saved
        return result

    def _peek(self) -> _Token | None:
        return self._tokens[self._position] if self._position < len(self._tokens) else None

    def _alternatives(self) -> str:
        parts = [self._sequence()]
        while self._peek() == ("punct", "|"):
            self._position += 1
            parts.append(self._sequence())
        return "(?:" + "|".join(parts) + ")"

    def _sequence(self) -> str:
        parts: list[str] = []
        while (token := self._peek()) is not None and token not in {("punct", "|"), ("punct", ")")}:
            parts.append(self._term())
        assert parts, "An empty sequence."
        return "".join(parts)

    def _term(self) -> str:
        token = self._peek()
        assert token is not None
        self._position += 1
        kind, value = token
        if kind == "string":
            atom = re.escape(value)
        elif kind == "class":
            atom = value
        elif kind == "name":
            atom = self.rule_regex(value)
        else:
            assert token == ("punct", "("), f"Unexpected token {token}."
            atom = self._alternatives()
            assert self._peek() == ("punct", ")"), "A group is not closed."
            self._position += 1
        suffix = self._peek()
        if suffix is not None and suffix[0] == "punct" and suffix[1] in "*+?":
            self._position += 1
            return f"(?:{atom}){suffix[1]}"
        return atom


def _grammar_regex(grammar: str) -> re.Pattern[str]:
    rules: dict[str, list[_Token]] = {}
    for line in grammar.splitlines():
        assert line.strip(), "The grammar has an empty line."
        tokens = _tokenize(line)
        assert tokens[0][0] == "name"
        assert tokens[1] == ("assign", "::="), f"Bad rule line: {line}"
        assert tokens[0][1] not in rules, f"The rule {tokens[0][1]} is defined twice."
        rules[tokens[0][1]] = tokens[2:]
    assert "root" in rules
    return re.compile(_Translator(rules).rule_regex("root"), re.DOTALL)


def _labels(count: int) -> list[str]:
    return [f"C{position}" for position in range(1, count + 1)]


def test_grammar_is_syntactically_valid_and_every_rule_is_used() -> None:
    grammar = citation_grammar(_labels(3))
    defined = {line.split(" ::= ")[0] for line in grammar.splitlines()}
    assert defined == {"root", "citations", "label", "string", "hex", "ws"}
    assert grammar.endswith("\n")
    _grammar_regex(grammar)


def test_grammar_without_labels_is_valid_and_defines_no_label_rule() -> None:
    grammar = citation_grammar([])
    assert "label ::=" not in grammar
    _grammar_regex(grammar)


@pytest.mark.parametrize("count", [0, 1, 9, 10, 25])
def test_grammar_accepts_json_dumps_output_for_known_labels(count: int) -> None:
    pattern = _grammar_regex(citation_grammar(_labels(count)))
    cited = _labels(count)[::2]
    for answer in ["", "plain", 'quote " and \\ backslash', "line\nbreak\ttab", "unicode é 日本 \U0001f600"]:
        for text in (
            json.dumps({"answer": answer, "citations": cited}),
            json.dumps({"answer": answer, "citations": cited}, indent=2),
        ):
            assert pattern.fullmatch(text), text
            assert json.loads(text)["citations"] == cited


def test_grammar_accepts_compact_json() -> None:
    pattern = _grammar_regex(citation_grammar(_labels(2)))
    assert pattern.fullmatch('{"answer":"x","citations":["C1","C2"]}')
    assert pattern.fullmatch('{"answer":"x","citations":[]}')


@pytest.mark.parametrize(
    "text",
    [
        '{"answer": "x", "citations": ["C3"]}',  # Unknown label.
        '{"answer": "x", "citations": ["C"]}',
        '{"answer": "x", "citations": ["c1"]}',
        '{"answer": "x", "citations": [C1]}',  # Label without quotes.
        '{"answer": "x", "citations": ["C1",]}',  # Trailing comma.
        '{"answer": "x", "citations": [,"C1"]}',
        '{"citations": ["C1"], "answer": "x"}',  # Wrong order.
        '{"answer": "x"}',
        '{"answer": 1, "citations": []}',
        '{"answer": "x", "citations": [], "extra": 1}',
        '{"answer": "raw\nnewline", "citations": []}',  # A raw control character in a string.
        '{"answer": "bad \\q escape", "citations": []}',
        '{"answer": "bad \\u12G4", "citations": []}',
        '{"answer": "x", "citations": []} trailing',
        '{"answer": "unterminated',
        "",
    ],
)
def test_grammar_refuses_bad_answers(text: str) -> None:
    pattern = _grammar_regex(citation_grammar(_labels(2)))
    assert not pattern.fullmatch(text)


def test_grammar_label_with_two_digits_does_not_match_a_prefix() -> None:
    pattern = _grammar_regex(citation_grammar(_labels(10)))
    assert pattern.fullmatch('{"answer": "", "citations": ["C10"]}')
    assert not pattern.fullmatch('{"answer": "", "citations": ["C11"]}')
    assert not pattern.fullmatch('{"answer": "", "citations": ["C1", "C"]}')


@pytest.mark.parametrize("labels", [["C2"], ["C1", "C3"], ["C1", "C1"], ["c1"], ["C1 "], ["C0"]])
def test_grammar_refuses_labels_that_are_not_in_order(labels: list[str]) -> None:
    with pytest.raises(InternalError):
        citation_grammar(labels)


def test_json_schema_without_labels_allows_no_citation() -> None:
    schema = citation_json_schema([])
    assert schema["properties"]["citations"] == {"type": "array", "maxItems": 0}
    assert schema["required"] == ["answer", "citations"]
    assert schema["additionalProperties"] is False


def test_json_schema_uses_only_portable_keywords() -> None:
    # llama.cpp and OpenAI strict mode do not support "uniqueItems" or "not". check_answer removes repeats.
    citations = citation_json_schema(["C1", "C2"])["properties"]["citations"]
    assert citations == {"type": "array", "items": {"type": "string", "enum": ["C1", "C2"]}}


LABELS = {"C1": "docs:a#1", "C2": "docs:b#2"}


@pytest.mark.parametrize(
    "citations",
    [[1], [None], [["C1"]], [{"id": "C1"}], [True], ["C1", 2], [1.5]],
)
def test_non_string_citations_are_a_bad_shape(citations: list[object]) -> None:
    result = check_answer(json.dumps({"answer": "x", "citations": citations}), LABELS)
    assert not result.valid
    assert result.problem is CitationProblem.BAD_SHAPE
    assert result.citations == ()


@pytest.mark.parametrize(
    "body",
    [
        {"answer": 1, "citations": []},
        {"answer": None, "citations": []},
        {"answer": "x", "citations": "C1"},
        {"answer": "x", "citations": None},
        {"answer": "x", "citations": {"C1": 1}},
        {"answer": "x"},
        {"citations": []},
        {},
    ],
)
def test_wrong_fields_are_a_bad_shape(body: dict[str, object]) -> None:
    assert check_answer(json.dumps(body), LABELS).problem is CitationProblem.BAD_SHAPE


@pytest.mark.parametrize("text", ["null", "1", '"C1"', "true", "[]", "NaN"])
def test_json_that_is_not_an_object_is_a_bad_shape(text: str) -> None:
    assert check_answer(text, LABELS).problem is CitationProblem.BAD_SHAPE


@pytest.mark.parametrize("text", ["", "   ", "{", '{"answer": "x", "citations": [', "not json", "{'answer': 'x'}"])
def test_text_that_is_not_json_is_reported(text: str) -> None:
    assert check_answer(text, LABELS).problem is CitationProblem.NOT_JSON


def test_empty_citation_list_is_valid() -> None:
    result = check_answer('{"answer": "no source", "citations": []}', LABELS)
    assert result.valid
    assert result.answer == "no source"
    assert result.citations == ()


def test_extra_keys_are_ignored() -> None:
    assert check_answer('{"answer": "x", "citations": ["C1"], "note": 1}', LABELS).valid


def test_known_and_unknown_labels_together_report_the_unknown_ones_and_keep_the_known_ones() -> None:
    result = check_answer('{"answer": "x", "citations": ["C1", "C9", "C9", "C8"]}', LABELS)
    assert result.problem is CitationProblem.UNKNOWN_LABEL
    assert result.unknown_labels == ("C9", "C8")
    assert [citation.label for citation in result.citations] == ["C1"]


def test_unknown_label_wins_over_a_missing_chunk_and_chunk_exists_is_not_called() -> None:
    calls: list[str] = []

    def exists(chunk_id: str) -> bool:
        calls.append(chunk_id)
        return False

    result = check_answer('{"answer": "x", "citations": ["C1", "C9"]}', LABELS, chunk_exists=exists)
    assert result.problem is CitationProblem.UNKNOWN_LABEL
    assert calls == []


def test_only_missing_chunks_are_reported_and_duplicates_are_checked_once() -> None:
    calls: list[str] = []

    def exists(chunk_id: str) -> bool:
        calls.append(chunk_id)
        return chunk_id == "docs:a#1"

    result = check_answer('{"answer": "x", "citations": ["C1", "C2", "C2"]}', LABELS, chunk_exists=exists)
    assert result.problem is CitationProblem.MISSING_CHUNK
    assert result.missing_chunks == ("docs:b#2",)
    assert calls == ["docs:a#1", "docs:b#2"]


def test_label_lookup_is_case_sensitive() -> None:
    assert check_answer('{"answer": "x", "citations": ["c1"]}', LABELS).problem is CitationProblem.UNKNOWN_LABEL


def test_empty_label_map_makes_every_label_unknown() -> None:
    assert check_answer('{"answer": "x", "citations": ["C1"]}', {}).problem is CitationProblem.UNKNOWN_LABEL


def test_deeply_nested_json_is_reported_and_does_not_raise() -> None:
    assert check_answer("[" * 100_000, LABELS).problem in {CitationProblem.NOT_JSON, CitationProblem.BAD_SHAPE}
