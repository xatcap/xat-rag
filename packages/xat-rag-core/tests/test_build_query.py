import pytest
from xat_rag_core.errors import InvalidRequestError
from xat_rag_core.query import MAX_QUESTION_CHARS, build_query

ZWSP = chr(0x200B)


def test_the_same_question_gives_the_same_query() -> None:
    assert build_query("Why does PROJ-12 fail?") == build_query("Why does PROJ-12 fail?")


def test_terms_keep_code_names_whole_and_are_unique_without_case() -> None:
    query = build_query("See PROJ-12, src/app/main.py and proj-12 again. Call os.path.join.")
    assert query.lexical_terms == ("See", "PROJ-12", "src/app/main.py", "and", "again", "Call", "os.path.join")


def test_edge_punctuation_is_removed_from_terms() -> None:
    assert build_query("(file.py). value: #tag @name -").lexical_terms == ("file.py", "value", "tag", "name")


def test_at_most_32_terms_are_kept_in_order() -> None:
    query = build_query(" ".join(f"term{number}" for number in range(100)))
    assert query.lexical_terms == tuple(f"term{number}" for number in range(32))


def test_a_long_question_is_not_cut_for_the_dense_stage() -> None:
    question = " ".join(["word"] * 3000)  # 14999 characters
    query = build_query(question)
    assert query.dense_text == question
    assert len(query.dense_text) > 2000


def test_the_rerank_text_is_cut_to_600_characters() -> None:
    question = "x" * 599 + " " + "y" * 100
    query = build_query(question)
    assert len(query.rerank_text) == 600
    assert query.rerank_text == query.dense_text[:600]
    assert len(query.dense_text) == 700


def test_a_question_at_the_limit_is_accepted_and_one_more_character_is_refused() -> None:
    assert build_query("a" * MAX_QUESTION_CHARS).dense_text == "a" * MAX_QUESTION_CHARS
    with pytest.raises(InvalidRequestError, match="20000"):
        build_query("a" * (MAX_QUESTION_CHARS + 1))


def test_the_limit_counts_the_raw_question_not_the_cleaned_text() -> None:
    with pytest.raises(InvalidRequestError):
        build_query(ZWSP * (MAX_QUESTION_CHARS + 1))


def test_the_limit_is_20000_characters() -> None:
    assert MAX_QUESTION_CHARS == 20_000


def test_white_space_collapses_and_hidden_characters_are_removed() -> None:
    query = build_query(f"  a{ZWSP}b \n\t c \x1b[31mred\x1b[0m ")
    assert query.dense_text == "ab c red"
    assert query.lexical_terms == ("ab", "c", "red")


def test_an_empty_or_zero_width_question_gives_an_empty_query() -> None:
    for question in ("", "   \n\t", ZWSP * 5):
        query = build_query(question)
        assert query.dense_text == ""
        assert query.rerank_text == ""
        assert query.lexical_terms == ()


def test_the_query_has_no_filters() -> None:
    assert not build_query("question").filters
