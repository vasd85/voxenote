from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Optional

import pytest
from freezegun import freeze_time

from voxnote import workflow as workflow_module
from voxnote.audio_metadata import AudioMetadata
from voxnote.diarize import DIARIZATION_DISABLED_FINGERPRINT, diarization_fingerprint
from voxnote.models import (
    AppConfig,
    DiarizationConfig,
    DiarizationResult,
    LLMConfig,
    NoteAnalysis,
    PathsConfig,
    PromptsConfig,
    SpeakerTurn,
    TranscriptionConfig,
    TranscriptionResult,
    TranscriptSegment,
    TranscriptWord,
)
from voxnote.runtime import RuntimeContext
from voxnote.state import find_processed_entry
from voxnote.workflow import Workflow

TWO_SPEAKER_TURNS = [
    SpeakerTurn(start=0.0, end=1.0, speaker=0),
    SpeakerTurn(start=1.0, end=2.0, speaker=3),
]
ONE_SPEAKER_TURNS = [SpeakerTurn(start=0.0, end=2.0, speaker=0)]

PLAIN_TEXT = "Hello there.\nGeneral Kenobi."
SEGMENTS = [
    TranscriptSegment(
        text=" Hello there.",
        start=0.0,
        end=1.0,
        words=[TranscriptWord(text=" Hello", start=0.0, end=0.5), TranscriptWord(text=" there.", start=0.5, end=1.0)],
    ),
    TranscriptSegment(
        text=" General Kenobi.",
        start=1.0,
        end=2.0,
        words=[
            TranscriptWord(text=" General", start=1.1, end=1.5),
            TranscriptWord(text=" Kenobi.", start=1.5, end=2.0),
        ],
    ),
]


def _make_workflow(tmp_path: Path, **diarization_kwargs) -> Workflow:
    paths = PathsConfig(input=tmp_path / "input", output=tmp_path / "output", archive=tmp_path / "archive")
    for directory in (paths.input, paths.output, paths.archive):
        directory.mkdir(parents=True, exist_ok=True)
    config = AppConfig(
        paths=paths,
        transcription=TranscriptionConfig(model="test-model"),
        llm=LLMConfig(model="test-llm"),
        prompts=PromptsConfig(system_prompt="Test system prompt"),
        diarization=DiarizationConfig(**diarization_kwargs),
    )
    state_dir = tmp_path / ".voxnote"
    state_dir.mkdir(parents=True, exist_ok=True)
    runtime = RuntimeContext(
        config_path=tmp_path / "config.yaml",
        config=config,
        project_root=tmp_path,
        state_dir=state_dir,
    )
    return Workflow(runtime)


class _Recorder:
    """Captures the arguments the workflow passes to its mocked boundaries."""

    def __init__(self) -> None:
        self.transcribe_calls: list[dict] = []
        self.diarize_calls: list[Path] = []
        self.analyze_calls: list[dict] = []


