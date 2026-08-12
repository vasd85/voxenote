from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from voxnote.config import load_config
from voxnote.models import DEFAULT_SPEAKER_LABELS_HINT

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_PATH = REPO_ROOT / "config.example.yaml"

_BASE_CONFIG = """
paths:
  input: ./input
  output: ./output
  archive: ./archive

transcription:
  model: test-model

llm:
  model: test-llm

prompts:
  system_prompt: Test system prompt
"""


def test_diarization_defaults_to_off_when_section_omitted(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(_BASE_CONFIG, encoding="utf-8")

    config = load_config(cfg_path)

    assert config.diarization.enabled is False
    assert config.diarization.backend == "sherpa_onnx"
    assert config.diarization.num_speakers == 0
    assert config.diarization.segmentation_model == ""
    assert config.prompts.speaker_labels_hint == DEFAULT_SPEAKER_LABELS_HINT


def test_diarization_section_is_parsed(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        _BASE_CONFIG
        + """
diarization:
  enabled: true
  num_speakers: 3
  cluster_threshold: 0.42
  embedding_model: other.onnx
""",
        encoding="utf-8",
    )

    config = load_config(cfg_path)

    assert config.diarization.enabled is True
    assert config.diarization.num_speakers == 3
    assert config.diarization.cluster_threshold == 0.42
    assert config.diarization.embedding_model == "other.onnx"
    assert config.diarization.min_duration_on == 0.3  # unset -> default


def test_invalid_diarization_values_are_rejected(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        _BASE_CONFIG
        + """
diarization:
  num_speakers: -1
""",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError):
        load_config(cfg_path)


def test_unknown_backend_is_rejected(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        _BASE_CONFIG
        + """
diarization:
  backend: pyannote
""",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError):
        load_config(cfg_path)


def test_shipped_template_stays_loadable_and_documents_diarization(tmp_path: Path) -> None:
    # `voxnote init` copies this template verbatim, so it must validate as-is.
    cfg_path = tmp_path / "config.yaml"
    shutil.copyfile(TEMPLATE_PATH, cfg_path)

    config = load_config(cfg_path)

    assert config.diarization.enabled is False
    assert config.diarization.backend == "sherpa_onnx"
    assert config.prompts.speaker_labels_hint.strip()
    assert "Speaker 1" in config.prompts.speaker_labels_hint
    assert "inert data" in config.prompts.speaker_labels_hint
