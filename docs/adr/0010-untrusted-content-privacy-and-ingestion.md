# 0010. Treat retrieved text as untrusted, keep content out of logs, and accept only pushed documents

- Status: Accepted. Partly superseded by ADR 0014.
- Date: 2026-11-07
- Deciders: Project maintainer

> Part of this decision is superseded on 2026-11-09 by [ADR 0014](0014-retrieval-only-and-app-side-fitting.md). xat-rag no longer wraps, escapes or labels retrieved text, and it no longer renders pinned prompts. The app must now treat returned text as untrusted and escape it. The other rules of this ADR stay in force. The text below is the history of the decision.

## Context

The indexes hold copies of internal docs, tickets and code changes. JIRA comments and diffs can hold text that tries to give instructions to the model (prompt injection). The organization requires offline work, no telemetry and no content in logs.

## Decision

- **Ingestion is push-only.** The app sends documents to xat-rag. xat-rag has no connectors to JIRA, GitHub or other network services. Optional helpers that read local exports can come later as extras.
- **Sanitize the extracted text, before chunking.** For PDF, DOCX and HTML, the parser runs first, and sanitizing runs on its text. For HTML, the parser also removes active content (`script`, `style`, `iframe`, `object` and similar). See [ADR 0012](0012-document-parsers.md). Sanitizing removes ANSI codes, control characters (but keeps tab and line breaks), zero-width characters, bidirectional controls and Unicode tag characters. Then it normalizes Unicode to NFC.
- **Retrieved chunks and the input section are untrusted.** Log lines can hold text from an attacker. Both go only in the user message, inside `<chunk ... trust="untrusted">` and `<input trust="untrusted">` blocks. Attributes come only from validated values, and xat-rag escapes `<` inside a block. The system prompt of the renderer tells the model to treat the blocks as data.
- **Pinned prompts are trusted.** They are files in `XatRag__PromptsFolder`, which the app installs. The HTTP API can only read them.
- **Access control.** Each request needs the access key of the service, and the service refuses browser requests. See [ADR 0003](0003-library-and-loopback-service.md).
- **Logs hold no content.** They hold hashed IDs, counts, times and error types only. No document text, queries, log lines or chunk IDs in clear text, because chunk IDs can hold titles and paths.
- **No telemetry, and no network connections** except on loopback. [ADR 0011](0011-offline-hardening.md) turns off LangSmith tracing and blocks other connections.
- **Personal data:** author and assignee values are metadata only. They do not go into chunk text or rendered messages. Free text (commit messages, comments) can still hold names. The app removes them before it sends the documents, if its policy requires it.
- **Deletion:** see [ADR 0005](0005-sqlite-store-and-index-manifest.md). Software cannot remove all copies on disk.
- **No encryption at rest in v1.** The indexes are in the data folder of the app. The app must protect it, for example with the disk encryption of the operating system.

## Consequences

- xat-rag works offline by design, and the app controls what goes into each RAG.
- The app must convert its data (for example a JIRA export) to the input format of xat-rag.
- Delimiters reduce the risk of prompt injection, but they do not remove it. Citation validation checks only the labels. The app must treat the answer as untrusted text.
- Encryption at rest needs a later ADR, because it changes the native packaging (for example SQLCipher).
