from __future__ import annotations

from pathlib import Path

from voxnote.collect_plan import build_collect_source_plan
from voxnote.models import (
    AppConfig,
    AudioSourceConfig,
    CollectConfig,
    LLMConfig,
    PathsConfig,
    PromptsConfig,
    TranscriptionConfig,
)


def _make_config(tmp_path: Path) -> AppConfig:
    paths = PathsConfig(
        input=tmp_path / "input",
        output=tmp_path / "output",
        archive=tmp_path / "archive",
    )
    return AppConfig(
        paths=paths,
        transcription=TranscriptionConfig(model="test-whisper"),
        llm=LLMConfig(model="test-llm"),
        collect=CollectConfig(recursive_default=True),
        sources=[
            AudioSourceConfig(path=tmp_path / "src1", recursive=False),
            AudioSourceConfig(path=tmp_path / "src2", recursive=True),
        ],
        prompts=PromptsConfig(system_prompt="Test system prompt"),
    )


def test_plan_uses_config_sources_when_no_cli_sources(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    plan = build_collect_source_plan(config, cli_sources=[], recursive_mode="auto")

    assert [p.source_dir for p in plan] == [
        (tmp_path / "src1").resolve(),
        (tmp_path / "src2").resolve(),
    ]
    assert [(p.recursive, p.reason) for p in plan] == [(False, "config"), (True, "config")]


def test_plan_cli_sources_match_config_auto_uses_config_recursive(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    plan = build_collect_source_plan(
        config,
        cli_sources=[tmp_path / "src1"],
        recursive_mode="auto",
    )
    assert len(plan) == 1
    assert plan[0].source_dir == (tmp_path / "src1").resolve()
    assert plan[0].recursive is False
    assert plan[0].reason == "config"


def test_plan_cli_sources_not_in_config_auto_defaults_recursive(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    unknown = tmp_path / "unknown"
    plan = build_collect_source_plan(
        config,
        cli_sources=[unknown],
        recursive_mode="auto",
    )
    assert len(plan) == 1
    assert plan[0].source_dir == unknown.resolve()
    assert plan[0].recursive is True
    assert plan[0].reason == "default"


def test_plan_cli_sources_not_in_config_auto_uses_config_default_false(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    config = config.model_copy(update={"collect": CollectConfig(recursive_default=False)})

    unknown = tmp_path / "unknown"
    plan = build_collect_source_plan(
        config,
        cli_sources=[unknown],
        recursive_mode="auto",
    )
    assert len(plan) == 1
    assert plan[0].source_dir == unknown.resolve()
    assert plan[0].recursive is False
    assert plan[0].reason == "default"


def test_plan_cli_override_on_off(tmp_path: Path) -> None:
    config = _make_config(tmp_path)

    plan_on = build_collect_source_plan(
        config,
        cli_sources=[tmp_path / "src1"],
        recursive_mode="on",
    )
    assert plan_on[0].recursive is True
    assert plan_on[0].reason == "cli"

    plan_off = build_collect_source_plan(
        config,
        cli_sources=[tmp_path / "src2"],
        recursive_mode="off",
    )
    assert plan_off[0].recursive is False
    assert plan_off[0].reason == "cli"
