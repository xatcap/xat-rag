# xat-rag-parsers

Text parsers and the structural chunker of xat-rag. See the [architecture, sections 4 and 5](../../docs/architecture.md#4-data-model).

| Parser | Kind | Sections |
|---|---|---|
| `MarkdownParser` (`markdown-v2`) | `markdown` | One section for each heading. The heading path is the header and the anchor. Text before the first heading is the `h` section. |
| `TextParser` (`text-v1`) | `text` | One section. The chunker splits it when it is too large. |
| `DiffParser` (`diff-v1`) | `diff` | One section for each file hunk. Metadata: `path`, `path_name` (the lowercase file name) and `change`. |
| `JiraParser` (`jira-v1`) | `jira` | The summary and the description (`desc`), then one section for each comment. Author names are never in the text. |
| `PromptParser` (`prompt-v1`) | `prompt` | One section, never split. |

`StructuralChunker` turns sections into chunks. A section that is larger than `max_chunk_tokens` (in size ruler tokens) or than the embedder input is split again. The splitter is LangChain `RecursiveCharacterTextSplitter`, and it counts with both token counters. The chunker also sanitizes the headers and the metadata of each section.

The parsers get bytes. They never read files or the network.
