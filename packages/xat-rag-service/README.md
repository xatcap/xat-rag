# xat-rag-service

The loopback HTTP service of xat-rag. It hosts the library over HTTP. It has the upload parser, the ingestion jobs and the access checks. See the [API reference](../../docs/api.md), the [architecture, section 9](../../docs/architecture.md#9-http-service) and [ADR 0003](../../docs/adr/0003-library-and-loopback-service.md).

Start it with `xat-rag-service`. It reads these environment variables. The names are not case-sensitive.

| Variable | Required | Meaning |
|---|---|---|
| `XatRag__Urls` | No | `http://127.0.0.1:<port>` or `http://[::1]:<port>`. Port 0 lets the system choose. Or `http://unix:<path>` for a Unix socket. The default is `http://127.0.0.1:0`. |
| `XatRag__AccessKey` | Yes | The access key, with at least 16 ASCII characters. Each request, except `GET /health/live`, sends `Authorization: Bearer <key>`. |
| `XatRag__DataFolder` | Yes | The data folder. It holds the indexes, the lock file `xat-rag.lock` and the model check cache. |
| `XatRag__RecipesFolder` | Yes | The folder with `rags/*.toml` and `contexts/*.toml`. |
| `XatRag__ModelsFolder` | Yes | The folder with the model files. |
| `XatRag__LockFile` | Yes | The path of `models.lock.json`. |
| `XatRag__PromptsFolder` | No | The folder with the pinned prompts. |
| `XatRag__ReadyFile` | No | The file that gets `{url, pid, version}` when the service is ready. |
| `XatRag__StopWhenInputCloses` | No | `true` stops the service when its standard input closes. |
| `XatRag__QueueLimit` | No | The number of requests that can wait or run in the pool. From 1 to 1000. The default is 8. |
| `XatRag__Threads` | No | The threads of ONNX Runtime. From 0 to 256. The default 0 lets ONNX Runtime choose. |

At startup, the service calls `harden_process()` first (ADR 0011). It deletes all `LANGSMITH_*` and `LANGCHAIN_*` variables, and all variables that end in `_API_KEY`, `_TOKEN`, `_ENDPOINT` or `_ENDPOINTS`, except `XatRag__*`. This also removes unrelated variables, for example `OTEL_EXPORTER_OTLP_ENDPOINT`.

The full list of routes, bodies, responses and errors is in the [API reference](../../docs/api.md).
