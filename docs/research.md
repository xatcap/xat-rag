# Research: RAG for small models and small context windows

This page records the industry practices that shape xat-rag. It also lists the draft requirements and the open questions. The research was done in November 2026. The [architecture](architecture.md) and the [ADRs](adr) tell what xat-rag does with these findings.

Numbers from vendors or papers are their own claims. We did not measure them again. Where a value says "check", we must measure it on our target hardware.

## 1. The main constraint: the token budget

The target generator is Ministral-3-3B Q4_K_M with a context of 8192 tokens. It runs one request at a time. Parallel requests split the context between them.

A typical split of the 8k window:

| Part | Tokens |
|---|---|
| System prompt and instructions | about 400-800 |
| Log lines and other app context | about 1000-2000 |
| Space kept for the answer | about 600-1000 |
| Chat template, message overhead and chunk wrappers | about 200-400 |
| Safety margin (3%) | about 250 |
| **Left for retrieved chunks** | **about 3700-5700** |

That is about 10-18 chunks of 300-350 tokens. Thus, **precision at a small k is more important than recall at a large k.** This fact is the reason for reranking, pruning and an exact budget packer.

Small models also follow long or noisy prompts less well than large models. Less text of higher quality gives better answers than more text.

## 2. Chunking

- **Split by structure first.** Use Markdown headings, ticket fields, diff hunks and code syntax nodes. Use recursive or fixed-size splits only as a fallback. Chroma's study found that a tuned recursive splitter is a strong baseline. The best and the worst strategies differed by about 9 points of recall. Source: [Chroma, Evaluating Chunking](https://research.trychroma.com/evaluating-chunking).
- **Chunk code by its syntax tree.** cAST uses tree-sitter. It gave +4.3 Recall@5 on RepoEval and +2.67 Pass@1 on SWE-bench, compared with line-based chunks. Sources: [cAST, arXiv 2506.15655](https://arxiv.org/html/2506.15655v1), [astchunk](https://github.com/yilinjz/astchunk).
- **Contextual Retrieval.** An LLM writes a context line of 50-100 tokens for each chunk before indexing. Contextual embeddings plus contextual BM25 cut top-20 retrieval failures by 49%. With reranking, the cut was 67%. Source: [Anthropic, Contextual Retrieval](https://www.anthropic.com/research/contextual-retrieval).
  - A 3B model is slow for this work, and its context lines can be wrong.
  - A cheap version needs no LLM: a fixed header such as `Handbook > Deploy > Linux`, `repo/path@sha` or `PROJ-123: summary`.
- **Late chunking.** The model embeds the whole document, then pools the token vectors of each chunk. It needs a long-context embedder and access to the token vectors. Source: [Late Chunking, arXiv 2409.04701](https://arxiv.org/html/2409.04701v3).
- **Small-to-big (parent and child).** Search small units, then give a larger unit to the model when the budget allows it. In an 8k window, expand to neighbor chunks before whole parents.

## 3. Hybrid retrieval

- **Run BM25 and dense search together. Merge the lists with Reciprocal Rank Fusion (RRF, k=60).** RRF uses only ranks, so it needs no score normalization. Source: [Cormack et al. 2009](https://plg.uwaterloo.ca/~gvcormac/cormacksigir09-rrf.pdf).
- **BM25 is necessary for technical text.** Error codes, exception names, file paths, JIRA keys and commit SHAs need exact matches. Dense models do not match them well.
- **Use a code-aware lexical tokenizer.** Split `camelCase`, `snake_case` and dotted names into parts, and also keep the full token. Keep JIRA keys and SHAs whole.
- **Filter by metadata before or during the search.** Never filter after the top-k cut, because the cut can remove all the matching items.

## 4. Reranking

Get 30-50 candidates, rerank them with a cross-encoder, and keep the top 5-10.

| Model | Size | License | Notes |
|---|---|---|---|
| ms-marco-MiniLM-L6-v2 | about 22M | Apache-2.0 | English. Fast on CPU: about 14-36 ms for about 30 short pairs. Longer chunks take more time. Source: [Qdrant](https://qdrant.tech/blog/oxidizing-cross-encoders/). |
| bge-reranker-v2-m3 | 568M | Apache-2.0 | Multilingual. 512-token input. Supported by llama.cpp `--reranking`. Source: [llama.cpp server](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md). |
| mxbai-rerank-base-v2 | 0.5B | Apache-2.0 (check) | BEIR 55.57, as the vendor reports. Source: [mixedbread](https://mixedbread.com/blog/mxbai-rerank-v2). |
| Qwen3-Reranker-0.6B | 0.6B | Apache-2.0 (check) | Community GGUF and ONNX builds only. |

We found no independent CPU numbers for the 0.5B rerankers. We must measure them on our laptops.

## 5. Small embedding models

| Model | Size | Dimensions | License | Notes |
|---|---|---|---|---|
| bge-small-en-v1.5 | 33M | 384 | MIT | English. About 35 MB as int8 ONNX. Fast on CPU. |
| multilingual-e5-small | 118M | 384 | MIT | Multilingual. |
| snowflake-arctic-embed-m-v2.0 | 305M | 768, Matryoshka to 256 | Apache-2.0 | Multilingual. |
| nomic-embed-text-v2-moe | about 475M | 768, Matryoshka to 256 | Apache-2.0 | Multilingual. Source: [Hugging Face](https://huggingface.co/nomic-ai/nomic-embed-text-v2-moe). |
| Qwen3-Embedding-0.6B | 0.6B | 32-1024 | Apache-2.0 | MTEB multilingual 64.33. Slow on CPU for 100k chunks. Source: [Qwen](https://qwenlm.github.io/blog/qwen3-embedding). |
| EmbeddingGemma-300m | 300M | 768, Matryoshka to 128 | Gemma Terms | Strong and small. The Gemma Terms are not an OSI license and need a legal check before redistribution. Source: [Hugging Face](https://huggingface.co/blog/embeddinggemma). |

- **Matryoshka cuts and int8 or binary vectors make the index smaller and the search faster.** EmbeddingGemma loses about 3 MTEB points from 768 to 128 dimensions.
- **Binary vectors need a second step.** Search with the binary vectors first, then score the short list again with full vectors.
- **A license must allow redistribution in a commercial desktop app.** This rule removes some strong models.

## 6. Context compression and pruning

- **Extractive sentence pruning is the safe default.** Keep the sentences that score highest against the query, and never change their text. Citations stay correct.
- **Provence** prunes sentences and reranks in one pass. Source: [Provence, arXiv 2501.16214](https://arxiv.org/html/2501.16214v1). XProvence is the multilingual version: [arXiv 2601.18886](https://arxiv.org/html/2601.18886v1). The Provence checkpoint is reported as CC BY-NC 4.0, which does not allow commercial use. We exclude it.
- **LLMLingua-2** removes tokens with a BERT-class classifier. It gives 2-5x compression. Source: [arXiv 2403.12968](https://arxiv.org/html/2403.12968v2). Token removal can break code, identifiers and quoted log text. Use it only on prose, if at all.

## 7. Order and packing

- **Models use text in the middle of a long prompt less well** ("lost in the middle"). Source: [Liu et al., TACL 2024](https://arxiv.org/abs/2307.03172).
  - Put the strongest evidence first and last.
  - Put the question and the log lines after the documents. Source: [Anthropic, long context tips](https://docs.anthropic.com/en/docs/long-context-window-tips).
- **Pack greedily by score, with diversity.** Use Maximal Marginal Relevance (MMR), remove near duplicates, apply a quota for each source, and stop at the budget.
- **Count tokens with the generator's own tokenizer.** A count from another tokenizer can be very different, mostly on code and identifiers.

## 8. Query rewriting with small models

- **HyDE and LLM query expansion are less reliable with small models.** The model adds wrong terms, and the search moves to the wrong content. Source: [GOLFer, ACL Findings 2025](https://arxiv.org/html/2506.04762v1).
- **Expansion rarely helps BM25 on technical text.**
- **Build the query in a deterministic way when possible.** Section 11 shows how to do this for log lines.

## 9. Citations, grounding and untrusted text

- **Give each chunk a stable ID** and a content hash.
- **Ask the model for a structured answer** that cites the chunks. A local model can use `response_format` and a GBNF `grammar`.
- **Check the answer after generation.** Each cited ID must be in the prompt.
- **Treat retrieved text as untrusted.** JIRA comments and diffs can hold prompt injections. Put the text inside delimiters, and tell the model that the text is data and not instructions.

## 10. Evaluation, indexing and stores

### Evaluation

- **Retrieval metrics:** recall@k, nDCG@10 and MRR, on a golden set of 50-200 cases for each source kind.
- **Token metrics:** the share of relevant tokens in the context (Chroma IoU), to tune the chunk size.
- **LLM-judged metrics:** context precision, context recall and faithfulness. Source: [RAGAS](https://docs.ragas.io/en/stable/references/metrics.html). An offline 3B judge is weak, so use these as signals and not as release gates.
- **Gate releases on deterministic retrieval metrics.**

### Indexing

- **Index by content hash.** Embed again only the chunks that changed. Delete the chunks of removed documents.
- **Record the embedding model and its settings in the index.** Never mix vectors from two models. If the model changes, rebuild the index.

### Embedded stores

| Store | Notes |
|---|---|
| SQLite FTS5 + sqlite-vec | One file, ACID writes, simple backup. Brute-force vector search, which is fast enough to about 100k-1M vectors (check). sqlite-vec is before version 1.0. |
| LanceDB | Columnar. ANN, full-text search and RRF are built in. Rust core. A larger dependency. |
| DuckDB VSS + FTS | Good for analytics. The persistence of the VSS index is experimental. |
| Qdrant local, Chroma | Heavier, or similar to a service. Chroma's default embedder downloads a model. |
| LangChain `BM25Retriever` + `SQLiteVec` | `BM25Retriever` keeps the index in Python memory: about 1.8 GB at 100k chunks (extrapolated). `langchain-community` is being retired. See [ADR 0001](adr/0001-langchain-core-as-foundation.md). |
| FAISS | Vectors only. Needs a separate lexical index. |
| Tantivy | A strong BM25 engine in Rust, with a Python binding. |

## 11. Log lines as a query

**The log lines are the query.** They are long and noisy, and most of their tokens do not help the search. A deterministic query builder does these steps:

1. Mask the variable parts: timestamps, UUIDs, hex addresses, IP addresses and numbers.
2. Group the masked lines into templates, as the Drain algorithm does. Keep a count for each template. Source: [EnrichLog, arXiv 2512.11997](https://arxiv.org/html/2512.11997).
3. Extract the strong identifiers: error codes, exception classes, `file:line` stack frames, logger names, JIRA keys (`[A-Z]+-\d+`), SHAs and endpoint paths. Send them to BM25 and to exact metadata filters.
4. Send the template text to the dense search.

Each source kind needs its own handling:

| Source | Chunk unit | Header and metadata | Notes |
|---|---|---|---|
| Docs | One section per heading | The heading path | Prune prose if the budget is short. |
| JIRA tickets | Summary and description as the parent, one chunk per comment | Key, status, resolution, component, labels, dates | Resolved tickets with a resolution text are the best evidence. |
| Git diffs | One chunk per file hunk | Repo, path, short SHA, commit message. Author and date as metadata. | Link a stack frame (`file:line`) to the hunks that touch the file. Never prune. |
| Prompts | The whole prompt | Name and version | Select by name ("pinned"), not by search. Never prune or reorder. |
| PDF | One section per outline heading, or per page group | File, outline path, page numbers | Index only pages with text. Tables become plain text. |
| DOCX | One section per heading style | File, heading path, block numbers | Tables become pipe rows. Remove hidden text. |
| HTML | One section per `h1` to `h6` heading | File, heading path, source lines | Remove scripts, styles and hidden elements. |

**Search each RAG separately, then merge the results.** Give each source a quota, so that one source cannot use the whole budget. Rules and metadata route a query more safely than the 3B model.

## 12. Draft requirements

These requirements are the input to the architecture. The [architecture](architecture.md) shows which part meets each one.

### Functional requirements

| ID | Requirement |
|---|---|
| FR-1 | Ingest Markdown or plain text, unified diffs, JIRA issue JSON, prompt files, PDF, DOCX and HTML. Each source kind has a pluggable parser. For a scanned PDF, index the pages that have text, and report the skipped pages. |
| FR-2 | Chunk by structure. No chunk is larger than the maximum token size that the recipe sets. |
| FR-3 | Give each chunk a deterministic context header. Context written by a model is not in v1. |
| FR-4 | Give each chunk a stable ID and a content hash. Ingesting unchanged content again makes no new embeddings. |
| FR-5 | Add, update and delete at document level. A query does not find a deleted document after the delete returns. |
| FR-6 | Retrieve with BM25, dense search, or both merged with RRF. The query can choose the mode. |
| FR-7 | Filter by metadata with equality, set and range conditions, before the top-k cut. |
| FR-8 | Search several RAGs in one request, with a quota for each source. |
| FR-9 | Rerank with a cross-encoder. The recipe sets the number of candidates and the number to keep. |
| FR-10 | Prune with extractive sentence selection that never changes the kept text. |
| FR-11 | Pack the context to a token budget, counted with the generator's tokenizer. The result is never larger than the budget. |
| FR-12 | Build a query from log lines in a deterministic way. The same input gives the same query. |
| FR-13 | Give each packed item an ID, a source and a location. Validate the citations of an answer. |
| FR-14 | Return the scores of each stage on request, for debugging. |
| FR-15 | Measure recall@k, nDCG@10 and MRR on a golden set, and compare the results between versions. |
| FR-16 | Store the embedding model, its settings and the chunker versions in the index. Refuse an index that does not match. |

### Non-functional requirements

| ID | Requirement |
|---|---|
| NFR-1 | Offline. No network traffic except on loopback. Tested with the network blocked. |
| NFR-2 | No downloads at runtime. Models are bundled files with sha256 locks. |
| NFR-3 | No telemetry. At the default log level, logs hold no document text, queries or log lines. |
| NFR-4 | The service accepts only loopback connections. It supports port 0, a ready file and a stop when its input closes. |
| NFR-5 | One error type for each kind of failure. No retries. A time limit for each stage. |
| NFR-6 | Semantic Versioning for the library API, the HTTP API, the index format and the recipe format. |
| NFR-7 | Query time of `POST /v1/context`: p95 of at most 300 ms without the reranker, and at most 1.5 s with the reranker for a context with 3 RAGs. Measured at 100k chunks on the hardware floor. |
| NFR-8 | Memory: at most 500 MB of extra RAM for the whole xat-rag service (embedder, reranker, SQLite and the service) at 100k chunks, next to the 2.15 GB generator. |
| NFR-9 | Ingestion: at least 1000 chunks per minute on the CPU of the hardware floor, so a first sync of 100k chunks takes about 1.5 to 2 hours. Ingestion can stop and continue. |
| NFR-10 | Index writes are atomic. A crash does not damage the index. |
| NFR-11 | All shipped models and libraries allow redistribution in commercial desktop apps. A check lists them in the third-party notices. |
| NFR-12 | Platforms: macOS arm64, Windows x64 and Linux x64. |
| NFR-13 | Documentation follows ASD-STE100. ADRs record the significant decisions. |
| NFR-14 | The golden-set recall@10 of each source kind does not drop by more than 2 points (absolute) between minor versions. CI checks it, and a larger drop blocks the release. |
| NFR-15 | Hardware floor: a 4-core x64 or Apple Silicon CPU, 8 GB of RAM and no GPU. All targets apply to this machine, with the generator loaded. |

## 13. Open questions

| Question | Status |
|---|---|
| Which framework? | Decided: LangChain core interfaces, with our own retrievers and budget stages. See [ADR 0001](adr/0001-langchain-core-as-foundation.md). |
| PDF, DOCX and HTML in v1? | Decided: yes, parsed in the service process, with limits. See [ADR 0012](adr/0012-document-parsers.md). |
| Python or .NET? | Decided: Python. See [ADR 0002](adr/0002-python.md). |
| Library, service, or both? | Decided: both. See [ADR 0003](adr/0003-library-and-loopback-service.md). |
| Who runs the embedding and reranking models? | Decided: xat-rag, with ONNX Runtime. See [ADR 0004](adr/0004-onnx-runtime-and-locked-models.md). |
| Scale target? | Decided: about 100k chunks for each laptop. See [ADR 0005](adr/0005-sqlite-store-and-index-manifest.md). |
| Which generators in v1? | Decided: the local model only. See [ADR 0007](adr/0007-context-budget-and-token-counting.md). |
| Which content languages in v1? | Decided: English only. |
| Who owns the budget split? | Decided: the context recipe. A request can only lower the values. See [ADR 0007](adr/0007-context-budget-and-token-counting.md). |
| Are prompts retrieved or pinned? | Decided: pinned by name and version, from a folder that the app installs. See [ADR 0010](adr/0010-untrusted-content-privacy-and-ingestion.md). |
| Are context written by a model and query rewriting allowed? | Decided: not in v1. See [ADR 0006](adr/0006-hybrid-retrieval-and-reranking.md). |
| JIRA input format? | Decided: JIRA REST issue JSON. |
| Live connectors to JIRA or GitHub? | Decided: no. Push-only ingestion. See [ADR 0010](adr/0010-untrusted-content-privacy-and-ingestion.md). |
| Encryption at rest? | Decided: not in v1. A documented limit. See [ADR 0010](adr/0010-untrusted-content-privacy-and-ingestion.md). |
| Author and assignee data? | Decided: metadata only, not in the chunk text. See [ADR 0010](adr/0010-untrusted-content-privacy-and-ingestion.md). |
| Hardware floor and the numbers in NFR-7, NFR-8 and NFR-9? | Decided: see NFR-7, NFR-8, NFR-9 and NFR-15. |
| Who owns the golden sets, and what threshold blocks a release? | Decided: the team that owns the app builds 50 to 100 cases for each source kind, from sanitized real data. A drop of more than 2 points of recall@10 blocks a release (NFR-14). |
| Budget, quota, retrieval, upload, parser and time-limit defaults? | Decided: the values in the [architecture](architecture.md#17-defaults). |
| Encryption at rest, invisible PDF text, OCR, names in free text? | Accepted as v1 limits. See the [architecture](architecture.md#14-privacy-and-untrusted-content). |
