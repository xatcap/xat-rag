"""Tests of the service paths and their defaults in the frozen service."""

import os
import sys
from collections.abc import Callable, Mapping
from pathlib import Path

import pytest
from xat_rag_core.errors import ConfigError
from xat_rag_service.paths import (
    read_setting,
    release_folder,
    resolve_data_folder,
    resolve_lock_file,
    resolve_models_folder,
    resolve_ready_file,
    resolve_recipes_folder,
)
from xat_rag_service.settings import read_settings

RELEASE = Path("/opt/xat rag é")


def test_read_setting_keeps_not_set_and_empty_apart() -> None:
    assert read_setting({}, "DataFolder") is None
    assert read_setting({"XatRag__DataFolder": ""}, "DataFolder") == ""
    assert read_setting({"XATRAG__DATAFOLDER": "/d"}, "DataFolder") == "/d"


def test_the_frozen_service_has_a_default_for_each_path() -> None:
    settings = read_settings({}, RELEASE)
    assert settings.data_folder == RELEASE / "data"
    assert settings.recipes_folder == RELEASE / "recipes"
    assert settings.models_folder == RELEASE / "models"
    assert settings.lock_file == RELEASE / "models" / "models.lock.json"
    assert settings.ready_file == RELEASE / "data" / "ready.json"


@pytest.mark.parametrize(
    "resolver",
    [resolve_data_folder, resolve_recipes_folder, resolve_models_folder, resolve_lock_file, resolve_ready_file],
)
def test_no_default_applies_from_source(resolver: Callable[[Mapping[str, str], Path | None], Path | None]) -> None:
    assert resolver({}, None) is None


@pytest.mark.parametrize("name", ["DataFolder", "RecipesFolder", "ModelsFolder", "LockFile"])
def test_the_paths_stay_required_from_source(name: str) -> None:
    environ = {f"XatRag__{n}": "/x" for n in ("DataFolder", "RecipesFolder", "ModelsFolder", "LockFile") if n != name}
    with pytest.raises(ConfigError, match=f"XatRag__{name}"):
        read_settings(environ)


def test_a_set_variable_wins_over_its_default() -> None:
    assert resolve_recipes_folder({"XatRag__RecipesFolder": "/r"}, RELEASE) == Path("/r")
    assert resolve_lock_file({"XatRag__LockFile": "/l.json"}, RELEASE) == Path("/l.json")


def test_an_empty_path_variable_uses_the_default() -> None:
    assert resolve_data_folder({"XatRag__DataFolder": ""}, RELEASE) == RELEASE / "data"


def test_the_lock_file_follows_a_set_models_folder() -> None:
    assert resolve_lock_file({"XatRag__ModelsFolder": "/m"}, RELEASE) == Path("/m/models.lock.json")


def test_the_ready_file_follows_a_set_data_folder() -> None:
    assert resolve_ready_file({"XatRag__DataFolder": "/d"}, RELEASE) == Path("/d/ready.json")


def test_a_set_folder_gives_no_derived_default_from_source() -> None:
    assert resolve_lock_file({"XatRag__ModelsFolder": "/m"}, None) is None
    assert resolve_ready_file({"XatRag__DataFolder": "/d"}, None) is None


def test_an_empty_ready_file_means_no_ready_file() -> None:
    assert resolve_ready_file({"XatRag__ReadyFile": ""}, RELEASE) is None


def test_release_folder_is_none_from_source(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert release_folder() is None


@pytest.mark.skipif(os.name == "nt", reason="symbolic links need rights on Windows")
def test_release_folder_follows_a_symbolic_link(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    release = tmp_path / "release"
    release.mkdir()
    binary = release / "xat-rag-service"
    binary.write_text("")
    link = tmp_path / "link"
    link.symlink_to(binary)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(link))
    assert release_folder() == release.resolve()
