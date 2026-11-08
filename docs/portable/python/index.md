# Python Engineering Guidelines

These rules apply to Python code. Like the [.NET](../dotnet/index.md) and [Flutter](../flutter/index.md) pages, they are a **shared baseline**. If a rule does not fit, you can use a different solution, and you must write down why. General principles (SOLID, coupling, ADRs) are in the .NET pages. This page does not repeat them. All text follows the [writing style](../writing-style/writing-style.md).

**Baseline:** Python 3.12+ · `uv` workspaces · `ruff` (lint and format) · `pyright` in strict mode · `pytest` with `pytest-asyncio`.

## Project layout

- **Do** put each package in `packages/<name>/` with a `src/<import_name>/` folder, a `tests/` folder, a `README.md` and a `py.typed` marker.
- **Do** declare optional integrations (for example database drivers, cloud SDKs and browser automation) as extras. Import them inside the function that needs them. A missing extra must raise a clear error that names the extra to install.
- **Do not** let a package import a sibling that its `pyproject.toml` does not list.

## Types and data

- **Do** type every function, parameter and return value. `pyright` runs in strict mode. Turn off its "unknown type" rules only if a dependency does not have full type data, and write down why.
- **Do** use pydantic models for anything read from config or sent over HTTP, and `dataclass` for internal records.
- **Do** keep secrets in `SecretStr` and read them from `${ENV_VAR}` in the configuration file. Never log them or put them in error messages.
- **Do not** use mutable default values or module-level state. Pass settings in explicitly.

## Errors

- **Do** define a small set of error types in one shared package, and raise those types. For example, use `ConfigError` for bad config, `SourceError` for an external source that failed, and `UnsupportedOperationError` for an operation that is not available.
- **Do** write messages a user can act on: what failed, where, and what to do (`Run: my-tool login`).
- **Do not** silently change to another service, data source or index on failure. Report the failure.

## Async and I/O

- **Do** use `async` for network, database and browser work. Use `async with` for all resources that must close.
- **Do not** call blocking functions (`subprocess.run`, `time.sleep`, `requests`) inside `async def`. Use `asyncio.create_subprocess_exec`, `asyncio.sleep` and `httpx.AsyncClient`.
- **Do** run `git` and other tools with an argument list, never through a shell.

## SQL

- **Do** build SQL with the safe tools of your database driver, for example `psycopg.sql` (`Identifier`, `Literal`, placeholders). Table and column names come from validated config or fixed lists. Values are always parameters.

## Documentation and style

- **Do** give every module, public class and public function a docstring that says what it is for, in plain language.
- **Do** keep lines within 120 characters. `ruff format` decides the layout.

## Testing

- **Do** test external integrations without the network. For example, use `respx` for HTTP, real temporary git repositories for git code, and saved pages or XML for browser automation.
- **Do** mark tests that need a real database with `@pytest.mark.integration` and a database fixture. They skip when the environment variable for the test database URL is unset.
- **Do** test pipelines with a scripted fake for each external service. Assert on the data that each fake received, and also on the data that it returned.

## Review checklist

- [ ] The package README and the example configuration file document new config fields.
- [ ] The change adds no silent fallback.
- [ ] Sensitive data passes through redaction before it goes to a remote service.
- [ ] `uv run ruff check`, `uv run ruff format --check`, `uv run pyright` and `uv run pytest` pass.
