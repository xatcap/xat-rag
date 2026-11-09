# 0016. Use the release folder for the default paths of the frozen service

- Status: Accepted
- Date: 2026-11-09
- Deciders: Project maintainer
- Related: [ADR 0004](0004-onnx-runtime-and-locked-models.md), [ADR 0011](0011-offline-hardening.md) and [ADR 0015](0015-remove-service-access-key.md).

## Context

The frozen service needed four path settings before it started: `XatRag__DataFolder`, `XatRag__RecipesFolder`, `XatRag__ModelsFolder` and `XatRag__LockFile`. The ready file was optional. Each user had to set the paths before the first start.

The models are not in the release folder. [ADR 0002](0002-python.md) records that the models are a separate file. The lock file lists each model file with its size and sha256. [ADR 0004](0004-onnx-runtime-and-locked-models.md) describes this lock file.

## Decision

- When the service runs as a frozen executable, each path setting without a value uses a default. The defaults are relative to the release folder, which is the folder of the executable:
  - `XatRag__DataFolder` is `<release>/data`.
  - `XatRag__RecipesFolder` is `<release>/recipes`.
  - `XatRag__ModelsFolder` is `<release>/models`.
  - `XatRag__LockFile` is `<ModelsFolder>/models.lock.json`. It follows a set models folder.
  - `XatRag__ReadyFile` is `<DataFolder>/ready.json`. It follows a set data folder.
- A setting with a value wins over its default. A path setting with an empty value uses its default.
- An empty `XatRag__ReadyFile` means that the service writes no ready file.
- From source, no default applies. The four path settings stay required. `XatRag__ReadyFile` stays optional.
- The release folder contains the executable and its libraries, the `recipes/` folder with the default recipes, and empty `data/` and `models/` folders.
- The models are not bundled. The models archive, `xat-rag-models-<tag>.zip`, is a separate file. It holds `models.lock.json` at its root. The user extracts it into `models/`.
- The indexes go into `data/` inside the release folder. A release starts with no setting.
- A read-only installation needs `XatRag__DataFolder` set to a folder that the user can write to. The ready file follows that folder.
- A startup failure exits with code 2. It prints one line that starts with `xat-rag:`.

## Consequences

- A user who extracts a release and the models archive can start the service with no setting.
- The release folder has no models and no data. `check-bundle` and `archive` check this. The smoke test also runs the defaults on a copy that has the models.
- Replacing the release folder deletes `data/` and `models/`. A user must move these folders before the replacement, or send the documents again to build the indexes.
- The frozen service has a ready file by default, so its Unix socket has the mode `0600` by default. An empty `XatRag__ReadyFile` returns to the behaviour of [ADR 0015](0015-remove-service-access-key.md).
- The module that holds the defaults, `xat_rag_service.paths`, runs before the hardening of [ADR 0011](0011-offline-hardening.md). It uses the standard library only. An import rule in `pyproject.toml` checks this.
- A failed start can leave `xat-rag.lock` in `data/`. The next start takes the lock again.
- A bind failure on a busy port in `XatRag__Urls` prints the message of uvicorn. This case does not use the one-line message.
- On Windows, the `set` command deletes a variable with an empty value. A program that starts the service must pass an empty `XatRag__ReadyFile` in the environment of the process.

## Alternatives considered

- **Require all path settings in a release.** This was the behaviour before this decision. Each user had to set the paths before the first start.
- **Bundle the models in the release.** Not chosen. The models stay a separate file, as in the current release layout.
- **Store the indexes in a per-user folder by default.** Not chosen. The default stays in the release folder. An app can set `XatRag__DataFolder` to store the indexes elsewhere.
