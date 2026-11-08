# 0003. Offer a Python library and a loopback HTTP service

- Status: Accepted
- Date: 2026-11-07
- Deciders: Project maintainer

## Context

Apps of the organization can be written in Flutter, .NET or Python. A Python-only library works only for Python apps. A service works for all of them.

## Decision

- The package `xat-rag` is the public Python library. Python tools and the evaluation harness use it directly.
- The package `xat-rag-service` is a thin HTTP host over the library. It has no logic of its own.
- The service has this lifecycle:
  - It accepts only loopback connections (`127.0.0.1`).
  - Port 0 lets the system choose a free port.
  - A ready file holds the address, the process ID and the version.
  - It can stop when its standard input closes.
  - A lock file allows one service for each data folder.
- Loopback is not enough protection, because other programs and web pages can reach `127.0.0.1`. Thus:
  - Each request must send a random access key that the app gives at each start (`XatRag__AccessKey`).
  - The service accepts only loopback `Host` values, sends no CORS headers and refuses requests with an `Origin` header.
  - On macOS and Linux, the service can listen on a Unix socket.
- The settings use environment variables with the prefix `XatRag__`.
- Document upload uses `multipart/form-data`, so binary files (PDF, DOCX) need no base64 encoding. See [ADR 0012](0012-document-parsers.md).
- The routes are under `/v1`. Each release publishes its OpenAPI document. See [architecture, section 9](../architecture.md#9-http-service).

## Consequences

- Any app can use xat-rag, in any language.
- The app must start and stop the xat-rag service as a separate process.
- Each request makes one local HTTP call. The time of this call is small compared with the search time.
- The library API and the HTTP API are both SemVer contracts.
- The app must make and keep the access key. A program that can read the environment of the service can read the key.

## Implementation notes (v0.1)

- The rule of a published OpenAPI document is not met yet. See the [open items](../architecture.md#16-later-versions-and-open-items).
