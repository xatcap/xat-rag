"""Milestone 5 tests for recipes and model loading errors."""

import json
from pathlib import Path

import pytest
from xat_rag.models import load_models
from xat_rag.recipes import RagRecipe, load_recipes
from xat_rag_core.errors import ConfigError
from xat_rag_core.ranking import MAX_CHUNKS

ROOT = Path(__file__).resolve().parents[3]
MODELS = ROOT / "models"
LOCK = ROOT / "build" / "models.lock.json"


def rag_toml(name: str, *, reranker: str = "none", kind: str = "markdown", extra: str = "") -> str:
    return (
        f'[rag]\nname = "{name}"\nembedder = "bge-small-en-v1.5"\nreranker = "{reranker}"\n'
        f'[sources.main]\nkind = "{kind}"\n{extra}'
    )


def context_toml(name: str, rags: list[str], *, extra: str = "") -> str:
    sources = "".join(f'[[context.sources]]\nrag = "{rag}"\n' for rag in rags)
    return f'[context]\nname = "{name}"\n{extra}\n{sources}'


def write(folder: Path, rags: dict[str, str], contexts: dict[str, str]) -> Path:
    (folder / "rags").mkdir(parents=True, exist_ok=True)
    (folder / "contexts").mkdir(parents=True, exist_ok=True)
    for name, text in rags.items():
        (folder / "rags" / f"{name}.toml").write_text(text)
    for name, text in contexts.items():
        (folder / "contexts" / f"{name}.toml").write_text(text)
    return folder


# Recipes.


def test_an_empty_recipes_folder_gives_no_recipes(tmp_path: Path) -> None:
    assert load_recipes(tmp_path) == ({}, {})


def test_valid_recipes_are_loaded_with_defaults(tmp_path: Path) -> None:
    write(tmp_path, {"docs": rag_toml("docs")}, {"ask": context_toml("ask", ["docs"])})
    rags, contexts = load_recipes(tmp_path)
    assert rags["docs"].retrieval.keep == 8
    assert rags["docs"].sources["main"].max_chunk_tokens == 350
    section = contexts["ask"].context
    assert section.chunks == 10
    assert [source.rag for source in section.sources] == ["docs"]
    assert contexts["ask"].limits.rerank == 2500
    assert contexts["ask"].limits.pack == 300


@pytest.mark.parametrize("chunks", [1, 10, 55, MAX_CHUNKS])
def test_the_default_number_of_chunks_of_a_context_is_read(tmp_path: Path, chunks: int) -> None:
    write(tmp_path, {"docs": rag_toml("docs")}, {"ask": context_toml("ask", ["docs"], extra=f"chunks = {chunks}")})
    assert load_recipes(tmp_path)[1]["ask"].context.chunks == chunks


@pytest.mark.parametrize("chunks", ["0", "-1", "101", "1000", "1.5", '"ten"'])
def test_a_number_of_chunks_outside_1_to_100_is_refused(tmp_path: Path, chunks: str) -> None:
    write(tmp_path, {"docs": rag_toml("docs")}, {"ask": context_toml("ask", ["docs"], extra=f"chunks = {chunks}")})
    with pytest.raises(ConfigError, match=r"context\.chunks"):
        load_recipes(tmp_path)


def test_context_with_an_unknown_rag_is_refused(tmp_path: Path) -> None:
    write(tmp_path, {"docs": rag_toml("docs")}, {"ask": context_toml("ask", ["docs", "ghost", "phantom"])})
    with pytest.raises(ConfigError, match=r"unknown RAGs: ghost, phantom"):
        load_recipes(tmp_path)


def test_context_with_different_rerankers_is_refused(tmp_path: Path) -> None:
    rags = {"a": rag_toml("a", reranker="ms-marco-minilm-l6-v2"), "b": rag_toml("b", reranker="none")}
    write(tmp_path, rags, {"mix": context_toml("mix", ["a", "b"])})
    with pytest.raises(ConfigError, match="same reranker"):
        load_recipes(tmp_path)


def test_context_with_the_same_reranker_is_accepted(tmp_path: Path) -> None:
    rags = {name: rag_toml(name, reranker="ms-marco-minilm-l6-v2") for name in ("a", "b")}
    write(tmp_path, rags, {"mix": context_toml("mix", ["a", "b"])})
    assert set(load_recipes(tmp_path)[1]) == {"mix"}


def _with_rrf_k(text: str, rrf_k: int) -> str:
    return text + f"[retrieval]\nrrf_k = {rrf_k}\n"


