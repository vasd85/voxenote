from __future__ import annotations

from pathlib import Path
from typing import Optional

from voxnote.config import load_config
from voxnote.models import (
    AppConfig,
    LLMConfig,
    PathsConfig,
    PipelineConfig,
    PromptsConfig,
    TranscriptionConfig,
)
from voxnote.runtime import RuntimeContext
from voxnote.workflow import Workflow, WorkflowEvent

# Summary payloads the fake steps emit — shaped like the real generators' summaries.
_STEP_SUMMARIES = {
    "collect": {"copied": 1, "skipped": 0},
    "prepare_vad": {"prepared": 1, "skipped": 0, "errors": 0},
    "vad_trim": {"processed": 1, "skipped_cached": 0, "skipped_no_speech": 0, "errors": 0},
    "process": {"processed": 1, "skipped": 0, "failed": 0},
}


def _make_workflow(tmp_path: Path, pipeline: Optional[PipelineConfig] = None) -> Workflow:
    paths = PathsConfig(
        input=tmp_path / "input",
        output=tmp_path / "output",
        archive=tmp_path / "archive",
    )
    paths.input.mkdir(parents=True, exist_ok=True)
    extra = {"pipeline": pipeline} if pipeline is not None else {}
    config = AppConfig(
        paths=paths,
        transcription=TranscriptionConfig(model="test-whisper"),
        llm=LLMConfig(model="test-llm"),
        prompts=PromptsConfig(system_prompt="test"),
        **extra,
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


def _install_fake_steps(workflow: Workflow) -> list[tuple[str, tuple, dict]]:
    """Replace the four step generators with fakes that record their calls.

    Keeps run_pipeline orchestration testable without ffmpeg/torch/whisper/Ollama.
    """
    calls: list[tuple[str, tuple, dict]] = []

    def make(step_name: str):
        def fake(*args, **kwargs):
            calls.append((step_name, args, kwargs))
            yield WorkflowEvent("processing", f"{step_name} running")
            yield WorkflowEvent("summary", f"{step_name} done", data=_STEP_SUMMARIES[step_name])

        return fake

    workflow.collect_files = make("collect")  # type: ignore[method-assign]
    workflow.prepare_vad_files = make("prepare_vad")  # type: ignore[method-assign]
    workflow.vad_trim_files = make("vad_trim")  # type: ignore[method-assign]
    workflow.process_files = make("process")  # type: ignore[method-assign]
    return calls


def test_run_pipeline_runs_all_steps_in_order_by_default(tmp_path: Path) -> None:
    workflow = _make_workflow(tmp_path)
    calls = _install_fake_steps(workflow)

    events = list(workflow.run_pipeline())

    assert [name for name, _, _ in calls] == ["collect", "prepare_vad", "vad_trim", "process"]

    plan = next(e for e in events if e.type == "pipeline_plan")
    assert [s["name"] for s in plan.data["steps"]] == ["collect", "prepare_vad", "vad_trim", "process"]

    steps = [e for e in events if e.type == "step"]
    assert [(e.data["index"], e.data["total"]) for e in steps] == [(1, 4), (2, 4), (3, 4), (4, 4)]

    # Each sub-step's "summary" is converted to "step_summary"; the only "summary" is the final one.
    assert sum(1 for e in events if e.type == "step_summary") == 4
    assert events[-1].type == "summary"
    assert [s["step"] for s in events[-1].data["steps"]] == ["collect", "prepare_vad", "vad_trim", "process"]
    assert events[-1].data["steps"][0]["stats"] == _STEP_SUMMARIES["collect"]


def test_run_pipeline_skips_disabled_steps(tmp_path: Path) -> None:
    pipeline = PipelineConfig(collect=False, prepare_vad=True, vad_trim=False, process=True)
    workflow = _make_workflow(tmp_path, pipeline)
    calls = _install_fake_steps(workflow)

    events = list(workflow.run_pipeline())

    assert [name for name, _, _ in calls] == ["prepare_vad", "process"]
    steps = [e for e in events if e.type == "step"]
    assert [e.data["label"] for e in steps] == ["Prepare for VAD", "Process"]
    assert [(e.data["index"], e.data["total"]) for e in steps] == [(1, 2), (2, 2)]
    assert [s["step"] for s in events[-1].data["steps"]] == ["prepare_vad", "process"]


def test_run_pipeline_no_steps_enabled_yields_info_and_no_summary(tmp_path: Path) -> None:
    pipeline = PipelineConfig(collect=False, prepare_vad=False, vad_trim=False, process=False)
    workflow = _make_workflow(tmp_path, pipeline)
    calls = _install_fake_steps(workflow)

    events = list(workflow.run_pipeline())

    assert calls == []
    assert len(events) == 1
    assert events[0].type == "info"
    assert "pipeline" in events[0].message.lower()
    assert not any(e.type == "summary" for e in events)


def test_run_pipeline_passes_force_and_expected_args(tmp_path: Path) -> None:
    workflow = _make_workflow(tmp_path)
    calls = _install_fake_steps(workflow)

    list(workflow.run_pipeline(force=True))

    by_name = {name: (args, kwargs) for name, args, kwargs in calls}
    assert by_name["collect"] == (([], "auto"), {})
    assert by_name["prepare_vad"] == ((), {"files": None, "force": True})
    assert by_name["vad_trim"] == ((), {"files": None, "force": True})
    assert by_name["process"] == ((), {"files": None, "force_reprocess": True})


def test_run_pipeline_force_defaults_to_false(tmp_path: Path) -> None:
    workflow = _make_workflow(tmp_path)
    calls = _install_fake_steps(workflow)

    list(workflow.run_pipeline())

    by_name = {name: kwargs for name, _, kwargs in calls}
    assert by_name["prepare_vad"]["force"] is False
    assert by_name["vad_trim"]["force"] is False
    assert by_name["process"]["force_reprocess"] is False


def test_run_pipeline_records_step_without_summary_as_message(tmp_path: Path) -> None:
    # A step that early-returns on empty input yields only an "info" event (no summary).
    # The final recap must still list it, using the info text as its result.
    pipeline = PipelineConfig(collect=True, prepare_vad=False, vad_trim=False, process=False)
    workflow = _make_workflow(tmp_path, pipeline)

    def collect_no_op(*args, **kwargs):
        yield WorkflowEvent("info", "No sources configured.")

    workflow.collect_files = collect_no_op  # type: ignore[method-assign]

    events = list(workflow.run_pipeline())

    final = next(e for e in events if e.type == "summary")
    assert len(final.data["steps"]) == 1
    entry = final.data["steps"][0]
    assert entry["step"] == "collect"
    assert entry["message"] == "No sources configured."
    assert entry["stats"] == {}
    # The info event is passed through; no redundant step_summary is emitted for a no-op step.
    assert any(e.type == "info" and e.message == "No sources configured." for e in events)
    assert not any(e.type == "step_summary" for e in events)


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


def test_config_pipeline_defaults_true_when_section_omitted(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(_BASE_CONFIG, encoding="utf-8")

    config = load_config(cfg_path)

    assert config.pipeline.collect is True
    assert config.pipeline.prepare_vad is True
    assert config.pipeline.vad_trim is True
    assert config.pipeline.process is True


def test_config_pipeline_parsed_from_yaml(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        _BASE_CONFIG
        + """
pipeline:
  collect: false
  vad_trim: false
""",
        encoding="utf-8",
    )

    config = load_config(cfg_path)

    assert config.pipeline.collect is False
    assert config.pipeline.prepare_vad is True  # unset -> default
    assert config.pipeline.vad_trim is False
    assert config.pipeline.process is True
