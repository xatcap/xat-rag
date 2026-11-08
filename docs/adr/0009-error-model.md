# 0009. Map each kind of failure to one error type, with no retries

- Status: Accepted
- Date: 2026-11-07
- Deciders: Project maintainer

## Context

Apps must show the right message for each failure. xat-llm-engine maps each kind of failure to one status code and one error type, and it never retries. xat-rag follows the same rules, so that an app handles both in the same way.

## Decision

- Each kind of failure has one error type and one HTTP status. The table is in [architecture, section 11](../architecture.md#11-errors).
- Errors use the engine's body: `{"error": {"message", "type"}}`. Where the engine has a type with the same meaning (`invalid_request_error`, `not_found_error`, `service_unavailable_error`, `overloaded_error`, `internal_error`), xat-rag uses the same name.
- xat-rag does not use the engine's `budget_exceeded_error`, because it has another meaning there. A context that does not fit is `context_too_large_error` (422).
- One mapping in the code converts the errors of the library to HTTP responses. The match covers all error types.
- xat-rag never retries a stage.
- Each stage of a context request has a time limit that the context recipe sets. Ingestion runs as a job that the caller can cancel. A stage that takes too long gives `stage_timeout_error` (504), and the message names the stage.
- There is no silent fallback. For example, a reranker failure is an error. xat-rag does not continue without the reranker.
- In a document batch, each document has its own result. One bad document does not stop the others. A `parse_error` has a reason: `invalid`, `encrypted`, `no_text_layer`, `unsupported_media_type`, `too_large` or `parse_timeout`.
- After a `parse_timeout`, the service reports `degraded` and refuses new ingestion jobs. See [ADR 0012](0012-document-parsers.md).
- The pipeline never uses the LangChain `with_retry` or `with_fallbacks` methods. Errors from pypdf, lxml, beautifulsoup4 and LangChain become xat-rag error types in one place.
- A startup failure stops the process with a non-zero exit code and a message that tells what to do.

## Consequences

- Apps can use the same error body and most of the same error types for xat-rag and the engine.
- A temporary failure reaches the caller, and the caller decides if it tries again.
- Error messages follow the [writing style](../writing-style.md). They never hold document text or input values. xat-rag replaces the default validation messages of pydantic and FastAPI.

## Implementation notes (v0.1)

- One function in the service, `error_response`, maps each error to its status and body. A `Retry-After` header is set only for `overloaded_error` and `service_unavailable_error`.
- The request pool refuses a new request when `XatRag__QueueLimit` requests already wait or run. The response is `overloaded_error` with `Retry-After: 1`.
- The upload queue holds at most 256 MiB of document bytes, over all jobs. A new upload above that gives `overloaded_error` with `Retry-After: 5`.
- Jobs live in memory. The service keeps the last 200 finished jobs.
- A document that raises a xat-rag error gets its own `failed` result. An unexpected error ends the job with the status `failed`.
- The watchdog marks the service degraded when a document runs longer than twice the parse deadline plus 300 seconds. The deadline is 60 seconds.
- An internal error gives 500 `internal_error` with a fixed message. A failed stage, such as the reranker, is an internal error.
