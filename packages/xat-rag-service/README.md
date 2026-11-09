# xat-rag-service

The loopback HTTP service of xat-rag. It hosts the library over HTTP. It has the upload parser, the ingestion jobs and the request checks. See the [API reference](../../docs/api.md) and the [architecture](../../docs/architecture.md).

The service has no authentication. Each program on the same machine can use it.

Start it with `xat-rag-service`. In a release folder, the service starts with its default paths. From source, the four path settings are required. The [API reference](../../docs/api.md#settings) lists the settings and their defaults.

At startup, `main` reads the data folder with `xat_rag_service.paths`, which uses the standard library only. Then it calls `harden_process()`.

A startup failure exits with code 2. It prints one line that starts with `xat-rag:`.

The full list of routes, bodies, responses and errors is in the [API reference](../../docs/api.md).
