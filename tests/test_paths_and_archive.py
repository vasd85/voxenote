from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from freezegun import freeze_time

from voxnote.models import (
    AppConfig,
    LLMConfig,
    NoteAnalysis,
    PathsConfig,
    ProcessingConfig,
    PromptsConfig,
    TranscriptionConfig,
    TranscriptionResult,
)
from voxnote.organize import organize_note
from voxnote.state import compute_file_hash
from voxnote.vad_trim import get_trimmed_cache_path


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
        processing=ProcessingConfig(),
        prompts=PromptsConfig(system_prompt="Test system prompt"),
    )


def test_trimmed_cache_path_deterministic(tmp_path: Path) -> None:
    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"audio-bytes")

    state_dir = tmp_path / ".voxnote"
    file_hash = compute_file_hash(audio)
    first = get_trimmed_cache_path(original_hash=file_hash, state_dir=state_dir)
    second = get_trimmed_cache_path(original_hash=file_hash, state_dir=state_dir)

    assert first == second
    assert first.name == f"{file_hash}.wav"
    assert first.parent == state_dir / "trimmed"


@freeze_time("2024-01-01 12:00:00")
def test_archive_mode_auto_moves_input(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    audio_in_input = config.input_dir / "note.m4a"
    audio_in_input.write_text("data", encoding="utf-8")

    transcription = TranscriptionResult(audio_path=audio_in_input, text="hello")
    analysis = NoteAnalysis(title="Title", category="Category")

    ctx = organize_note(
        config=config,
        transcription=transcription,
        analysis=analysis,
        recorded_at=datetime.now(UTC),
        source_audio_path=audio_in_input,
    )

    assert not audio_in_input.exists()
    assert ctx.paths.audio_archive_path.exists()
    assert ctx.paths.audio_archive_path.parent == config.archive_dir
    assert ctx.paths.note_path.exists()


@freeze_time("2024-01-01 12:00:00")
def test_archive_mode_auto_moves_external(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    external_audio = tmp_path / "external.m4a"
    external_audio.write_text("data", encoding="utf-8")

    transcription = TranscriptionResult(audio_path=external_audio, text="hello")
    analysis = NoteAnalysis(title="Title", category="Category")

    ctx = organize_note(
        config=config,
        transcription=transcription,
        analysis=analysis,
        recorded_at=datetime.now(UTC),
        source_audio_path=external_audio,
    )

    assert not external_audio.exists()
    assert ctx.paths.audio_archive_path.exists()
    assert ctx.paths.audio_archive_path.read_text(encoding="utf-8") == "data"
    assert ctx.paths.note_path.exists()
