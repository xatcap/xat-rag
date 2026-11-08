"""Milestone 5 tests for recipes, pinned prompts and model loading errors. These tests need no model files."""

from pathlib import Path

import pytest
from xat_rag.models import load_models
from xat_rag.prompts import MAX_PROMPT_BYTES, PromptStore
from xat_rag.recipes import RagRecipe, load_recipes
from xat_rag_core.errors import ConfigError, InvalidRequestError, NotFoundError

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
    return f'[context]\nname = "{name}"\ngenerator = "ministral-3-3b"\n{extra}\n{sources}'


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
    assert (section.window, section.reserved_output, section.input_max_tokens) == (8192, 768, 1200)
    assert section.query_builder == "text-v1"
    assert contexts["ask"].limits.rerank == 2500


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
        ("window = 255", "context.window"),
        ("reserved_output = 0", "context.reserved_output"),
        ("input_max_tokens = 15", "context.input_max_tokens"),
        ('query_builder = "log-v2"', "context.query_builder"),
        ('order = "random"', "context.order"),
        ('citation = "free"', "context.citation"),
        ("limits = 1", "context.limits"),
    ],
)
def test_bad_context_recipes_name_the_field(tmp_path: Path, extra: str, field: str) -> None:
    write(tmp_path, {"docs": rag_toml("docs")}, {"ask": context_toml("ask", ["docs"], extra=extra)})
    with pytest.raises(ConfigError, match=field.replace(".", r"\.")):
        load_recipes(tmp_path)


def test_bad_quota_values_are_refused(tmp_path: Path) -> None:
    for quota in ("max_share = 0.0", "max_share = 1.01", "min_tokens = -1"):
        text = context_toml("ask", ["docs"]) + quota + "\n"
        write(tmp_path, {"docs": rag_toml("docs")}, {"ask": text})
        with pytest.raises(ConfigError, match=r"context\.sources"):
            load_recipes(tmp_path)


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


# Pinned prompts.


def test_prompt_store_without_a_folder_has_no_prompts() -> None:
    store = PromptStore(None)
    assert store.ids() == []
    with pytest.raises(NotFoundError, match="prompt:x@1"):
        store.get("prompt:x@1")


def test_prompt_store_with_a_missing_folder_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="does not exist"):
        PromptStore(tmp_path / "nowhere")
    (tmp_path / "file").write_text("x")
    with pytest.raises(ConfigError, match="does not exist"):
        PromptStore(tmp_path / "file")


def test_prompt_store_reads_only_files_with_a_valid_name(tmp_path: Path) -> None:
    for name in ("good@1.md", "plain@2.txt", "x1-y@10.md"):
        (tmp_path / name).write_text(f"text of {name}\n\n")
    ignored = (
        "Upper@1.md",
        "noversion.md",
        "bad@x.md",
        "bad@1.json",
        "@1.md",
        "-lead@1.md",
        "big@1234567.md",
        ".hidden",
    )
    for name in ignored:
        (tmp_path / name).write_text("ignored")
    store = PromptStore(tmp_path)
    assert store.ids() == ["prompt:good@1", "prompt:plain@2", "prompt:x1-y@10"]
    prompt = store.get("prompt:good@1")
    assert (prompt.name, prompt.version, prompt.prompt_id) == ("good", 1, "prompt:good@1")
    assert prompt.text == "text of good@1.md"


def test_prompt_text_is_sanitized(tmp_path: Path) -> None:
    (tmp_path / "p@1.md").write_text("Be\u200b brief.\x1b[31m Red\x1b[0m\x00 end\r\nnext", encoding="utf-8")
    text = PromptStore(tmp_path).get("prompt:p@1").text
    assert text == "Be brief. Red end\nnext"


def test_prompt_size_limit_is_exact(tmp_path: Path) -> None:
    (tmp_path / "ok@1.md").write_text("a" * MAX_PROMPT_BYTES)
    assert len(PromptStore(tmp_path).get("prompt:ok@1").text) == MAX_PROMPT_BYTES
    (tmp_path / "big@1.md").write_text("a" * (MAX_PROMPT_BYTES + 1))
    with pytest.raises(ConfigError, match="larger than 256 KiB"):
        PromptStore(tmp_path)


def test_prompt_that_is_not_utf8_is_a_config_error(tmp_path: Path) -> None:
    (tmp_path / "bad@1.md").write_bytes(b"\xff\xfe\xfa")
    with pytest.raises(ConfigError, match="not valid UTF-8"):
        PromptStore(tmp_path)


def test_prompt_name_that_is_a_folder_is_a_config_error(tmp_path: Path) -> None:
    (tmp_path / "dir@1.md").mkdir()
    with pytest.raises(ConfigError, match="not valid UTF-8"):
        PromptStore(tmp_path)


def test_prompt_with_leading_zeros_has_the_plain_version(tmp_path: Path) -> None:
    (tmp_path / "p@03.md").write_text("three")
    assert PromptStore(tmp_path).ids() == ["prompt:p@3"]


def test_two_files_with_the_same_prompt_id_are_refused(tmp_path: Path) -> None:
    (tmp_path / "p@3.md").write_text("first")
    (tmp_path / "p@03.txt").write_text("second")
    with pytest.raises(ConfigError):
        PromptStore(tmp_path)


def test_empty_prompt_file_gives_an_empty_prompt(tmp_path: Path) -> None:
    (tmp_path / "p@1.md").write_text("   \n\n")
    assert PromptStore(tmp_path).get("prompt:p@1").text == ""


# Model loading errors. These checks run before any model file is read.


def test_unknown_embedder_and_reranker_and_generator_are_refused(tmp_path: Path) -> None:
    cache = tmp_path / "cache.json"
    with pytest.raises(ConfigError, match="embedder 'nope' is not supported"):
        load_models(MODELS, LOCK, cache, embedders={"nope"}, rerankers=set(), generators=set())
    with pytest.raises(ConfigError, match="reranker 'nope' is not supported"):
        load_models(MODELS, LOCK, cache, embedders=set(), rerankers={"nope"}, generators=set())
    with pytest.raises(InvalidRequestError, match="no profile"):
        load_models(MODELS, LOCK, cache, embedders=set(), rerankers=set(), generators={"gpt-x"})


def test_a_missing_lock_file_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="missing or damaged"):
        load_models(
            MODELS, tmp_path / "none.json", tmp_path / "c.json", embedders=set(), rerankers=set(), generators=set()
        )


def test_a_model_that_is_not_in_the_lock_is_refused(tmp_path: Path) -> None:
    lock = tmp_path / "lock.json"
    lock.write_text('{"models": []}')
    with pytest.raises(ConfigError, match="not in the model lock"):
        load_models(
            MODELS, lock, tmp_path / "c.json", embedders={"bge-small-en-v1.5"}, rerankers=set(), generators=set()
        )


def test_none_reranker_needs_no_model(tmp_path: Path) -> None:
    models = load_models(MODELS, LOCK, tmp_path / "c.json", embedders=set(), rerankers={"none"}, generators=set())
    assert dict(models.rerankers) == {}
