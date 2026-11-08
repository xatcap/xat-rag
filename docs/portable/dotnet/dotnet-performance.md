# .NET Performance

This page gives platform-level performance guidance for .NET 10. It shows how to measure. It also shows how the garbage collector, the JIT compiler, threading, I/O, serialization, caching, and deployment choices affect speed and memory. Language-level techniques are in [C# Performance](./csharp-performance.md). Database access is in [EF Core Performance](./efcore-performance.md).

---

## Measuring

**Do**
- Pick the tool that answers your question:

  | Question | Tool |
  |----------|------|
  | Is method A faster than method B? How much does it allocate? | **BenchmarkDotNet** with `[MemoryDiagnoser]` |
  | What is the app doing right now (CPU, GC, thread pool, exceptions)? | **`dotnet-counters`** |
  | Where does the time go? | **`dotnet-trace`**, then open the trace in Visual Studio, PerfView, or speedscope |
  | What is filling up memory? | **`dotnet-gcdump`** |
  | Why did it hang or crash? | **`dotnet-dump`** |

- Install the diagnostic tools as .NET tools (`dotnet tool install -g dotnet-counters`) and use them against a Release build.
- Start with `dotnet-counters`. It quickly shows if the problem is CPU, GC, lock contention, or a thread pool without free threads.

```bash
dotnet-counters monitor --process-id <pid> --counters System.Runtime
dotnet-trace collect --process-id <pid> --profile cpu-sampling
```

- Add **performance regression tests** for critical paths: run benchmarks in CI and compare against a stored baseline.
- Publish your own metrics with `System.Diagnostics.Metrics` (a `Meter` with counters and histograms) for things like *lines parsed per second*. See [.NET › Observability](./dotnet-best-practices.md#observability).

**Do not**
- Measure Debug builds, builds with a debugger attached, or the first few seconds after startup. The JIT is still optimising code during that time (see below).
- Compare numbers taken on different machines, or on a laptop running on battery.

---

## The garbage collector

The garbage collector (GC) frees memory that is no longer in use. Basic knowledge of how it works explains most memory advice.

- **Generations.** New objects start in *generation 0*. Most objects have a short life, and gen 0 collections are cheap. Objects that survive move to gen 1 and then gen 2. Gen 2 collections are expensive.
- **Large Object Heap (LOH).** Objects of 85,000 bytes or more (for example, a large array or string) go directly to the LOH. The GC collects it only with gen 2, so frequent large allocations are costly.
- **Workstation vs Server GC.** Workstation GC uses one heap and suits desktop and small console apps. Server GC uses one heap per CPU core and suits busy services. ASP.NET Core apps use Server GC by default.
- **DATAS** (Dynamic Adaptation To Application Sizes) is on by default with Server GC. It grows and shrinks the number of heaps to match the load, so a quiet service uses less memory.

**Do**
- Keep objects short-lived, or keep them alive for the whole process. The costly case is "medium-lived" objects, which survive into gen 2 and then become unused.
- Pool or reuse large buffers instead of allocating them repeatedly ([C# › Span, Memory, and pooled buffers](./csharp-performance.md#spant-memoryt-and-pooled-buffers)).
- Choose the GC mode in the project file, and record why:

```xml
<PropertyGroup>
  <!-- A batch tool that processes large files: use Server GC on all cores. -->
  <ServerGarbageCollection>true</ServerGarbageCollection>
  <!-- Turn DATAS off, so the GC starts with one heap per core instead of growing slowly. -->
  <GarbageCollectionAdaptationMode>0</GarbageCollectionAdaptationMode>
</PropertyGroup>
```

- Use `GCSettings.LatencyMode = GCLatencyMode.SustainedLowLatency` only around short sections that must not pause. Then restore the previous mode. It only has an effect when concurrent GC is on (the default). For a short section that must have no GC at all, use `GC.TryStartNoGCRegion`.

**Do not**
- Call `GC.Collect()` in production code.
- Add finalizers. Objects with a finalizer survive at least one extra collection. Use `SafeHandle` instead ([.NET › Native interop](./dotnet-best-practices.md#native-interop)).
- Tune GC settings without a before/after measurement.

---

## The JIT compiler, tiering, and PGO

The JIT (just-in-time) compiler compiles .NET code to machine code **at run time**.

- **Tiered compilation.** The JIT first compiles a method quickly with few optimisations, so the app starts fast. If the app calls the method often, the JIT compiles it again with full optimisations.
- **Dynamic PGO** (profile-guided optimisation). While the first version runs, the runtime records how the app uses the method, for example which concrete type is behind an interface. The JIT then builds the optimised version for that real use.
- **Escape analysis.** The .NET 10 JIT can place some small objects that never leave a method on the stack instead of the heap. This is an extra benefit. Do not make your design depend on it.

**Do**
- Leave tiered compilation and dynamic PGO **on** (the defaults). They are a large part of why modern .NET is fast.
- Expect the first calls to be slower. Warm up the code before you measure.
- Use **ReadyToRun** (`<PublishReadyToRun>true</PublishReadyToRun>`) when startup time matters. The publish step precompiles the code, and the JIT still optimises hot methods again later.

**Do not**
- Turn off tiered compilation to "make it faster". It usually slows startup and removes PGO.
- Draw conclusions from a benchmark that runs a method only a few times.

---

## Startup and deployment

| Option | What it does | Good for | Trade-off |
|--------|--------------|----------|-----------|
| **Framework-dependent** (default) | Uses the .NET runtime installed on the machine | Most apps | Requires the runtime on the target |
| **Self-contained** | Ships the runtime with the app | Machines you do not control | Larger download |
| **ReadyToRun** | Precompiles code at publish time | Faster startup, still fully dynamic | Larger files |
| **Native AOT** | Compiles the whole app to a native executable; no JIT at run time | CLI tools, small services, fastest startup and lowest memory | No runtime code generation. Libraries that use much reflection can fail. Every dependency must be trim-safe and AOT-safe. |
| **Trimming** | Removes unused code from the published output | Smaller self-contained and AOT apps | Trimming can remove code that only reflection reaches |

**Do**
- Treat trim and AOT warnings as errors in projects that publish with trimming or AOT. A warning means that something can fail at run time.
- Use source generators (JSON, logging, regex, configuration binding) instead of reflection. They work with trimming and AOT, and they are also faster.
- Set `<InvariantGlobalization>true</InvariantGlobalization>` for tools that do not need culture-specific behaviour. It removes the ICU dependency and speeds up startup. It also changes culture-aware comparison, upper/lower-casing, and the sort order of non-ASCII text. Thus, test with real input first.

**Do not**
- Turn on Native AOT in a library project. AOT is a property of the final app. Libraries must only declare `<IsAotCompatible>true</IsAotCompatible>` and fix the warnings it reports.

---

## Threading and the thread pool

.NET runs most background work and `async` continuations on a shared **thread pool**. If its threads cannot continue, all work in the process becomes slower.

**Do**
- Keep I/O asynchronous at all levels in services, because blocked threads use all the threads in the pool ([C# › Async/await](./csharp-best-practices.md#asyncawait)). A CPU-bound batch tool that reads one file at a time can use sync reads. They have the same speed or are faster ([File I/O › Sync or async](./file-io-performance.md#sync-or-async)).
- Use `Parallel.ForEachAsync` with a `MaxDegreeOfParallelism` to process many independent items, such as many files.
- Use `Parallel.For` / `Parallel.ForEach` for CPU-heavy work on in-memory data.
- Use `Channel<T>` to connect a producer (for example, a file reader) with one or more consumers (for example, parsers). Use a **bounded** channel, so that a fast producer cannot fill all memory.
- Use the `System.Threading.Lock` type for `lock` statements. It is faster than locking on a plain `object`.
- Keep locked sections very small. While you hold a lock, do not `await`, do I/O, or call unknown code.
- Use `Interlocked` for simple shared counters.

```csharp
var channel = Channel.CreateBounded<string>(new BoundedChannelOptions(10_000)
{
    SingleWriter = true,
    FullMode = BoundedChannelFullMode.Wait,
});
```

**Do not**
- Block on async code with `.Result` or `.Wait()`. It holds a pool thread, and the pool can have no free threads as a result.
- Start more parallel work than there are cores for CPU-bound work. Extra threads only add switching overhead.
- Share mutable state between parallel workers without synchronisation. Give each worker its own state and merge the results at the end.

**Why:** When the thread pool has no free threads, everything is slow but the CPU is idle. `dotnet-counters` shows it as a growing *ThreadPool Queue Length*.

---

## File and stream I/O

These are the basics. Measured comparisons, parallel reads, memory-mapped files, and durable writes are in [File I/O Performance](./file-io-performance.md).

**Do**
- Read large files **as a stream**, line by line or block by block. Do not load the whole file into memory with `File.ReadAllText` or `File.ReadAllLines`.
- Pass `FileOptions.SequentialScan` when you read a file from start to end. It tells the operating system to read ahead.
- Read in large pieces (64 KB or more). The default 4 KB buffer causes many small system calls. When you read into your own large buffer, or through a `StreamReader` with its own large buffer, set `BufferSize = 0` on the `FileStream`. This prevents a second copy of the data.
- Use `System.IO.Pipelines` for byte streams that arrive in pieces you do not control, such as network or decompression streams. For a synchronous file parser, a plain read loop has the same speed and is simpler ([File I/O › Pipelines](./file-io-performance.md#systemiopipelines)).
- Use `RandomAccess` for reads at known positions without a shared file position.
- Choose `CompressionLevel.Fastest` for compressed output that a program reads soon after. Choose `Optimal` or `SmallestSize` only when file size matters more than time.

```csharp
await using var stream = new FileStream(path, new FileStreamOptions
{
    Mode = FileMode.Open,
    Access = FileAccess.Read,
    Share = FileShare.Read,
    Options = FileOptions.Asynchronous | FileOptions.SequentialScan,
    BufferSize = 0, // the StreamReader below has its own 64 KB buffer
});
using var reader = new StreamReader(stream, Encoding.UTF8, detectEncodingFromByteOrderMarks: false, bufferSize: 64 * 1024);
while (await reader.ReadLineAsync(cancellationToken) is { } line)
{
    // ...
}
```

This is the simple option: one `string` per line. It is about 3× slower than working on bytes. For hot paths, see [File I/O › Work on bytes, not strings](./file-io-performance.md#work-on-bytes-not-strings).

**Do not**
- Open and close the same file repeatedly in a loop.
- Use `MemoryStream` as a growing buffer for large data. It reallocates and copies as it grows. Use pooled buffers or `RecyclableMemoryStream` (from the `Microsoft.IO.RecyclableMemoryStream` NuGet package) instead.

---

## Serialization

**Do**
- Use `System.Text.Json` with **source generation** (`JsonSerializerContext`). It avoids reflection at run time and works with trimming and AOT.
- Create `JsonSerializerOptions` **once** and reuse it. A new instance for each call removes its internal caches.
- Serialize directly to the output stream (`JsonSerializer.SerializeAsync(stream, ...)`). Do not build a string first.
- Use `JsonSerializer.DeserializeAsyncEnumerable` to stream large JSON arrays one item at a time.
- Use `Utf8JsonWriter` / `Utf8JsonReader` directly for the hottest paths, such as writing millions of records.

```csharp
[JsonSerializable(typeof(LogEntry))]
[JsonSourceGenerationOptions(PropertyNamingPolicy = JsonKnownNamingPolicy.CamelCase)]
internal sealed partial class LogJsonContext : JsonSerializerContext;

await JsonSerializer.SerializeAsync(output, entry, LogJsonContext.Default.LogEntry, cancellationToken);
```

**Do not**
- Use `Newtonsoft.Json` in new code. `System.Text.Json` is faster and allocates less.
- Serialize to a `string` and then encode it to bytes. Write UTF-8 directly.

---

## HTTP

**Do**
- Use `IHttpClientFactory` or one long-lived `HttpClient` ([.NET › Hosted services, time, and HTTP](./dotnet-best-practices.md#hosted-services-time-and-http)). The client then reuses connections.
- Use `HttpCompletionOption.ResponseHeadersRead` for large responses, and read the body as a stream.
- Set `PooledConnectionLifetime` on long-lived handlers, so that they see DNS changes.
- Turn on response compression for large text payloads.

**Do not**
- Create a new `HttpClient` per request. It wastes connections and can use all available sockets.
- Read a large response with `ReadAsStringAsync` when you only need to parse it once.

---

## Caching and pooling

**Do**
- Cache results that are expensive to compute and read often. Give every cache a **size limit** and an **expiry**. Without them, the cache is a memory leak.
- Use `HybridCache` when you need both an in-memory and a distributed cache. It also prevents many callers from computing the same missing value at the same time.
- Use `ObjectPool<T>` (from `Microsoft.Extensions.ObjectPool`) for objects that are expensive to create and safe to reset, such as large `StringBuilder` instances.

**Do not**
- Pool cheap objects. Pooling has its own cost, and the GC handles small short-lived objects very well.
- Cache data without a decision about when it becomes old.

---

## Logging cost

**Do**
- Use source-generated logging (`[LoggerMessage]`) ([.NET › Logging](./dotnet-best-practices.md#logging)). It does no work when the log level is off.
- Guard expensive log arguments with `logger.IsEnabled(level)`.
- Log per batch or per file in hot loops, not per item.

**Do not**
- Log at `Information` or above inside a loop that runs per record.

---

## Runtime settings reference

Set these in the app's project file. Change them only with a measurement that shows the benefit.

| Setting | Default | When to change |
|---------|---------|----------------|
| `ServerGarbageCollection` | `false` (`true` for ASP.NET Core) | Turn on for throughput-heavy batch tools and services on multi-core machines. |
| `GarbageCollectionAdaptationMode` | not set (DATAS is on when Server GC is on) | Set to `0` for batch jobs where use of all cores from the start is more important than low memory use. |
| `ConcurrentGarbageCollection` | `true` | Turn off only for pure batch jobs where pause times do not matter. |
| `TieredCompilation` | `true` | Leave on. |
| `TieredPGO` | `true` | Leave on. |
| `PublishReadyToRun` | `false` | Turn on when startup time matters. |
| `PublishAot` | `false` | Turn on for CLI tools and small services where startup and memory matter, and all dependencies support AOT. |
| `InvariantGlobalization` | `false` | Turn on when the app never needs culture-specific formatting. |

---

## Review checklist

- [ ] BenchmarkDotNet results or a trace from a Release build support the claim "this is faster".
- [ ] Code streams large files and does not load them whole. Code pools large buffers.
- [ ] No `GC.Collect()`, no finalizers. A measurement and an explanation support all changes to GC settings.
- [ ] Tiered compilation and PGO stay on. Trim/AOT warnings are errors where the project uses trimming or AOT.
- [ ] No blocking on async code. Parallel work has a bounded degree of parallelism. Channels are bounded.
- [ ] JSON uses source-generated `System.Text.Json` with reused options.
- [ ] The code reuses `HttpClient` through the factory.
- [ ] Every cache has a size limit and an expiry.
- [ ] Hot loops do not log per item.