def test_context_without_a_reranker_and_with_different_rrf_k_is_refused(tmp_path: Path) -> None:
    rags = {"a": _with_rrf_k(rag_toml("a"), 60), "b": _with_rrf_k(rag_toml("b"), 30)}
    write(tmp_path, rags, {"mix": context_toml("mix", ["a", "b"])})
    with pytest.raises(ConfigError, match=r"context 'mix' have no reranker, so they need one rrf_k"):
        load_recipes(tmp_path)


def test_the_default_rrf_k_counts_as_a_value(tmp_path: Path) -> None:
    rags = {"a": rag_toml("a"), "b": _with_rrf_k(rag_toml("b"), 10)}
    write(tmp_path, rags, {"mix": context_toml("mix", ["a", "b"])})
    with pytest.raises(ConfigError, match="one rrf_k"):
        load_recipes(tmp_path)


def test_context_without_a_reranker_and_with_the_same_rrf_k_is_accepted(tmp_path: Path) -> None:
    rags = {"a": _with_rrf_k(rag_toml("a"), 20), "b": _with_rrf_k(rag_toml("b"), 20), "c": rag_toml("c")}
    write(tmp_path, rags, {"mix": context_toml("mix", ["a", "b"]), "one": context_toml("one", ["c"])})
    assert set(load_recipes(tmp_path)[1]) == {"mix", "one"}


def test_context_with_a_reranker_may_have_different_rrf_k(tmp_path: Path) -> None:
    rags = {
        "a": _with_rrf_k(rag_toml("a", reranker="ms-marco-minilm-l6-v2"), 60),
        "b": _with_rrf_k(rag_toml("b", reranker="ms-marco-minilm-l6-v2"), 30),
    }
    write(tmp_path, rags, {"mix": context_toml("mix", ["a", "b"])})
    assert set(load_recipes(tmp_path)[1]) == {"mix"}


def test_different_rrf_k_in_different_contexts_is_accepted(tmp_path: Path) -> None:
    rags = {"a": _with_rrf_k(rag_toml("a"), 60), "b": _with_rrf_k(rag_toml("b"), 30)}
    write(tmp_path, rags, {"one": context_toml("one", ["a"]), "two": context_toml("two", ["b"])})
    assert set(load_recipes(tmp_path)[1]) == {"one", "two"}


def test_rag_recipe_name_must_match_its_file(tmp_path: Path) -> None:
    write(tmp_path, {"docs": rag_toml("other")}, {})
    with pytest.raises(ConfigError, match=r"'docs.toml' must have the name 'docs'"):
        load_recipes(tmp_path)


def test_context_recipe_name_must_match_its_file(tmp_path: Path) -> None:
    write(tmp_path, {"docs": rag_toml("docs")}, {"ask": context_toml("tell", ["docs"])})
    with pytest.raises(ConfigError, match=r"'ask.toml' must have the name 'ask'"):
        load_recipes(tmp_path)


@pytest.mark.parametrize(
    ("text", "field"),
    [
        (
            '[rag]\nname = "docs"\nembedder = "bge-small-en-v1.5"\nunknown = 1\n[sources.m]\nkind = "text"\n',
            "rag.unknown",
        ),
        (rag_toml("docs", extra="max_chunk_tokens = 31\n"), "sources.main.max_chunk_tokens"),
        (rag_toml("docs", extra="max_chunk_tokens = 2001\n"), "sources.main.max_chunk_tokens"),
        (rag_toml("docs", kind="word"), "sources.main.kind"),
        (rag_toml("docs") + "[retrieval]\nkeep = 0\n", "retrieval.keep"),
        (rag_toml("docs") + "[retrieval]\nmmr_lambda = 1.5\n", "retrieval.mmr_lambda"),
        (rag_toml("docs") + '[retrieval]\nfusion = "linear"\n', "retrieval.fusion"),
        (rag_toml("docs").replace("[rag]\n", "[rag]\nformat = 2\n"), "rag.format"),
        (rag_toml("docs").replace('name = "docs"', 'name = "Docs"'), "rag.name"),
        (rag_toml("docs").replace('name = "docs"', 'name = "-docs"'), "rag.name"),
        (rag_toml("docs") + "[surprise]\nx = 1\n", "surprise"),
        ("[sources.main]\nkind = 'text'\n", "rag"),
    ],
)
def test_bad_rag_recipes_name_the_field(tmp_path: Path, text: str, field: str) -> None:
    write(tmp_path, {"docs": text}, {})
    with pytest.raises(ConfigError, match=field.replace(".", r"\.")):
        load_recipes(tmp_path)


