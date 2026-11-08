"""The entry point of the xat-rag service.

`harden_process()` runs first, before any other xat-rag or LangChain import (ADR 0011). This module
imports only the standard library and `xat_rag_offline` at the top.
"""

import os
import sys
from pathlib import Path

from xat_rag_offline import harden_process


def _setting(name: str) -> str | None:
    wanted = f"XATRAG__{name}".upper()
    return next((value for key, value in os.environ.items() if key.upper() == wanted), None)


def main() -> None:
    """Harden the process, then start the service. Exit with a message on a startup failure."""
    data = _setting("DataFolder")
    if not data:
        sys.exit("xat-rag: set XatRag__DataFolder.")
    import sqlite_vec  # noqa: PLC0415  # pyright: ignore[reportMissingTypeStubs]

    extension = str(sqlite_vec.loadable_path())  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
    suffixes = (".dylib", ".so", ".dll")

    def on_block(event: str) -> None:  # The event name only, never the address or the data (ADR 0011).
        print(f"xat-rag: the network guard refused '{event}'.", file=sys.stderr)

    hardening = harden_process(
        Path(data), allowed_extensions=[extension, *(extension + s for s in suffixes)], on_block=on_block
    )

    from xat_rag_service.run import run  # noqa: PLC0415 - after the hardening, by design.

    sys.exit(run(hardening))


if __name__ == "__main__":
    main()
