"""Tests of the fixture set and of a small run with the real models. They do not depend on the number of cases."""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import get_args

import pytest
from xat_rag import ContextRequest, Settings, XatRag
from xat_rag.recipes import SourceKindName, load_recipes
from xat_rag_eval.corpus import load_corpus
from xat_rag_eval.harness import Case, Relevant, gate, load_golden, report_json, run

ROOT = Path(__file__).resolve().parents[3]
MODELS = ROOT / "models"
LOCK = ROOT / "build" / "models.lock.json"
SETUP = ROOT / "eval" / "fixtures" / "setup"
GOLDEN = ROOT / "eval" / "fixtures" / "golden.jsonl"
BASELINE = ROOT / "eval" / "baseline.json"
needs_models = pytest.mark.skipif(not (MODELS / "bge-small-en-v1.5" / "model.onnx").exists(), reason="no models")
RAG_OF_KIND = {
    "markdown": "docs",
    "text": "text",
    "diff": "diffs",
    "jira": "jira",
    "prompt": "prompts",
    "pdf": "pdfs",
    "docx": "word",
    "html": "web",
    "xlsx": "sheets",
}


def test_the_golden_set_is_valid_and_uses_the_contexts_and_rags_of_the_fixture_recipes() -> None:
    rags, contexts = load_recipes(SETUP / "recipes")
    cases = load_golden(GOLDEN)
    assert cases
    for case in cases:
        assert case.question.strip(), case.id
        assert case.context in contexts, case.id
        assert case.rag in {source.rag for source in contexts[case.context].context.sources}, case.id
        assert case.rag in rags, case.id


def test_the_golden_set_has_no_log_lines() -> None:
    for line in GOLDEN.read_text("utf-8").splitlines():
        if line.strip():
            assert "log_lines" not in json.loads(line)


def test_the_fixture_recipes_use_the_retrieval_only_keys() -> None:
    _, contexts = load_recipes(SETUP / "recipes")
    assert contexts
    assert not (SETUP / "prompts").exists()
    for recipe in contexts.values():
        assert 1 <= recipe.context.chunks <= 100


def test_each_source_kind_has_a_default_rag_and_a_context_that_searches_only_that_rag() -> None:
    rags, contexts = load_recipes(SETUP / "recipes")
    assert set(get_args(SourceKindName)) == set(RAG_OF_KIND)
    for kind, name in RAG_OF_KIND.items():
        assert [source.kind for source in rags[name].sources.values()] == [kind]
        assert [source.rag for source in contexts[f"ask-{name}"].context.sources] == [name]


def test_the_all_rag_has_each_source_kind_and_ask_all_searches_only_it() -> None:
    rags, contexts = load_recipes(SETUP / "recipes")
    assert {source.kind for source in rags["all"].sources.values()} == set(get_args(SourceKindName))
    assert [source.rag for source in contexts["ask-all"].context.sources] == ["all"]


def test_the_all_rag_splits_each_kind_as_its_single_kind_rag_does() -> None:
    rags, _ = load_recipes(SETUP / "recipes")
    for kind, name in RAG_OF_KIND.items():
        assert rags["all"].source_for(kind) == rags[name].source_for(kind), kind


def test_every_kind_of_the_golden_set_is_in_the_baseline_with_the_final_metrics() -> None:
    baseline = json.loads(BASELINE.read_text("utf-8"))["kinds"]
    assert {case.kind for case in load_golden(GOLDEN)} == set(baseline)
    for kind, entry in baseline.items():
        assert set(entry["final_recall"]) == {"3", "5", "10"}, kind
        assert "final_mrr" in entry, kind
        assert "source_coverage" in entry, kind
        assert "pack_recall" not in entry, kind


@pytest.fixture(scope="module")
def rag(tmp_path_factory: pytest.TempPathFactory) -> Iterator[XatRag]:
    folder = tmp_path_factory.mktemp("eval")
    library = XatRag.open(Settings(folder / "data", SETUP / "recipes", MODELS, LOCK, threads=1))
    results = library.ingest(load_corpus(SETUP / "documents"))
    assert not [result for result in results if result.status == "failed"]
    yield library
    library.close()


@needs_models
def test_a_run_on_the_fixture_corpus_has_no_violations_and_passes_its_own_baseline(rag: XatRag) -> None:
    cases = load_golden(GOLDEN)
    report = run(rag, cases)
    assert report.invariant_violations == []
    assert report.nondeterministic == []
    assert {case.kind for case in cases} == set(report.kinds)
    for item in report.kinds.values():
        assert set(item.final_recall) == {"3", "5", "10"}
        assert all(0.0 <= value <= 1.0 for value in item.final_recall.values())
        assert 0.0 <= item.final_mrr <= 1.0
        assert 0.0 <= item.source_coverage <= 1.0
        assert item.final_recall["3"] <= item.final_recall["5"] <= item.final_recall["10"]
    assert gate(report, {"kinds": report_json(report)["kinds"]}) == []


@needs_models
def test_a_case_for_a_document_that_is_in_the_corpus_is_found_in_the_final_list(rag: XatRag) -> None:
    recipe_context = load_recipes(SETUP / "recipes")[1]["ask-docs"]
    rag_name = recipe_context.context.sources[0].rag
    document = next(document for document in load_corpus(SETUP / "documents") if document.rag == rag_name)
    text = document.data.decode("utf-8", "replace")
    question = " ".join(text.split()[:12])
    case = Case(
        id="self",
        context=recipe_context.context.name,
        rag=rag_name,
        kind="self",
        question=question,
        relevant=[Relevant(doc_id=document.doc_id)],
    )
    report = run(rag, [case])
    item = report.kinds["self"]
    assert item.final_recall["10"] == 1.0
    assert item.source_coverage == 1.0
    assert item.final_mrr > 0.0


UNRELATED_QUESTIONS = (
    "What is the capital of Australia?",
    "How do I bake sourdough bread at home?",
    "Who won the football world cup in 1998?",
    "What is the boiling point of water on Mars?",
    "Best hiking boots for winter",
)


@needs_models
@pytest.mark.parametrize("context", ["ask-docs", "triage"])
@pytest.mark.parametrize("question", UNRELATED_QUESTIONS)
def test_an_unrelated_question_gives_no_chunks(rag: XatRag, context: str, question: str) -> None:
    result = rag.retrieve(ContextRequest(context, question))
    assert result.chunks == ()
    assert any(item.reason == "low_score" for item in result.omitted)


@needs_models
def test_each_golden_case_keeps_chunks_at_or_above_the_cut_off(rag: XatRag) -> None:
    _, contexts = load_recipes(SETUP / "recipes")
    for case in load_golden(GOLDEN):
        min_score = contexts[case.context].context.min_score
        assert min_score is not None, case.context
        result = rag.retrieve(ContextRequest(case.context, case.question))
        assert result.chunks or case.answers(), case.id  # An answer-text case measures what the cut-off keeps.
        assert all(chunk.score >= min_score for chunk in result.chunks), case.id
