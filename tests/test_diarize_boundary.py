from __future__ import annotations

import os
import tarfile
from pathlib import Path

import pytest

from voxnote import diarize
from voxnote.diarize import (
    DEFAULT_EMBEDDING_FILENAME,
    DEFAULT_SEGMENTATION_RELPATH,
    DIARIZATION_DISABLED_FINGERPRINT,
    SEGMENTATION_ARCHIVE_MEMBER,
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


class _FakeStreamedResponse:
    """Minimal stand-in for `requests.get(..., stream=True)` used as a context manager."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self) -> _FakeStreamedResponse:
        return self

    def __exit__(self, *exc_info) -> bool:
        return False

    def raise_for_status(self) -> None:
        return None

    def iter_content(self, chunk_size: int = 1):
        yield self._body


def _segmentation_archive_bytes(tmp_path: Path, *, payload: bytes) -> bytes:
    source = tmp_path / "source_model.onnx"
    source.write_bytes(payload)
    archive = tmp_path / "source.tar.bz2"
    with tarfile.open(archive, "w:bz2") as tar:
        tar.add(source, arcname=SEGMENTATION_ARCHIVE_MEMBER)
    return archive.read_bytes()


def _serve_archive(monkeypatch: pytest.MonkeyPatch, body: bytes) -> None:
    monkeypatch.setattr(diarize.requests, "get", lambda *args, **kwargs: _FakeStreamedResponse(body))


def test_corrupt_segmentation_archive_is_an_actionable_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A captive portal / proxy answering HTTP 200 with an HTML body passes raise_for_status,
    # so the bz2 read is where it breaks. The user must still learn what to download where.
    config = _make_config(tmp_path, enabled=True)
    _serve_archive(monkeypatch, b"<html><body>Sign in to the network</body></html>")

    with pytest.raises(RuntimeError) as excinfo:
        ensure_diarization_models(config, state_dir=tmp_path / ".voxnote")

    message = str(excinfo.value)
    assert SEGMENTATION_ARCHIVE_MEMBER in message
    assert "speaker-segmentation-models" in message
    assert str(tmp_path / ".voxnote" / "diarization" / DEFAULT_SEGMENTATION_RELPATH) in message


def test_truncated_segmentation_archive_is_an_actionable_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # An aborted download leaves a valid bz2 prefix, which fails with EOFError rather than a
    # tarfile error. Incompressible payload > 900 KB so the stream really spans several blocks.
    config = _make_config(tmp_path, enabled=True)
    full = _segmentation_archive_bytes(tmp_path, payload=os.urandom(2 << 20))
    _serve_archive(monkeypatch, full[: len(full) // 2])

    with pytest.raises(RuntimeError) as excinfo:
        ensure_diarization_models(config, state_dir=tmp_path / ".voxnote")

    message = str(excinfo.value)
    assert SEGMENTATION_ARCHIVE_MEMBER in message
    assert "speaker-segmentation-models" in message


def test_failed_extraction_leaves_no_partial_model_behind(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The archive opens fine and the write breaks halfway: the half-written model must not be
    # left in the models dir (nothing reads `*.part`, but a stale multi-MB file would linger).
    config = _make_config(tmp_path, enabled=True)
    _serve_archive(monkeypatch, _segmentation_archive_bytes(tmp_path, payload=b"fake-model"))

    target = tmp_path / ".voxnote" / "diarization" / DEFAULT_SEGMENTATION_RELPATH
    leftovers: list[list[Path]] = []

    def broken_copy(*args, **kwargs):
        leftovers.append(list(target.parent.glob("*.part")))
        raise OSError("input/output error")

    monkeypatch.setattr(diarize.shutil, "copyfileobj", broken_copy)

    with pytest.raises(RuntimeError) as excinfo:
        ensure_diarization_models(config, state_dir=tmp_path / ".voxnote")

    message = str(excinfo.value)
    assert "input/output error" in message
    assert "speaker-segmentation-models" in message
    assert str(target) in message
    assert leftovers == [[target.with_name(target.name + ".part")]]  # the partial did exist...
    assert list(target.parent.glob("*.part")) == []  # ...and was cleaned up
    assert not target.exists()


def test_extraction_failure_survives_a_failing_cleanup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # `target.parent` occupied by a regular file: mkdir fails, and unlinking the partial under
    # that path fails too. The actionable message must not be replaced by the cleanup's error.
    config = _make_config(tmp_path, enabled=True, segmentation_model="")
    _serve_archive(monkeypatch, _segmentation_archive_bytes(tmp_path, payload=b"fake-model"))

    target = tmp_path / ".voxnote" / "diarization" / DEFAULT_SEGMENTATION_RELPATH
    target.parent.parent.mkdir(parents=True, exist_ok=True)
    target.parent.write_bytes(b"not a directory")

    with pytest.raises(RuntimeError) as excinfo:
        ensure_diarization_models(config, state_dir=tmp_path / ".voxnote")

    assert "speaker-segmentation-models" in str(excinfo.value)


def test_unsupported_backend_is_rejected(tmp_path: Path) -> None:
    config = _make_config(tmp_path, enabled=True)
    audio = tmp_path / "input" / "note.wav"
    audio.write_bytes(b"RIFF")
    # The Literal type keeps YAML honest; set it directly to prove the runtime guard exists too.
    config.diarization.backend = "pyannote"

    with pytest.raises(RuntimeError, match="Unsupported diarization backend"):
        diarize.diarize_audio(config, audio, state_dir=tmp_path / ".voxnote")


def test_missing_engine_fails_before_models_are_downloaded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Without sherpa-onnx installed there is nothing the ~35 MB download could be used for,
    # so the install hint must come first — the workflow preflight is not the only caller.
    config = _make_config(tmp_path, enabled=True)
    audio = tmp_path / "input" / "note.wav"
    audio.write_bytes(b"RIFF")

    def missing_engine():
        raise RuntimeError("sherpa-onnx is not available: no module named sherpa_onnx. Install it with `uv sync`")

    def fail_download(*args, **kwargs):
        raise AssertionError("models must not be fetched before the engine import is checked")

    monkeypatch.setattr(diarize, "_import_sherpa_onnx", missing_engine)
    monkeypatch.setattr(diarize, "ensure_diarization_models", fail_download)

    with pytest.raises(RuntimeError, match="sherpa-onnx is not available"):
        diarize.diarize_audio(config, audio, state_dir=tmp_path / ".voxnote")
