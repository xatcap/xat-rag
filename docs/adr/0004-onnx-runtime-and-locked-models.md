# 0004. Run the embedder and the reranker with ONNX Runtime, from locked files

- Status: Accepted
- Date: 2026-11-07
- Deciders: Project maintainer

## Context

xat-llm-engine does not do embeddings or reranking. xat-rag must run these models itself, offline, on consumer CPUs. The two options were ONNX Runtime in the process, or llama.cpp `--embedding` and `--reranking` as child processes.

## Decision

- ONNX Runtime runs the embedder and the reranker inside the service process, with the CPU execution provider.
- The v1 models are English only:
  - Embedder: `bge-small-en-v1.5` (MIT), 384 dimensions, int8.
  - Reranker: `ms-marco-MiniLM-L6-v2` (Apache-2.0), int8.
- `build/models.lock.json` lists each model with its source repository, revision and license. It lists each file of each model with its size and sha256.
- `tools/fetch_models.py` gets the files at build time only. The models ship as a separate release file, as in the engine's ADR 0003.
- At startup, xat-rag checks the size and the sha256 of each file. It caches the result in the data folder for faster starts.
- `OnnxEmbeddings` implements LangChain `Embeddings`, and `OnnxCrossEncoderCompressor` implements LangChain `BaseDocumentCompressor`. See [ADR 0001](0001-langchain-core-as-foundation.md). LangChain's `FastEmbedEmbeddings`, `HuggingFaceEmbeddings` and `CrossEncoderReranker` are not used, because they download models or need torch.
- xat-rag loads tokenizers only with `Tokenizer.from_file`, never with `from_pretrained`, which can download. The offline settings for `huggingface_hub` are in [ADR 0011](0011-offline-hardening.md).

## Consequences

- No second process to supervise. The outputs on one CPU are deterministic.
- ONNX Runtime supports the BERT-family embedders and cross-encoders that we use. Each new model needs an export and a check.
- GPU execution providers (CoreML, DirectML, CUDA) are off in v1. Small encoders are fast enough on CPU. We check this with the evaluation harness.
- A model change makes all indexes of that model invalid. See [ADR 0005](0005-sqlite-store-and-index-manifest.md).
- The lock file lists the license of each model. Each new model needs a license check before it goes into the lock.

## Implementation notes (v0.1)

- The startup check covers the files that the runtime uses: `model.onnx` and `tokenizer.json` of each ONNX model, and `tokenizer.json` of the generator. `tekken.json` is for development only.
- The embedder identity in the manifest is the sha256 of the text `<model.onnx sha256>:<tokenizer.json sha256>`.
- The check results are cached in `model-check-cache.json` in the data folder.
- `build/models.sources.json` lists the upstream sources. `tools/fetch_models.py --write-lock` writes the lock from them.
- Both ONNX models are ONNX int8 conversions of the upstream models, as `build/models.sources.json` states.
