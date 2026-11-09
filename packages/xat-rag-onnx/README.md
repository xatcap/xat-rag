# xat-rag-onnx

The embedder and the reranker of xat-rag. ONNX Runtime runs them in the process, on the CPU.

| Class | LangChain interface | Model (v1) |
|---|---|---|
| `OnnxEmbeddings` | `Embeddings` | `bge-small-en-v1.5`, 384 dimensions, CLS pooling, normalized |
| `OnnxCrossEncoderCompressor` | `BaseDocumentCompressor` | `ms-marco-MiniLM-L6-v2` |

Rules:

- Model files come only from a local folder. `verify_model` checks the size and sha256 of each runtime file against the model lock file. From source, this file is `build/models.lock.json`. In a release, it is `models/models.lock.json`.
- Tokenizers load with `Tokenizer.from_file`, never with `from_pretrained`.
- A query longer than the model input is cut. A document longer than 512 tokens is an error, because the chunker must split it first.
- The reranker cuts the query to 256 tokens. It cuts the passage when the pair is too long, and it marks the result with `rerank_truncated`.
- A failure raises `StageError`. There is no silent fallback.