@pytest.mark.parametrize(
    ("extra", "field"),
    [
        ('generator = "ministral-3-3b"', "context.generator"),
        ("window = 8192", "context.window"),
        ("reserved_output = 768", "context.reserved_output"),
        ("input_max_tokens = 1200", "context.input_max_tokens"),
        ('pinned = ["prompt:system@1"]', "context.pinned"),
        ('query_builder = "text-v1"', "context.query_builder"),
        ('order = "random"', "context.order"),
        ('citation = "free"', "context.citation"),
        ("limits = 1", "context.limits"),
        ("surprise = 1", "context.surprise"),
        ("chunks = 0", "context.chunks"),
        ("chunks = 101", "context.chunks"),
    ],
)
def test_bad_context_recipes_name_the_field(tmp_path: Path, extra: str, field: str) -> None:
    write(tmp_path, {"docs": rag_toml("docs")}, {"ask": context_toml("ask", ["docs"], extra=extra)})
    with pytest.raises(ConfigError, match=field.replace(".", r"\.")):
        load_recipes(tmp_path)


@pytest.mark.parametrize("key", ["min_tokens = 0", "max_share = 0.5"])
def test_removed_keys_of_a_context_source_are_refused(tmp_path: Path, key: str) -> None:
    text = context_toml("ask", ["docs"]) + key + "\n"
    write(tmp_path, {"docs": rag_toml("docs")}, {"ask": text})
    with pytest.raises(ConfigError, match=rf"context\.sources\.0\.{key.split(maxsplit=1)[0]}"):
        load_recipes(tmp_path)


def test_a_context_recipe_of_the_old_design_is_refused_and_names_a_removed_field(tmp_path: Path) -> None:
    text = (
        '[context]\nname = "ask"\ngenerator = "ministral-3-3b"\nwindow = 8192\nreserved_output = 768\n'
        'query_builder = "text-v1"\n[[context.sources]]\nrag = "docs"\nmin_tokens = 200\nmax_share = 0.5\n'
    )
    write(tmp_path, {"docs": rag_toml("docs")}, {"ask": text})
    with pytest.raises(ConfigError, match=r"context\.generator"):
        load_recipes(tmp_path)


def test_the_limits_of_a_context_can_be_set(tmp_path: Path) -> None:
    text = context_toml("ask", ["docs"]) + "[limits]\nrerank = 100\npack = 50\n"
    write(tmp_path, {"docs": rag_toml("docs")}, {"ask": text})
    limits = load_recipes(tmp_path)[1]["ask"].limits
    assert (limits.rerank, limits.pack, limits.retrieve) == (100, 50, 500)


def test_damaged_toml_is_a_config_error(tmp_path: Path) -> None:
    write(tmp_path, {"docs": "[rag\nname = "}, {})
    with pytest.raises(ConfigError, match=r"'docs.toml' could not be read"):
        load_recipes(tmp_path)


def test_a_recipe_folder_with_a_directory_in_place_of_a_file_is_a_config_error(tmp_path: Path) -> None:
    (tmp_path / "rags" / "docs.toml").mkdir(parents=True)
    with pytest.raises(ConfigError, match="could not be read"):
        load_recipes(tmp_path)


def test_a_recipe_that_is_not_utf8_is_a_config_error(tmp_path: Path) -> None:
    write(tmp_path, {"docs": rag_toml("docs")}, {})
    (tmp_path / "rags" / "docs.toml").write_bytes(b"\xff\xfe\x00")
    with pytest.raises(ConfigError, match="could not be read"):
        load_recipes(tmp_path)


def test_recipe_errors_do_not_hold_the_values(tmp_path: Path) -> None:
    secret = "SECRET-PROMPT-VALUE"
    write(tmp_path, {"docs": rag_toml("docs", extra=f'max_chunk_tokens = "{secret}"\n')}, {})
    with pytest.raises(ConfigError) as error:
        load_recipes(tmp_path)
    assert secret not in error.value.message


def test_non_toml_files_in_the_recipe_folders_are_ignored(tmp_path: Path) -> None:
    write(tmp_path, {"docs": rag_toml("docs")}, {})
    (tmp_path / "rags" / "notes.txt").write_text("not a recipe")
    (tmp_path / "rags" / "docs.toml.bak").write_text("garbage")
    assert set(load_recipes(tmp_path)[0]) == {"docs"}


