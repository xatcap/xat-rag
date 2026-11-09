import math

import pytest
from conftest import make_chunk
from xat_rag_core.errors import InternalError
from xat_rag_core.model import Candidate, StageScores
from xat_rag_core.ranking import MAX_CHUNKS, OmitReason, final_score, merge_ranked


def cand(chunk_id: str, rrf: float | None = None, rerank: float | None = None) -> Candidate:
    return Candidate(make_chunk(chunk_id), StageScores(rrf_score=rrf, rerank_score=rerank))


def ids(candidates: tuple[Candidate, ...]) -> list[str]:
    return [candidate.chunk_id for candidate in candidates]


def test_max_chunks_is_100() -> None:
    assert MAX_CHUNKS == 100


def test_final_score_prefers_the_rerank_score() -> None:
    assert final_score(cand("a", rrf=0.9, rerank=0.1)) == 0.1
    assert final_score(cand("a", rrf=0.9, rerank=0.0)) == 0.0
    assert final_score(cand("a", rrf=0.9)) == 0.9
    assert final_score(cand("a")) == 0.0


def test_candidates_of_all_rags_are_sorted_by_score_best_first() -> None:
    result = merge_ranked([[cand("a", rrf=0.2), cand("b", rrf=0.5)], [cand("c", rrf=0.9), cand("d", rrf=0.1)]], 10)
    assert ids(result.chosen) == ["c", "b", "a", "d"]
    assert result.omitted == ()


def test_a_tie_is_broken_by_chunk_id_whatever_the_input_order() -> None:
    first = merge_ranked([[cand("z", rerank=0.5)], [cand("a", rerank=0.5), cand("m", rerank=0.5)]], 10)
    second = merge_ranked([[cand("m", rerank=0.5), cand("a", rerank=0.5)], [cand("z", rerank=0.5)]], 10)
    assert ids(first.chosen) == ["a", "m", "z"]
    assert ids(second.chosen) == ["a", "m", "z"]


def test_rerank_scores_and_rrf_scores_use_the_same_scale_per_candidate() -> None:
    result = merge_ranked([[cand("a", rerank=-3.0)], [cand("b", rrf=0.03)]], 10)
    assert ids(result.chosen) == ["b", "a"]


def test_a_repeated_chunk_id_is_a_duplicate_and_the_best_copy_stays() -> None:
    result = merge_ranked([[cand("a", rrf=0.1)], [cand("a", rrf=0.8), cand("b", rrf=0.5)]], 10)
    assert ids(result.chosen) == ["a", "b"]
    assert result.chosen[0].scores.rrf_score == 0.8
    assert result.omitted == (("a", OmitReason.DUPLICATE),)


def test_a_duplicate_does_not_use_a_place_of_the_limit() -> None:
    result = merge_ranked([[cand("a", rrf=0.9), cand("a", rrf=0.8), cand("b", rrf=0.7), cand("c", rrf=0.6)]], 2)
    assert ids(result.chosen) == ["a", "b"]
    assert result.omitted == (("a", OmitReason.DUPLICATE), ("c", OmitReason.LIMIT))


def test_the_limit_cuts_the_list_and_names_the_omitted_chunks_in_rank_order() -> None:
    result = merge_ranked([[cand(f"c{n}", rrf=1 - n / 10) for n in range(5)]], 3)
    assert ids(result.chosen) == ["c0", "c1", "c2"]
    assert result.omitted == (("c3", OmitReason.LIMIT), ("c4", OmitReason.LIMIT))


def test_a_limit_above_the_count_keeps_everything() -> None:
    assert len(merge_ranked([[cand("a", rrf=1.0)]], 100).chosen) == 1


def test_nothing_in_gives_nothing_out() -> None:
    result = merge_ranked([], 5)
    assert result.chosen == ()
    assert result.omitted == ()
    assert merge_ranked([[], []], 5).chosen == ()


def test_a_score_that_is_not_a_number_is_an_internal_error() -> None:
    with pytest.raises(InternalError):
        merge_ranked([[cand("a", rerank=math.nan), cand("b", rrf=0.5)]], 5)
    with pytest.raises(InternalError):
        merge_ranked([[cand("a", rrf=math.nan)]], 5)


def test_the_omit_reasons_have_stable_names() -> None:
    assert {reason.value for reason in OmitReason} == {"duplicate", "limit", "low_score"}


def test_a_chunk_in_two_rags_keeps_its_higher_score() -> None:
    result = merge_ranked([[cand("a", rerank=1.0)], [cand("a", rerank=2.0)]], 5)
    assert [final_score(candidate) for candidate in result.chosen] == [2.0]
    assert result.omitted == (("a", OmitReason.DUPLICATE),)


def test_no_min_score_filters_nothing() -> None:
    result = merge_ranked([[cand("a", rerank=-100.0)]], 5)
    assert ids(result.chosen) == ["a"]


def test_a_score_equal_to_min_score_is_kept_and_a_lower_score_is_omitted() -> None:
    result = merge_ranked([[cand("a", rerank=-5.0), cand("b", rerank=-5.01)]], 5, min_score=-5.0)
    assert ids(result.chosen) == ["a"]
    assert result.omitted == (("b", OmitReason.LOW_SCORE),)


def test_all_candidates_below_min_score_give_an_empty_result() -> None:
    result = merge_ranked([[cand("a", rerank=-9.0), cand("b", rerank=-8.0)]], 5, min_score=0.0)
    assert result.chosen == ()
    assert result.omitted == (("b", OmitReason.LOW_SCORE), ("a", OmitReason.LOW_SCORE))


def test_a_low_score_is_the_reason_even_when_the_limit_is_reached() -> None:
    result = merge_ranked([[cand("a", rerank=2.0), cand("b", rerank=1.0), cand("c", rerank=-9.0)]], 1, min_score=0.0)
    assert result.omitted == (("b", OmitReason.LIMIT), ("c", OmitReason.LOW_SCORE))


def test_a_copy_of_a_chosen_chunk_is_a_duplicate_even_below_min_score() -> None:
    result = merge_ranked([[cand("a", rerank=2.0)], [cand("a", rerank=-9.0)]], 5, min_score=0.0)
    assert ids(result.chosen) == ["a"]
    assert result.omitted == (("a", OmitReason.DUPLICATE),)


def test_two_low_copies_of_a_chunk_are_both_low_score() -> None:
    result = merge_ranked([[cand("a", rerank=-1.0)], [cand("a", rerank=-2.0)]], 5, min_score=0.0)
    assert result.omitted == (("a", OmitReason.LOW_SCORE), ("a", OmitReason.LOW_SCORE))


def test_the_filtered_result_is_a_prefix_of_the_unfiltered_result() -> None:
    lists = [[cand("a", rerank=3.0), cand("b", rerank=-1.0)], [cand("c", rerank=1.0), cand("d", rerank=-4.0)]]
    assert ids(merge_ranked(lists, 5).chosen)[:2] == ids(merge_ranked(lists, 5, min_score=0.0).chosen)
