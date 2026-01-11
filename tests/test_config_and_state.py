from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from freezegun import freeze_time

from voxnote.config import load_config
from voxnote.state import (
    FailedTranscriptionEntry,
    ProcessedAudioEntry,
    append_failed_transcription_entry,
    append_processed_entry,
    get_failed_transcription_text,
    load_processed_hashes,
    purge_failed_transcription,
)


def test_load_config_expands_paths(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        """
paths:
  input: ~/test_input
  output: ./out_dir
  archive: out_archive

transcription:
  model: test-model
  language: auto

llm:
  model: test-llm
  base_url: http://localhost:11434

prompts:
  system_prompt: Test system prompt
        """,
        encoding="utf-8",
    )

    config = load_config(cfg_path)

    assert config.input_dir.is_absolute()
    assert str(config.input_dir).startswith(str(Path.home()))
    assert config.output_dir == (cfg_path.parent / "out_dir").resolve()
    assert config.archive_dir == (cfg_path.parent / "out_archive").resolve()


@freeze_time("2024-01-01 12:00:00")
def test_state_append_and_purge(tmp_path: Path) -> None:
    state_dir = tmp_path / ".voxnote"

    entry = ProcessedAudioEntry(
        processed_at=datetime.now(UTC),
        original_hash="abc",
        original_name="file.wav",
        original_path="/tmp/file.wav",
        archive_path="/tmp/archive/file.wav",
        note_path="/tmp/output/note.md",
    )
    append_processed_entry(entry, state_dir=state_dir)

    hashes = load_processed_hashes(state_dir=state_dir)
    assert "abc" in hashes

    failed = FailedTranscriptionEntry(
        created_at=datetime.now(UTC),
        audio_path="/tmp/file.wav",
        text="hello",
        error="boom",
    )
    append_failed_transcription_entry(failed, state_dir=state_dir)
    assert get_failed_transcription_text(Path("/tmp/file.wav"), state_dir=state_dir) == "hello"

    purge_failed_transcription(Path("/tmp/file.wav"), state_dir=state_dir)
    assert get_failed_transcription_text(Path("/tmp/file.wav"), state_dir=state_dir) is None
