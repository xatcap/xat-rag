# xat-rag-parsers

Text parsers and the structural chunker of xat-rag. See [how it works, step 5](../../docs/how-it-works.md#step-5-chunk).

| Parser | Kind | Sections |
|---|---|---|
| `MarkdownParser` (`markdown-v3`) | `markdown` | One section for each heading. The heading path is the header and the anchor. Text before the first heading is the `h` section. |
| `TextParser` (`text-v2`) | `text` | One section. The chunker splits it at its paragraphs when it is too large. |
| `DiffParser` (`diff-v1`) | `diff` | One section for each file hunk. Metadata: `path`, `path_name` (the lowercase file name) and `change`. |
| `JiraParser` (`jira-v1`) | `jira` | The summary and the description (`desc`), then one section for each comment. Author names are never in the text. |
| `PromptParser` (`prompt-v1`) | `prompt` | One section, never split. |

`StructuralChunker` turns sections into chunks. A section that is larger than `max_chunk_tokens` (in size ruler tokens) or than the embedder input is split again. The chunker counts with both token counters, and the size limits always win.

Sections of the kinds `markdown`, `text`, `pdf`, `docx`, `html` and `xlsx` split by their blocks. The split goes to list items and lines, then to sentences, words and characters. Tables that fit stay whole, and large tables split at their rows with the header repeated. The module `block_splitter` holds this split. It can also use the embedder to cut where the topic changes.

Diff, JIRA and prompt sections use a recursive split on separators. A prompt section is never split. The chunker also sanitizes the headers and the metadata of each section.

The parsers get bytes. They never read files or the network.
