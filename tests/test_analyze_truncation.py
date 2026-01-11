from __future__ import annotations

from pathlib import Path

import pytest

from voxnote import analyze
from voxnote.models import AppConfig, LLMConfig, PathsConfig, PromptsConfig, TranscriptionConfig


def _make_config(root: Path) -> AppConfig:
    paths = PathsConfig(
        input=root / "input",
        output=root / "output",
        archive=root / "archive",
    )
    paths.input.mkdir(parents=True, exist_ok=True)
    paths.output.mkdir(parents=True, exist_ok=True)
    paths.archive.mkdir(parents=True, exist_ok=True)

    return AppConfig(
        paths=paths,
        transcription=TranscriptionConfig(model="test-model"),
        llm=LLMConfig(model="test-llm"),
        prompts=PromptsConfig(system_prompt="Test system prompt"),
    )


def test_truncate_note_text_raises_when_reserved_exceeds_max_tokens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _make_config(tmp_path)

    def fake_count_tokens(_config: AppConfig, *, prompt: str) -> int:
        return 1

    monkeypatch.setattr(analyze, "_count_tokens_with_fallback", fake_count_tokens)

    with pytest.raises(ValueError, match=r"Context window too small"):
        analyze._truncate_note_text(config, note_text="hello", max_tokens=10)


def test_truncate_note_text_raises_when_marker_exceeds_available_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _make_config(tmp_path)
    note_text = "some note"
    truncation_marker = "\n[... text truncated due to context limit ...]"

    def fake_count_tokens(_config: AppConfig, *, prompt: str) -> int:
        if prompt == config.prompts.system_prompt:
            return 1
        if prompt == analyze.USER_PROMPT_PREFIX:
            return 1
        if prompt == truncation_marker:
            return 50
        if prompt == note_text:
            return 100
        return 1

    monkeypatch.setattr(analyze, "_count_tokens_with_fallback", fake_count_tokens)

    with pytest.raises(ValueError, match=r"truncation marker"):
        analyze._truncate_note_text(config, note_text=note_text, max_tokens=520)

