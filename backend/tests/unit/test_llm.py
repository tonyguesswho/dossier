"""Tests for the OpenRouter entry point (no real API calls)."""
from __future__ import annotations

import pytest

from dossier.core.llm import strong_model
from dossier.core.settings import Settings


def test_settings_requires_openrouter_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(
        "dossier.core.settings.Settings.model_config",
        {**Settings.model_config, "env_file": None},
    )
    with pytest.raises(Exception, match="openrouter_api_key|OPENROUTER_API_KEY"):
        Settings()  # type: ignore[call-arg]


def test_strong_model_configures_openrouter_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-fake")
    client = strong_model()
    assert str(client.base_url).rstrip("/") == "https://openrouter.ai/api/v1"
