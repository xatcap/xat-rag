# SOLID Principles

SOLID is five design principles that make code easy to change. They are **general guides, not laws**. Apply them when they reduce the cost of change. Stop when they only add indirection. Each section below shows what too much use of the principle looks like.

---

## Single Responsibility Principle (SRP)

> A module should have one, and only one, **reason to change**. Put differently, it should be responsible to one actor.

**Symptoms of a violation**
- The class name contains "And", "Manager", "Processor", or "Helper", and the class does many unrelated things.
- The class has many dependencies (more than about 5 constructor parameters), or more than a few hundred lines.
- Unrelated changes (a protocol detail, a business rule, a log format) all touch the same file.
- It is hard to test, because a test for one behaviour must set up everything.

```csharp
// ❌ One class: transport, protocol parsing, business rules, persistence, and logging
public class DeviceController
{
    private SerialPort _port = new("COM3");
    public void Process(Job job)
    {
        _port.Open();
        _port.Write(BuildFrame(job));
        var reply = _port.ReadLine();
        if (reply.StartsWith("ERR")) File.AppendAllText("errors.txt", reply);
        File.WriteAllText("counter.txt", (++_count).ToString());
    }
}
```

```csharp
// ✅ Each type has one reason to change
public sealed class ProcessJobHandler(IDeviceClient device, IJobCounter counter, ILogger<ProcessJobHandler> logger)
{
    public async Task<Result<Receipt>> HandleAsync(Job job, CancellationToken ct)
    {
        var result = await device.ExecuteAsync(job, ct);   // transport + protocol live behind IDeviceClient
        if (!result.IsSuccess)
        {
            logger.LogWarning("Job {JobId} failed: {Error}", job.Id, result.Error);
            return result;
        }
        await counter.IncrementAsync(ct);                  // persistence lives behind IJobCounter
        return result;
    }
}
```

**Too much use:** one-method classes everywhere, or separate classes for code that always changes together. The goal is *cohesion*, not very small classes.

---

## Open/Closed Principle (OCP)

> Software entities should be **open for extension** but **closed for modification**.

To add a new variant (a new device model, a new export format), **add** code. Do not edit a growing `if`/`switch` across the codebase.

```csharp
// ❌ Every new model edits this method, and probably several others like it
public decimal CalculateFee(Order order) => order.Type switch
{
    "standard" => order.Total * 0.02m,
    "express"  => order.Total * 0.05m + 3,
    _ => throw new NotSupportedException()
};
```

```csharp
// ✅ New behaviour = new class + one registration
public interface IFeePolicy
{
    string OrderType { get; }
    decimal Calculate(Order order);
}

public sealed class ExpressFeePolicy : IFeePolicy
{
    public string OrderType => "express";
    public decimal Calculate(Order order) => order.Total * 0.05m + 3;
}

public sealed class FeeCalculator(IEnumerable<IFeePolicy> policies)
{
    private readonly FrozenDictionary<string, IFeePolicy> _byType =
        policies.ToFrozenDictionary(p => p.OrderType);

    public decimal Calculate(Order order) => _byType[order.Type].Calculate(order);
}
```

Common mechanisms: Strategy, Decorator, plug-ins resolved from DI, events, and pipeline behaviours.

