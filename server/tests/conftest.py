"""Shared pytest fixtures.

Tests must never read the developer's real ``.env`` or their inherited shell
environment. Otherwise a local setting silently changes what the test run
proves, and a green suite on one machine says nothing about another.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.config import Settings, get_settings, reload_settings

# Derived from the model so a new setting is isolated automatically and cannot
# leak in from the developer's shell.
_SETTINGS_ENV_VARS = sorted(
    {str(field.alias or name).upper() for name, field in Settings.model_fields.items()}
)


@pytest.fixture(autouse=True, scope="session")
def isolated_env_file(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Point ``$ULTRON_ENV_FILE`` at an empty file for the whole run.

    An explicit file takes precedence over the default ``.env`` lookup, so the
    repository's own ``.env`` is never read by a test.
    """
    env_file = tmp_path_factory.mktemp("ultron-env") / ".env"
    env_file.write_text("", encoding="utf-8")
    previous = os.environ.get("ULTRON_ENV_FILE")
    os.environ["ULTRON_ENV_FILE"] = str(env_file)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("ULTRON_ENV_FILE", None)
        else:
            os.environ["ULTRON_ENV_FILE"] = previous


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
