"""The entry point of the xat-rag service.

`harden_process()` runs first, before any other xat-rag import. This module
imports only the standard library, `xat_rag_offline` and `xat_rag_service.paths` at the top.
"""

import multiprocessing
import os
import sys

from xat_rag_offline import harden_process

from xat_rag_service.paths import release_folder, resolve_data_folder


def main() -> None:
    """Harden the process, then start the service. Exit with a message on a startup failure."""
    data = resolve_data_folder(os.environ, release_folder())
    if data is None:
        print("xat-rag: set XatRag__DataFolder.", file=sys.stderr)
        sys.exit(2)
    import sqlite_vec  # noqa: PLC0415  # pyright: ignore[reportMissingTypeStubs]

    extension = str(sqlite_vec.loadable_path())  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
    suffixes = (".dylib", ".so", ".dll")

    def on_block(event: str) -> None:  # The event name only, never the address or the data.
        print(f"xat-rag: the network guard refused '{event}'.", file=sys.stderr)

    harden_process(data, allowed_extensions=[extension, *(extension + s for s in suffixes)], on_block=on_block)

    from xat_rag_service.run import run  # noqa: PLC0415 - after the hardening, by design.

    sys.exit(run())


if __name__ == "__main__":
    multiprocessing.freeze_support()  # A library can start a helper process in the frozen service.
    main()