def test_index_hash_depends_on_the_embedder_and_the_sources_only(tmp_path: Path) -> None:
    base = rag_toml("docs", extra="max_chunk_tokens = 300\n")
    other_retrieval = base + "[retrieval]\nkeep = 3\nrrf_k = 10\n"
    other_reranker = base.replace('reranker = "none"', 'reranker = "ms-marco-minilm-l6-v2"')
    other_chunk = rag_toml("docs", extra="max_chunk_tokens = 301\n")
    other_kind = rag_toml("docs", kind="text", extra="max_chunk_tokens = 300\n")
    write(
        tmp_path,
        {"docs": base, "r1": other_retrieval.replace("docs", "r1"), "r2": other_reranker.replace("docs", "r2")},
        {},
    )
    write(tmp_path, {"c": other_chunk.replace("docs", "c"), "k": other_kind.replace("docs", "k")}, {})
    rags, _ = load_recipes(tmp_path)
    assert rags["docs"].index_hash() == rags["r1"].index_hash() == rags["r2"].index_hash()
    assert len({rags["docs"].index_hash(), rags["c"].index_hash(), rags["k"].index_hash()}) == 3
    assert rags["docs"].index_hash() == rags["docs"].index_hash()


def test_source_for_an_unknown_kind_is_a_config_error() -> None:
    recipe = RagRecipe.model_validate(
        {"rag": {"name": "docs", "embedder": "e"}, "sources": {"main": {"kind": "markdown"}}}
    )
    assert recipe.source_for("markdown").max_chunk_tokens == 350
    with pytest.raises(ConfigError, match="no source of the kind 'pdf'"):
        recipe.source_for("pdf")


def test_recipes_are_frozen() -> None:
    recipe = RagRecipe.model_validate(
        {"rag": {"name": "docs", "embedder": "e"}, "sources": {"main": {"kind": "markdown"}}}
    )
    with pytest.raises(ValueError, match="frozen"):
        recipe.retrieval.keep = 3  # type: ignore[misc]


# Model loading errors. These checks run before any model file is read.


def test_unknown_embedder_and_reranker_are_refused(tmp_path: Path) -> None:
    cache = tmp_path / "cache.json"
    with pytest.raises(ConfigError, match="embedder 'nope' is not supported"):
        load_models(MODELS, LOCK, cache, embedders={"nope"}, rerankers=set())
    with pytest.raises(ConfigError, match="reranker 'nope' is not supported"):
        load_models(MODELS, LOCK, cache, embedders=set(), rerankers={"nope"})


def test_load_models_has_no_generator_argument(tmp_path: Path) -> None:
    with pytest.raises(TypeError):
        load_models(MODELS, LOCK, tmp_path / "c.json", embedders=set(), rerankers=set(), generators=set())  # type: ignore[call-arg]


def test_a_missing_lock_file_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="missing or damaged"):
        load_models(MODELS, tmp_path / "none.json", tmp_path / "c.json", embedders=set(), rerankers=set())


def test_a_model_that_is_not_in_the_lock_is_refused(tmp_path: Path) -> None:
    lock = tmp_path / "lock.json"
    lock.write_text('{"models": []}')
    with pytest.raises(ConfigError, match="not in the model lock"):
        load_models(MODELS, lock, tmp_path / "c.json", embedders={"bge-small-en-v1.5"}, rerankers=set())


def test_the_size_tokenizer_is_always_needed_in_the_lock(tmp_path: Path) -> None:
    lock = tmp_path / "lock.json"
    lock.write_text('{"models": []}')
    with pytest.raises(ConfigError, match="'ministral-3-3b-tokenizer' is not in the model lock"):
        load_models(MODELS, lock, tmp_path / "c.json", embedders=set(), rerankers=set())


needs_models = pytest.mark.skipif(
    not (MODELS / "ministral-3-3b-tokenizer" / "tokenizer.json").exists(), reason="no models"
)


@needs_models
def test_none_reranker_needs_no_model_and_the_size_counter_is_always_loaded(tmp_path: Path) -> None:
    models = load_models(MODELS, LOCK, tmp_path / "c.json", embedders=set(), rerankers={"none"})
    assert dict(models.rerankers) == {}
    assert dict(models.embedders) == {}
    assert models.size_counter.count("Hello world.") == 3


@needs_models
def test_the_size_tokenizer_id_comes_from_the_lock(tmp_path: Path) -> None:
    models = load_models(MODELS, LOCK, tmp_path / "c.json", embedders=set(), rerankers=set())
    entry = next(item for item in json.loads(LOCK.read_text())["models"] if item["id"] == "ministral-3-3b-tokenizer")
    sha = next(file["sha256"] for file in entry["files"] if file["path"] == "tokenizer.json")
    assert models.size_counter.tokenizer_id == f"ministral-3-3b-tokenizer@{sha[:12]}"
