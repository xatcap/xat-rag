# 0015. Remove the access key of the service

- Status: Accepted
- Date: 2026-11-09
- Deciders: Project maintainer
- Partly supersedes: [ADR 0003](0003-library-and-loopback-service.md), [ADR 0010](0010-untrusted-content-privacy-and-ingestion.md) and [ADR 0011](0011-offline-hardening.md).

## Context

The service required a random access key in `XatRag__AccessKey`. Each request sent the key in the `Authorization` header. The app had to make the key, keep it and give it to the service at each start.

The key was the only control against other programs on the same machine. The project maintainer decided to remove the key, and accepted the exposure in the consequences below.

## Decision

- The service has no access key and no `Authorization` check. The key does not become optional.
- The service ignores a stale `Authorization` header. It also ignores a stale `XatRag__AccessKey` variable.
- The error type `authentication_error` and the status 401 are removed.
- `/openapi.json` stays. It has the same checks as each other route.
- These controls do not change:
  - The service listens on loopback only.
  - The service accepts only loopback `Host` values.
  - The service refuses requests with an `Origin` header, and it sends no CORS headers.
  - The limits of the requests, the network guard and the removal of other secrets do not change.
- The Unix socket code does not change.

## Consequences

Read these consequences before you use the service on a shared machine.

- Each program on the machine can connect to the TCP port. This includes the programs of other users.
- A program that connects can read the indexed text, upload documents, delete documents, cancel jobs and rebuild a RAG.
- An uploaded document can put text from an attacker into the results. The app must treat returned text as untrusted. See the [app guide](../app-guide.md).
- The mode `0700` of the data folder does not limit access through the service.
- A Unix socket gets the mode `0600` only when `XatRag__ReadyFile` is set. Without a ready file, the socket stays open to all users. With a ready file, the socket is open to all users for a short time at the start.
- Windows has no Unix socket mode in xat-rag, so a shared Windows machine has no control against other users.
- A browser sends no `Origin` header on a cross-site `GET` without CORS. Such a request reaches the service. The page cannot read the response, and each `GET` route only reads data. A page can detect the service, and it can use request slots for a short time.
- The app has no key to make or keep.
- An old app that sends a key continues to work.

## Alternatives considered

- **Make the key optional.** The maintainer refused this option. The key must not stay in the code.
- **Keep the key for the routes that change data.** The maintainer refused this option for the same reason.
- **Make the Unix socket private on each start path.** The maintainer did not include this change.
- **Refuse requests by their `Sec-Fetch-Site` header.** This protects no data, because a page cannot read a response. The risk is documented instead.
