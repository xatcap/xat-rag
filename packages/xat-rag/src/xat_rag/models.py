"""Load the locked models of xat-rag: the embedder, the rerankers and the generator tokenizer."""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from xat_rag_core.budget import GeneratorProfile
from xat_rag_core.errors import ConfigError
from xat_rag_core.ids import sha256_hex
from xat_rag_onnx import OnnxEmbeddings, OnnxModel, load_lock, verify_model
from xat_rag_onnx.embeddings import BGE_QUERY_PREFIX
from xat_rag_onnx.locks import ModelLock
from xat_rag_tokenizers import HfTokenCounter, profile
from xat_rag_tokenizers.profiles import MINISTRAL_3_3B


@dataclass(frozen=True, slots=True)
class EmbedderSpec:
    """The facts about a supported embedder."""

    dims: int
    query_prefix: str
    document_prefix: str


EMBEDDERS: Mapping[str, EmbedderSpec] = MappingProxyType({"bge-small-en-v1.5": EmbedderSpec(384, BGE_QUERY_PREFIX, "")})
_ONNX_FILES = frozenset({"model.onnx", "tokenizer.json"})
RERANKERS: frozenset[str] = frozenset({"ms-marco-minilm-l6-v2"})
GENERATOR_TOKENIZERS: Mapping[str, str] = MappingProxyType({MINISTRAL_3_3B.name: "ministral-3-3b-tokenizer"})


@dataclass(frozen=True, slots=True)
class LoadedModels:
    """The loaded models. One object for each model, shared by all RAGs."""

    embedders: Mapping[str, OnnxEmbeddings]
    embedder_sha256: Mapping[str, str]
    rerankers: Mapping[str, OnnxModel]
    counters: Mapping[str, HfTokenCounter]
    profiles: Mapping[str, GeneratorProfile]


def _lock_entry(locks: Mapping[str, ModelLock], name: str) -> ModelLock:
    entry = locks.get(name)
    if entry is None:
        raise ConfigError(f"The model '{name}' is not in the model lock.")
    return entry


def load_models(
    models_folder: Path,
    lock_file: Path,
    cache_file: Path,
    *,
    embedders: set[str],
    rerankers: set[str],
    generators: set[str],
    threads: int = 0,
) -> LoadedModels:
    """Check the locked files of the named models, and load them. Raise `ConfigError` on any problem."""
    locks = load_lock(lock_file)
    loaded_embedders: dict[str, OnnxEmbeddings] = {}
    sha: dict[str, str] = {}
    for name in sorted(embedders):
        spec = EMBEDDERS.get(name)
        if spec is None:
            raise ConfigError(f"The embedder '{name}' is not supported. v1 supports: {', '.join(EMBEDDERS)}.")
        entry = _lock_entry(locks, name)
        verify_model(models_folder / name, entry, cache_file, only=_ONNX_FILES)
        model = OnnxModel(models_folder / name, threads)
        loaded_embedders[name] = OnnxEmbeddings(
            model, name, spec.dims, query_prefix=spec.query_prefix, document_prefix=spec.document_prefix
        )
        # The manifest identity covers the weights and the tokenizer of the embedder.
        sha[name] = sha256_hex(f"{entry.file('model.onnx').sha256}:{entry.file('tokenizer.json').sha256}".encode())
    loaded_rerankers: dict[str, OnnxModel] = {}
    for name in sorted(rerankers - {"none"}):
        if name not in RERANKERS:
            raise ConfigError(f"The reranker '{name}' is not supported. v1 supports: {', '.join(sorted(RERANKERS))}.")
        verify_model(models_folder / name, _lock_entry(locks, name), cache_file, only=_ONNX_FILES)
        loaded_rerankers[name] = OnnxModel(models_folder / name, threads)
    counters: dict[str, HfTokenCounter] = {}
    profiles: dict[str, GeneratorProfile] = {}
    for name in sorted(generators):
        generator = profile(name)
        folder = GENERATOR_TOKENIZERS[name]
        entry = _lock_entry(locks, folder)
        verify_model(models_folder / folder, entry, cache_file, only=frozenset({"tokenizer.json"}))
        # The tokenizer ID comes from the lock, so a new tokenizer file changes the manifest.
        tokenizer_id = f"{folder}@{entry.file('tokenizer.json').sha256[:12]}"
        counters[name] = HfTokenCounter(models_folder / folder / "tokenizer.json", tokenizer_id)
        profiles[name] = generator
    return LoadedModels(loaded_embedders, sha, loaded_rerankers, counters, profiles)
