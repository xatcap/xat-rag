# xat-rag-service

The loopback HTTP service of xat-rag. It hosts the library over HTTP. It has the upload parser, the ingestion jobs and the access checks. See the [API reference](../../docs/api.md), the [architecture, section 9](../../docs/architecture.md#9-http-service) and [ADR 0003](../../docs/adr/0003-library-and-loopback-service.md).

Start it with `xat-rag-service`. The [API reference](../../docs/api.md#settings) lists its settings.

At startup, the service calls `harden_process()` first ([ADR 0011](../../docs/adr/0011-offline-hardening.md)).

The full list of routes, bodies, responses and errors is in the [API reference](../../docs/api.md).