@pytest.fixture
def boundaries(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    recorder = _Recorder()

    def fake_transcribe(config, audio_path, *, state_dir=None, word_timestamps=False):
        recorder.transcribe_calls.append({"audio_path": audio_path, "word_timestamps": word_timestamps})
        return TranscriptionResult(
            audio_path=audio_path,
            text=PLAIN_TEXT,
            segments=list(SEGMENTS) if word_timestamps else [],
        )

    def fake_analyze(config, text, *, state_dir=None, speaker_labeled=False):
        recorder.analyze_calls.append({"text": text, "speaker_labeled": speaker_labeled})
        return NoteAnalysis(title="Title", category="Category", short_summary="Summary")

    def fake_metadata(path):
        return AudioMetadata(
            recorded_at=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
            recorded_at_source="test",
            mdls=None,
            ffprobe=None,
            stat={},
        )

    monkeypatch.setattr(workflow_module, "transcribe_file", fake_transcribe)
    monkeypatch.setattr(workflow_module, "analyze_text", fake_analyze)
    monkeypatch.setattr(workflow_module, "collect_audio_metadata", fake_metadata)
    monkeypatch.setattr(workflow_module, "probe_diarization_engine", lambda: None)
    monkeypatch.setattr(workflow_module, "diarization_models_ready", lambda config, state_dir=None: True)
    monkeypatch.setattr(
        workflow_module,
        "ensure_diarization_models",
        lambda config, state_dir=None: (Path("/models/seg.onnx"), Path("/models/emb.onnx")),
    )
    return recorder


def _install_diarization(monkeypatch: pytest.MonkeyPatch, recorder: _Recorder, turns: list[SpeakerTurn]) -> None:
    def fake_diarize(config, audio_path, *, state_dir=None, progress_callback=None):
        recorder.diarize_calls.append(audio_path)
        return DiarizationResult(
            audio_path=audio_path,
            turns=list(turns),
            speaker_count=len({turn.speaker for turn in turns}),
        )

    monkeypatch.setattr(workflow_module, "diarize_audio", fake_diarize)


def _add_audio(workflow: Workflow, name: str = "note.wav") -> Path:
    audio = workflow.config.input_dir / name
    audio.write_bytes(b"fake-audio-bytes")
    return audio


def _note_text(events) -> str:
    completed = next(event for event in events if event.type == "completed")
    return Path(completed.data["note_path"]).read_text(encoding="utf-8")


@freeze_time("2024-01-01 12:00:00")
def test_disabled_diarization_keeps_todays_behaviour(tmp_path: Path, boundaries: _Recorder) -> None:
    workflow = _make_workflow(tmp_path, enabled=False)
    _add_audio(workflow)

    events = list(workflow.process_files())

    assert boundaries.transcribe_calls[0]["word_timestamps"] is False
    assert boundaries.diarize_calls == []
    assert boundaries.analyze_calls[0]["speaker_labeled"] is False
    assert not any(event.type == "diarized" for event in events)

    note = _note_text(events)
    assert PLAIN_TEXT in note
    assert "Speaker 1:" not in note
    assert "**Speakers:**" not in note


@freeze_time("2024-01-01 12:00:00")
def test_multi_speaker_note_carries_labels_and_reports_counts(
    tmp_path: Path, boundaries: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow = _make_workflow(tmp_path, enabled=True)
    _install_diarization(monkeypatch, boundaries, TWO_SPEAKER_TURNS)
    _add_audio(workflow)

    events = list(workflow.process_files())

    assert boundaries.transcribe_calls[0]["word_timestamps"] is True
    assert boundaries.analyze_calls[0]["speaker_labeled"] is True
    assert boundaries.analyze_calls[0]["text"] == "Speaker 1: Hello there.\n\nSpeaker 2: General Kenobi."

    diarized = next(event for event in events if event.type == "diarized")
    assert diarized.data == {"speakers": 2, "turns": 2}
    assert "2 speaker(s)" in diarized.message

    note = _note_text(events)
    assert "Speaker 1: Hello there." in note
    assert "Speaker 2: General Kenobi." in note
    assert "- **Speakers:** 2" in note


@freeze_time("2024-01-01 12:00:00")
def test_single_speaker_output_is_indistinguishable_from_disabled(
    tmp_path: Path, boundaries: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow = _make_workflow(tmp_path, enabled=True)
    _install_diarization(monkeypatch, boundaries, ONE_SPEAKER_TURNS)
    _add_audio(workflow)

    events = list(workflow.process_files())

    assert boundaries.analyze_calls[0]["speaker_labeled"] is False
    assert boundaries.analyze_calls[0]["text"] == PLAIN_TEXT

    note = _note_text(events)
    assert "Speaker 1:" not in note
    assert "**Speakers:**" not in note

    diarized = next(event for event in events if event.type == "diarized")
    assert diarized.data == {"speakers": 1, "turns": 1}


@freeze_time("2024-01-01 12:00:00")
def test_diarization_runs_on_the_same_source_as_transcription(
    tmp_path: Path, boundaries: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow = _make_workflow(tmp_path, enabled=True)
    _install_diarization(monkeypatch, boundaries, TWO_SPEAKER_TURNS)
    audio = _add_audio(workflow)

    trimmed_dir = workflow.state_dir / "trimmed"
    trimmed_dir.mkdir(parents=True, exist_ok=True)
    from voxnote.state import compute_file_hash

    trimmed = trimmed_dir / f"{compute_file_hash(audio)}.wav"
    trimmed.write_bytes(b"trimmed-audio")

    list(workflow.process_files())

    assert boundaries.transcribe_calls[0]["audio_path"] == trimmed
    assert boundaries.diarize_calls == [trimmed]


@freeze_time("2024-01-01 12:00:00")
def test_enabling_diarization_forces_reprocessing(
    tmp_path: Path, boundaries: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow = _make_workflow(tmp_path, enabled=False)
    audio = _add_audio(workflow)

    list(workflow.process_files())
    entry = find_processed_entry(_hash_of(audio, tmp_path), state_dir=workflow.state_dir)
    assert entry is not None
    assert entry["diarization_fingerprint"] == DIARIZATION_DISABLED_FINGERPRINT

    # Second run without changes: skipped.
    _restore_audio(workflow, audio)
    events = list(workflow.process_files())
    assert any(event.type == "skipped" for event in events)

    # Third run with diarization on: reprocessed.
    _restore_audio(workflow, audio)
    _install_diarization(monkeypatch, boundaries, TWO_SPEAKER_TURNS)
    events = list(workflow.process_files(diarize=True))

    assert any(event.type == "info" and "diarization settings changed" in event.message for event in events)
    summary = next(event for event in events if event.type == "summary")
    assert summary.data["processed"] == 1
    assert "Speaker 1:" in _note_text(events)


@freeze_time("2024-01-01 12:00:00")
def test_legacy_entry_without_fingerprint_is_still_skipped(tmp_path: Path, boundaries: _Recorder) -> None:
    workflow = _make_workflow(tmp_path, enabled=False)
    audio = _add_audio(workflow)

    list(workflow.process_files())

    index_path = workflow.state_dir / "processed_audio.jsonl"
    entries = [json.loads(line) for line in index_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    for entry in entries:
        entry.pop("diarization_fingerprint", None)
        entry.pop("speaker_count", None)
    index_path.write_text(
        "\n".join(json.dumps(entry, ensure_ascii=False) for entry in entries) + "\n", encoding="utf-8"
    )

    _restore_audio(workflow, audio)
    events = list(workflow.process_files())

    assert any(event.type == "skipped" for event in events)
    assert not any(event.type == "completed" for event in events)


@freeze_time("2024-01-01 12:00:00")
def test_failed_analysis_retry_keeps_speaker_labels(
    tmp_path: Path, boundaries: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow = _make_workflow(tmp_path, enabled=True)
    _install_diarization(monkeypatch, boundaries, TWO_SPEAKER_TURNS)
    _add_audio(workflow)

    def failing_analyze(config, text, *, state_dir=None, speaker_labeled=False):
        raise RuntimeError("ollama is down")

    monkeypatch.setattr(workflow_module, "analyze_text", failing_analyze)
    events = list(workflow.process_files())
    assert any(event.type == "error" for event in events)

    stored = json.loads((workflow.state_dir / "failed_transcriptions.jsonl").read_text(encoding="utf-8").strip())
    assert stored["text"].startswith("Speaker 1:")
    assert stored["speaker_count"] == 2

    # Retry: the saved labeled text is reused, nothing is transcribed or diarized again.
    boundaries.transcribe_calls.clear()
    boundaries.diarize_calls.clear()

    def ok_analyze(config, text, *, state_dir=None, speaker_labeled=False):
        boundaries.analyze_calls.append({"text": text, "speaker_labeled": speaker_labeled})
        return NoteAnalysis(title="Title", category="Category", short_summary="Summary")

    monkeypatch.setattr(workflow_module, "analyze_text", ok_analyze)
    events = list(workflow.process_files())

    assert boundaries.transcribe_calls == []
    assert boundaries.diarize_calls == []
    assert boundaries.analyze_calls[-1]["speaker_labeled"] is True
    note = _note_text(events)
    assert "Speaker 1: Hello there." in note
    assert "- **Speakers:** 2" in note


@freeze_time("2024-01-01 12:00:00")
def test_failed_retry_re_transcribes_when_diarization_settings_changed(
    tmp_path: Path, boundaries: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow = _make_workflow(tmp_path, enabled=False)
    _add_audio(workflow)

    def failing_analyze(config, text, *, state_dir=None, speaker_labeled=False):
        raise RuntimeError("ollama is down")

    monkeypatch.setattr(workflow_module, "analyze_text", failing_analyze)
    list(workflow.process_files())

    def ok_analyze(config, text, *, state_dir=None, speaker_labeled=False):
        boundaries.analyze_calls.append({"text": text, "speaker_labeled": speaker_labeled})
        return NoteAnalysis(title="Title", category="Category", short_summary="Summary")

    monkeypatch.setattr(workflow_module, "analyze_text", ok_analyze)
    _install_diarization(monkeypatch, boundaries, TWO_SPEAKER_TURNS)
    boundaries.transcribe_calls.clear()

    events = list(workflow.process_files(diarize=True))

    assert boundaries.transcribe_calls  # re-transcribed instead of reusing the unlabeled text
    assert "Speaker 1:" in _note_text(events)


@freeze_time("2024-01-01 12:00:00")
def test_broken_diarization_setup_fails_the_step_once(
    tmp_path: Path, boundaries: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow = _make_workflow(tmp_path, enabled=True)
    _add_audio(workflow, "a.wav")
    _add_audio(workflow, "b.wav")

    monkeypatch.setattr(workflow_module, "probe_diarization_engine", lambda: "sherpa-onnx is not available: boom")

    events = list(workflow.process_files())

    errors = [event for event in events if event.type == "error"]
    assert len(errors) == 1
    assert "sherpa-onnx is not available" in errors[0].message
    assert boundaries.transcribe_calls == []
    summary = next(event for event in events if event.type == "summary")
    assert summary.data == {"processed": 0, "skipped": 0, "failed": 0}


@freeze_time("2024-01-01 12:00:00")
def test_cli_override_can_force_diarization_off(
    tmp_path: Path, boundaries: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow = _make_workflow(tmp_path, enabled=True)
    _install_diarization(monkeypatch, boundaries, TWO_SPEAKER_TURNS)
    _add_audio(workflow)

    events = list(workflow.process_files(diarize=False))

    assert boundaries.diarize_calls == []
    assert boundaries.transcribe_calls[0]["word_timestamps"] is False
    assert not any(event.type == "diarized" for event in events)
    assert diarization_fingerprint(workflow.config) == DIARIZATION_DISABLED_FINGERPRINT


def _hash_of(audio: Path, tmp_path: Path) -> str:
    from voxnote.state import compute_file_hash

    archived = next((tmp_path / "archive").glob("*"), None)
    return compute_file_hash(archived if archived is not None else audio)


def _restore_audio(workflow: Workflow, audio: Path, content: bytes = b"fake-audio-bytes") -> Optional[Path]:
    """Put the file back into input/ after `process` archived it."""
    audio.write_bytes(content)
    return audio
