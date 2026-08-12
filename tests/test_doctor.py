from __future__ import annotations

from pathlib import Path
from typing import List

import pytest

from voxnote import doctor
from voxnote.doctor import CheckResult, _check_diarization
from voxnote.models import (
    AppConfig,
    DiarizationConfig,
    LLMConfig,
    PathsConfig,
    PromptsConfig,
    TranscriptionConfig,
)
from voxnote.runtime import RuntimeContext


def _make_runtime(root: Path, **diarization_kwargs) -> RuntimeContext:
    paths = PathsConfig(input=root / "input", output=root / "output", archive=root / "archive")
    for directory in (paths.input, paths.output, paths.archive):
        directory.mkdir(parents=True, exist_ok=True)
    config = AppConfig(
        paths=paths,
        transcription=TranscriptionConfig(model="test-model"),
        llm=LLMConfig(model="test-llm"),
        prompts=PromptsConfig(system_prompt="Test system prompt"),
        diarization=DiarizationConfig(**diarization_kwargs),
    )
    return RuntimeContext(
        config_path=root / "config.yaml",
        config=config,
        project_root=root,
        state_dir=root / ".voxnote",
    )


def _result(results: List[CheckResult], name: str) -> CheckResult:
    return next(result for result in results if result.name == name)


@pytest.fixture(autouse=True)
def _engine_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor, "probe_diarization_engine", lambda: None)


def test_missing_default_models_promise_the_auto_download(tmp_path: Path) -> None:
    runtime = _make_runtime(tmp_path, enabled=True)

    results = _check_diarization(runtime)

    for name in ("Diarization segmentation model", "Diarization embedding model"):
        check = _result(results, name)
        assert not check.ok
        assert "voxnote process" in check.info


def test_missing_overridden_segmentation_model_does_not_promise_a_download(tmp_path: Path) -> None:
    override = tmp_path / "elsewhere" / "seg.onnx"
    runtime = _make_runtime(tmp_path, enabled=True, segmentation_model=str(override))

    results = _check_diarization(runtime)

    check = _result(results, "Diarization segmentation model")
    assert not check.ok
    assert "voxnote process" not in check.info
    assert str(override) in check.info
    assert "diarization.segmentation_model" in check.info
    assert "config.yaml" in check.info


def test_missing_embedding_path_override_does_not_promise_a_download(tmp_path: Path) -> None:
    runtime = _make_runtime(tmp_path, enabled=True, embedding_model="somewhere/emb.onnx")

    results = _check_diarization(runtime)

    check = _result(results, "Diarization embedding model")
    assert not check.ok
    assert "voxnote process" not in check.info
    assert "diarization.embedding_model" in check.info
    assert "config.yaml" in check.info


def test_missing_bare_filename_embedding_override_still_promises_the_download(tmp_path: Path) -> None:
    # A bare file name maps to the k2-fsa release, so process would download it.
    runtime = _make_runtime(tmp_path, enabled=True, embedding_model="custom-model.onnx")

    results = _check_diarization(runtime)

    check = _result(results, "Diarization embedding model")
    assert not check.ok
    assert "voxnote process" in check.info


def test_present_models_pass_regardless_of_override(tmp_path: Path) -> None:
    override = tmp_path / "elsewhere" / "seg.onnx"
    override.parent.mkdir(parents=True, exist_ok=True)
    override.write_bytes(b"fake-model")
    embedding = tmp_path / ".voxnote" / "diarization" / "custom-model.onnx"
    embedding.parent.mkdir(parents=True, exist_ok=True)
    embedding.write_bytes(b"fake-model")
    runtime = _make_runtime(
        tmp_path,
        enabled=True,
        segmentation_model=str(override),
        embedding_model="custom-model.onnx",
    )

    results = _check_diarization(runtime)

    assert _result(results, "Diarization segmentation model").ok
    assert _result(results, "Diarization embedding model").ok


def test_disabled_diarization_reports_a_single_ok_check(tmp_path: Path) -> None:
    runtime = _make_runtime(tmp_path, enabled=False)

    results = _check_diarization(runtime)

    assert len(results) == 1
    assert results[0].ok
    assert "Disabled" in results[0].info
