import json
from pathlib import Path

from xat_rag_documents.docling_parsers import LAYOUT_REVISION, TABLE_REVISION

LOCK = Path(__file__).resolve().parents[3] / "build" / "models.lock.json"


def test_the_parser_version_names_the_locked_layout_models() -> None:
    revisions = {model["id"]: model["revision"] for model in json.loads(LOCK.read_text())["models"]}
    assert revisions["docling-project--docling-layout-heron"].startswith(LAYOUT_REVISION)
    assert revisions["docling-project--docling-models"].startswith(TABLE_REVISION)
