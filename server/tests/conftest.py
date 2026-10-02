"""Shared pytest fixtures.

Tests must never read the developer's real ``.env`` or their inherited shell
environment. Otherwise a local setting silently changes what the test run
proves, and a green suite on one machine says nothing about another.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.config import Settings, get_settings, reload_settings

# Derived from the model so a new setting is isolated automatically and cannot
# leak in from the developer's shell.
_SETTINGS_ENV_VARS = sorted(
    {str(field.alias or name).upper() for name, field in Settings.model_fields.items()}
)

# Isolation is installed at *import* time, not only in a fixture, and that
# ordering matters.
#
# `app.database.models.memories` chooses the type of `memories.embedding` while
# the module is being imported, by asking `get_settings()`. Test modules are
# collected -- and therefore imported -- before any test-scoped fixture runs, so
# an `autouse` fixture that clears the environment is already too late: the
# column type would be decided by the developer's real `.env` while every test
# body then observed default settings. The two disagreed, and the suite's result
# depended on whether the developer happened to have a `.env`.
#
# A conftest module is imported before collection, so setting the variable here
# covers both the import-time reads and the tests themselves. The empty file is
# created eagerly rather than in `tmp_path_factory` because collection may need
# it before any fixture exists.
_ISOLATED_ENV_FILE = Path(tempfile.gettempdir()) / "ultron-tests-isolated.env"
_ISOLATED_ENV_FILE.write_text("", encoding="utf-8")
_PREVIOUS_ENV_FILE = os.environ.get("ULTRON_ENV_FILE")
os.environ["ULTRON_ENV_FILE"] = str(_ISOLATED_ENV_FILE)


@pytest.fixture(autouse=True, scope="session")
def isolated_env_file() -> Iterator[None]:
    """Keep ``$ULTRON_ENV_FILE`` pointing at an empty file for the whole run.

    An explicit file takes precedence over the default ``.env`` lookup, so the
    repository's own ``.env`` is never read during collection or by a test. The
    variable is already set at import time (see above); this fixture exists to
    restore whatever the developer had afterwards.
    """
    os.environ["ULTRON_ENV_FILE"] = str(_ISOLATED_ENV_FILE)
    try:
        yield
    finally:
        if _PREVIOUS_ENV_FILE is None:
            os.environ.pop("ULTRON_ENV_FILE", None)
        else:
            os.environ["ULTRON_ENV_FILE"] = _PREVIOUS_ENV_FILE


@pytest.fixture(autouse=True)
def clean_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Start every test on default settings and leave no cached settings behind."""
    for name in _SETTINGS_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    reload_settings()
    yield
    # Only the cache is dropped here. This teardown runs before monkeypatch
    # restores the environment, so a test that deliberately set an invalid value
    # would make a rebuild fail; clearing the cache is all that is needed.
    get_settings.cache_clear()


@pytest.fixture
def repo_root() -> Path:
    """The repository root, derived from the package location."""
    return Path(__file__).resolve().parents[2]
