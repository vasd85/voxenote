from __future__ import annotations

from pathlib import Path

import pytest

from voxnote import diarize
from voxnote.diarize import (
    DEFAULT_EMBEDDING_FILENAME,
    DEFAULT_SEGMENTATION_RELPATH,
    DIARIZATION_DISABLED_FINGERPRINT,
    diarization_fingerprint,
    ensure_diarization_models,
    resolve_diarization_models,
)
from voxnote.models import (
    AppConfig,
    DiarizationConfig,
    LLMConfig,
    PathsConfig,
    PromptsConfig,
    TranscriptionConfig,
)


def _make_config(root: Path, **diarization_kwargs) -> AppConfig:
    paths = PathsConfig(input=root / "input", output=root / "output", archive=root / "archive")
    for directory in (paths.input, paths.output, paths.archive):
        directory.mkdir(parents=True, exist_ok=True)
    return AppConfig(
        paths=paths,
        transcription=TranscriptionConfig(model="test-model"),
        llm=LLMConfig(model="test-llm"),
        prompts=PromptsConfig(system_prompt="Test system prompt"),
        diarization=DiarizationConfig(**diarization_kwargs),
    )


def test_fingerprint_is_off_when_disabled(tmp_path: Path) -> None:
    config = _make_config(tmp_path, enabled=False, num_speakers=3)

    assert diarization_fingerprint(config) == DIARIZATION_DISABLED_FINGERPRINT


def test_fingerprint_changes_with_relevant_settings(tmp_path: Path) -> None:
    base = diarization_fingerprint(_make_config(tmp_path, enabled=True))
    other = diarization_fingerprint(_make_config(tmp_path, enabled=True, num_speakers=2))

    assert base.startswith("on:")
    assert base != other


def test_fingerprint_ignores_settings_that_do_not_change_output(tmp_path: Path) -> None:
    base = diarization_fingerprint(_make_config(tmp_path, enabled=True))
    tuned = diarization_fingerprint(_make_config(tmp_path, enabled=True, num_threads=8, download_timeout_s=30))

    assert base == tuned


def test_resolve_models_defaults_to_state_dir(tmp_path: Path) -> None:
    config = _make_config(tmp_path, enabled=True)
    state_dir = tmp_path / ".voxnote"

    segmentation, embedding = resolve_diarization_models(config, state_dir=state_dir)

    assert segmentation == state_dir / "diarization" / DEFAULT_SEGMENTATION_RELPATH
    assert embedding == state_dir / "diarization" / DEFAULT_EMBEDDING_FILENAME


def test_resolve_models_honours_absolute_and_relative_overrides(tmp_path: Path) -> None:
    absolute = tmp_path / "elsewhere" / "seg.onnx"
    config = _make_config(
        tmp_path,
        enabled=True,
        segmentation_model=str(absolute),
        embedding_model="custom/emb.onnx",
    )
    state_dir = tmp_path / ".voxnote"

    segmentation, embedding = resolve_diarization_models(config, state_dir=state_dir)

    assert segmentation == absolute
    assert embedding == state_dir / "diarization" / "custom" / "emb.onnx"


def test_missing_custom_segmentation_model_is_an_actionable_error(tmp_path: Path) -> None:
    config = _make_config(tmp_path, enabled=True, segmentation_model=str(tmp_path / "missing.onnx"))

    with pytest.raises(RuntimeError) as excinfo:
        ensure_diarization_models(config, state_dir=tmp_path / ".voxnote")

    message = str(excinfo.value)
    assert "missing.onnx" in message
    assert "diarization.segmentation_model" in message


def test_missing_custom_embedding_path_is_not_downloaded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A path (rather than a bare file name) means "user supplied", so no download is attempted.
    segmentation = tmp_path / ".voxnote" / "diarization" / DEFAULT_SEGMENTATION_RELPATH
    segmentation.parent.mkdir(parents=True, exist_ok=True)
    segmentation.write_bytes(b"fake-model")

    config = _make_config(tmp_path, enabled=True, embedding_model="somewhere/emb.onnx")

    def fail_download(*args, **kwargs):
        raise AssertionError("no download expected for a user-supplied path")

    monkeypatch.setattr(diarize.requests, "get", fail_download)

    with pytest.raises(RuntimeError) as excinfo:
        ensure_diarization_models(config, state_dir=tmp_path / ".voxnote")

    message = str(excinfo.value)
    assert "emb.onnx" in message
    assert "diarization.embedding_model" in message


def test_download_failure_message_points_at_the_release_page(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    segmentation = tmp_path / ".voxnote" / "diarization" / DEFAULT_SEGMENTATION_RELPATH
    segmentation.parent.mkdir(parents=True, exist_ok=True)
    segmentation.write_bytes(b"fake-model")

    config = _make_config(tmp_path, enabled=True)

    def broken_get(*args, **kwargs):
        raise OSError("network is unreachable")

    monkeypatch.setattr(diarize.requests, "get", broken_get)

    with pytest.raises(RuntimeError) as excinfo:
        ensure_diarization_models(config, state_dir=tmp_path / ".voxnote")

    message = str(excinfo.value)
    assert "network is unreachable" in message
    assert DEFAULT_EMBEDDING_FILENAME in message
    assert "speaker-recongition-models" in message


def test_unsupported_backend_is_rejected(tmp_path: Path) -> None:
    config = _make_config(tmp_path, enabled=True)
    audio = tmp_path / "input" / "note.wav"
    audio.write_bytes(b"RIFF")
    # The Literal type keeps YAML honest; set it directly to prove the runtime guard exists too.
    config.diarization.backend = "pyannote"

    with pytest.raises(RuntimeError, match="Unsupported diarization backend"):
        diarize.diarize_audio(config, audio, state_dir=tmp_path / ".voxnote")