**Too much use:** extension points that nothing needs yet. A single `switch` over a closed set of cases (for example, the values of an enum you own) is correct. Add the abstraction when the **second or third** variant is necessary ([YAGNI](#yagni-you-arent-gonna-need-it)).

---

## Liskov Substitution Principle (LSP)

> Objects of a subtype must be usable wherever the base type is expected, **without breaking correctness**.

A subtype must honour the base type's contract. It must not strengthen preconditions, weaken postconditions, or throw unexpected exceptions.

**Symptoms of a violation**
- Overrides that throw `NotImplementedException` or `NotSupportedException`.
- Callers check the concrete type (`if (x is SpecialThing)`) before calling a base method.
- A "mock" or "null" implementation silently returns success for operations that fail on real hardware. Thus, tests pass, but production fails.

```csharp
// ❌ Read-only device pretends to be a full device
public class ReadOnlyScanner : IDevice
{
    public Task<Image> ScanAsync(CancellationToken ct) => ...;
    public Task PrintAsync(Document d, CancellationToken ct) => throw new NotSupportedException();
}
```

```csharp
// ✅ Model capabilities honestly (see also ISP)
public interface IScanner { Task<Image> ScanAsync(CancellationToken ct); }
public interface IPrinter { Task PrintAsync(Document document, CancellationToken ct); }

public sealed class MultiFunctionDevice : IScanner, IPrinter { /* ... */ }
public sealed class FlatbedScanner : IScanner { /* ... */ }
```

**Tip:** run the same **contract test suite** against every implementation of an interface, including fakes. This proves that you can substitute them.

---

## Interface Segregation Principle (ISP)

> Clients should not be forced to depend on methods they do not use.

**Symptoms of a violation**
- An interface has many members, and most callers use only two or three.
- Implementations leave half of the members empty.
- A change to one member forces unrelated consumers to recompile or retest.

```csharp
// ❌ Fat interface
public interface IDeviceService
{
    bool Connect();
    void Disconnect();
    Status GetStatus();
    void Calibrate(int offset);
    Image Scan();
    void ClearTray();
    int GetTrayCount();
    bool IsTrayFull();
    // ...
}
```

```csharp
// ✅ Role interfaces, shaped by what clients need
public interface IDeviceConnection { Task OpenAsync(CancellationToken ct); Task CloseAsync(CancellationToken ct); }
public interface IDeviceStatus     { Task<DeviceStatus> GetStatusAsync(CancellationToken ct); }
public interface IOutputTray       { Task<TrayState> GetStateAsync(CancellationToken ct); Task ClearAsync(CancellationToken ct); }
```

One class can still implement several role interfaces. ISP is about **what consumers see**, not about how many classes you write.

**Too much use:** one-method interfaces for everything, or interfaces with no consumer that needs the separation.

---

## Dependency Inversion Principle (DIP)

> High-level modules should not depend on low-level modules; both should depend on **abstractions**. Abstractions should not depend on details.

The **policy** (business/use-case logic) owns the interface. The **detail** (database, serial port, HTTP, vendor SDK) implements it. This inverts the usual direction of source-code dependencies. It is the base of [Clean Architecture](./clean-architecture.md#the-dependency-rule).

```csharp
// ❌ Use case depends on a concrete detail and creates it itself
public sealed class StampDocumentHandler
{
    private readonly VendorStamperDriver _driver = new("COM3");
    // Untestable without hardware, and changing vendors means rewriting the use case
}
```

```csharp
// ✅ The application layer defines what it needs...
public interface IStamper
{
    Task<Result<StampReceipt>> StampAsync(StampRequest request, CancellationToken ct);
}

public sealed class StampDocumentHandler(IStamper stamper)
{
    public Task<Result<StampReceipt>> HandleAsync(StampRequest request, CancellationToken ct)
        => stamper.StampAsync(request, ct);
}

// ...and infrastructure provides it
internal sealed class VendorStamperAdapter(IOptions<StamperOptions> options) : IStamper { /* ... */ }
```

DIP (a design principle) is not the same as DI (a technique for supplying dependencies). For container mechanics, see [.NET › Dependency injection](./dotnet-best-practices.md#dependency-injection).

**Too much use:** an interface for every class. Abstract **volatile** or **external** dependencies (I/O, hardware, time, randomness, third-party APIs) and the seams you need for testing. Stable, pure in-process code (value objects, pure functions) does not need an interface.

---

## Related principles

### DRY (Don't Repeat Yourself)
Each piece of **knowledge** (a business rule, a protocol constant, a validation rule) must have one authoritative representation.

DRY is about knowledge, **not about code that looks the same by chance**. Keep two similar snippets separate if they change for different reasons. If you merge them, you couple unrelated concerns. A general rule: accept duplication until the third occurrence, then extract the code.

### KISS (Keep It Simple)
Choose the simplest design that meets the current requirements. Complex code costs much to read, debug, and give to another developer. Write for the reader.

### YAGNI (You Aren't Gonna Need It)
Do not build features, configuration switches, or abstractions for possible future needs. They cost time now and add permanent complexity. Good structure (SRP, DIP) makes it cheap to add things *when* they become necessary.

### Composition over inheritance
Build behaviour from small objects (Strategy, Decorator), not from deep class hierarchies. Inheritance couples a subclass to the parent's implementation details. Limit hierarchies to one or two levels, and prefer interfaces with `sealed` implementations.

```csharp
// ✅ Add retry behaviour by decoration, not by subclassing
public sealed class RetryingStamper(IStamper inner, ResiliencePipeline pipeline) : IStamper
{
    public Task<Result<StampReceipt>> StampAsync(StampRequest request, CancellationToken ct)
        => pipeline.ExecuteAsync(token => new ValueTask<Result<StampReceipt>>(inner.StampAsync(request, token)), ct).AsTask();
}
```

### Law of Demeter (principle of least knowledge)
A method must talk to its immediate collaborators, and not go through them. For example, `order.Customer.Address.City.Name` couples you to four types. Ask the nearest object for what you need, or pass it in directly.

### Tell, Don't Ask
Tell objects what to do. Do not read their state and decide for them. This keeps behaviour next to the data that it protects.

```csharp
// ❌ Ask
if (tray.Count >= tray.Capacity) tray.IsFull = true;

// ✅ Tell
tray.Add(page); // Tray enforces its own capacity invariant and raises TrayFull
```

---

## Review checklist

- [ ] Each class has one reason to change, and its name says what that is.
- [ ] A new variant needs a new class, and no edits to switches in several places.
- [ ] Implementations honour their interface's contract: no `NotSupportedException` stubs and no type checks by callers.
- [ ] Interfaces are small and shaped by their consumers.
- [ ] Business logic depends on abstractions it owns, and I/O, hardware, time, and vendor SDKs sit behind them.
- [ ] No speculative abstractions, and no interface without a reason (a volatile dependency or a test seam).
- [ ] The change removes duplication only where it represents the same knowledge.
