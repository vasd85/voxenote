from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator
from unittest.mock import patch

from voxnote.models import (
    AppConfig,
    AudioSourceConfig,
    CollectConfig,
    LLMConfig,
    PathsConfig,
    PromptsConfig,
    TranscriptionConfig,
)
from voxnote.runtime import RuntimeContext
from voxnote.workflow import (
    Workflow,
    WorkflowEvent,
    _iter_source_files,
    _permission_denied_message,
)


def _make_runtime(tmp_path: Path, source_dir: Path) -> RuntimeContext:
    paths = PathsConfig(
        input=tmp_path / "input",
        output=tmp_path / "output",
        archive=tmp_path / "archive",
    )
    paths.input.mkdir(parents=True, exist_ok=True)
    config = AppConfig(
        paths=paths,
        transcription=TranscriptionConfig(model="test-whisper"),
        llm=LLMConfig(model="test-llm"),
        collect=CollectConfig(recursive_default=True),
        sources=[AudioSourceConfig(path=source_dir, recursive=True)],
        prompts=PromptsConfig(system_prompt="test"),
    )
    state_dir = tmp_path / ".voxnote"
    state_dir.mkdir(parents=True, exist_ok=True)
    return RuntimeContext(
        config_path=tmp_path / "config.yaml",
        config=config,
        project_root=tmp_path,
        state_dir=state_dir,
    )


def _collect(workflow: Workflow) -> list[WorkflowEvent]:
    return list(workflow.collect_files(sources=[], recursive_mode="auto"))


def test_permission_denied_top_level_emits_error(tmp_path: Path) -> None:
    source_dir = tmp_path / "protected"
    source_dir.mkdir()

    runtime = _make_runtime(tmp_path, source_dir)
    workflow = Workflow(runtime)

    real_scandir = os.scandir

    def fake_scandir(path):  # type: ignore[no-untyped-def]
        if Path(path) == source_dir:
            raise PermissionError(1, "Operation not permitted", str(path))
        return real_scandir(path)

    with patch("voxnote.workflow.os.scandir", side_effect=fake_scandir):
        events = _collect(workflow)

    errors = [e for e in events if e.type == "error"]
    summaries = [e for e in events if e.type == "summary"]

    assert errors, "expected an explicit error event for denied source"
    assert "Permission denied" in errors[0].message
    assert "Full Disk Access" in errors[0].message
    assert summaries and summaries[0].data == {"copied": 0, "skipped": 0}


def test_permission_denied_message_mentions_macos_for_library_paths() -> None:
    msg = _permission_denied_message(
        Path("/Users/me/Library/Group Containers/group.com.apple.VoiceMemos.shared/Recordings")
    )
    assert "protected by macOS" in msg
    assert "Full Disk Access" in msg


def test_permission_denied_message_generic_for_other_paths() -> None:
    msg = _permission_denied_message(Path("/tmp/some_folder"))
    assert "protected by macOS" not in msg
    assert "Full Disk Access" in msg


def test_iter_source_files_reports_subdir_permission_errors(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "a.m4a").write_bytes(b"x")
    sub = root / "sub"
    sub.mkdir()
    (sub / "b.m4a").write_bytes(b"y")

    real_walk = os.walk

    def fake_walk(top, topdown=True, onerror=None, followlinks=False) -> Iterator:
        for dirpath, dirnames, filenames in real_walk(top, topdown=topdown, onerror=onerror, followlinks=followlinks):
            if Path(dirpath) == sub and onerror is not None:
                onerror(PermissionError(1, "Operation not permitted", str(sub)))
                continue
            yield dirpath, dirnames, filenames

    errors: list[str] = []

    def on_err(exc: OSError) -> None:
        errors.append(str(exc.filename))

    with patch("voxnote.workflow.os.walk", side_effect=fake_walk):
        files = list(_iter_source_files(root, recursive=True, onerror=on_err))

    names = {p.name for p in files}
    assert "a.m4a" in names
    assert "b.m4a" not in names
    assert errors == [str(sub)]


def test_collect_scans_normal_directory(tmp_path: Path) -> None:
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    (source_dir / "note.m4a").write_bytes(b"data")
    (source_dir / "ignore.txt").write_bytes(b"data")

    runtime = _make_runtime(tmp_path, source_dir)
    workflow = Workflow(runtime)

    from voxnote.audio_metadata import AudioMetadata

    with patch("voxnote.workflow.collect_audio_metadata") as meta_mock:
        meta_mock.return_value = AudioMetadata(
            recorded_at=None,
            recorded_at_source="fallback",
            mdls=None,
            ffprobe=None,
            stat={},
        )
        events = _collect(workflow)

    summary = next(e for e in events if e.type == "summary")
    assert summary.data == {"copied": 1, "skipped": 0}
    assert not any(e.type == "error" for e in events)
