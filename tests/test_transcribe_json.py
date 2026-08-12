from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from voxnote import transcribe
from voxnote.models import (
    AppConfig,
    LLMConfig,
    PathsConfig,
    PromptsConfig,
    SpeakerTurn,
    TranscriptionConfig,
    TranscriptSegment,
)
from voxnote.speaker_merge import assign_speakers


def _make_config(root: Path) -> AppConfig:
    paths = PathsConfig(input=root / "input", output=root / "output", archive=root / "archive")
    for directory in (paths.input, paths.output, paths.archive):
        directory.mkdir(parents=True, exist_ok=True)
    return AppConfig(
        paths=paths,
        transcription=TranscriptionConfig(model="test-model"),
        llm=LLMConfig(model="test-llm"),
        prompts=PromptsConfig(system_prompt="Test system prompt"),
    )


def test_parse_whisper_json_reads_segments_and_words() -> None:
    payload = {
        "segments": [
            {
                "start": 0.0,
                "end": 1.0,
                "text": " Hello there.",
                "words": [
                    {"word": " Hello", "start": 0.0, "end": 0.4, "probability": 0.9},
                    {"word": " there.", "start": 0.4, "end": 1.0, "probability": 0.8},
                ],
            }
        ]
    }

    segments = transcribe._parse_whisper_json(payload)

    assert len(segments) == 1
    assert segments[0].text == " Hello there."
    assert [word.text for word in segments[0].words] == [" Hello", " there."]
    assert segments[0].words[1].end == 1.0


def test_parse_whisper_json_tolerates_missing_timings_and_junk() -> None:
    payload = {
        "segments": [
            {"text": " ok", "words": [{"word": " ok"}, {"word": "   "}, "not-a-dict"]},
            "not-a-segment",
            {"text": " backwards", "start": 5.0, "end": 4.0, "words": []},
        ]
    }

    segments = transcribe._parse_whisper_json(payload)

    assert [segment.text for segment in segments] == [" ok", " backwards"]
    assert len(segments[0].words) == 1
    assert segments[0].words[0].start == 0.0
    # An end before the start is clamped rather than propagated.
    assert segments[1].end == segments[1].start == 5.0


def test_parse_whisper_json_anchors_missing_middle_word_timings_to_previous_word() -> None:
    payload = {
        "segments": [
            {
                "start": 10.0,
                "end": 20.0,
                "text": " one two three",
                "words": [
                    {"word": " one", "start": 15.5, "end": 16.5},
                    {"word": " two", "start": None, "end": None},
                    {"word": " three", "start": 17.0, "end": 18.0},
                ],
            }
        ]
    }

    segments = transcribe._parse_whisper_json(payload)

    # The degraded word stays at the running position instead of jumping to the segment start.
    degraded = segments[0].words[1]
    assert degraded.start == degraded.end == 16.5

    # All three words sit inside the second turn, so the block must not fragment.
    turns = [SpeakerTurn(speaker=0, start=10.0, end=15.0), SpeakerTurn(speaker=1, start=15.0, end=20.0)]
    blocks = assign_speakers(segments, turns)
    assert [(block.speaker, block.text) for block in blocks] == [(1, "one two three")]


def test_parse_whisper_json_without_segments_returns_empty() -> None:
    assert transcribe._parse_whisper_json({}) == []
    assert transcribe._parse_whisper_json({"segments": "nope"}) == []


def test_drop_repeated_segments_collapses_long_runs() -> None:
    segments = [TranscriptSegment(text=" loop", start=float(i), end=float(i) + 1.0) for i in range(6)]
    segments.append(TranscriptSegment(text=" end", start=6.0, end=7.0))

    kept = transcribe._drop_repeated_segments(segments)

    assert [segment.text for segment in kept] == [" loop", " loop", " end"]
    # The kept copies keep their own timings.
    assert (kept[0].start, kept[1].start) == (0.0, 1.0)


def test_drop_repeated_segments_keeps_short_runs() -> None:
    segments = [TranscriptSegment(text=" twice", start=0.0, end=1.0) for _ in range(3)]

    assert len(transcribe._drop_repeated_segments(segments)) == 3


def _fake_run_writing(output_name_for: str, content: str):
    """Return a subprocess.run stub that writes the file mlx-whisper would produce."""

    def fake_run(cmd, **kwargs):
        output_dir = Path(cmd[cmd.index("--output-dir") + 1])
        (output_dir / output_name_for).write_text(content, encoding="utf-8")
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

    return fake_run


def test_run_mlx_whisper_requests_json_and_word_timestamps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _make_config(tmp_path)
    audio = tmp_path / "input" / "note.wav"
    audio.write_bytes(b"audio")

    payload = {
        "segments": [
            {"start": 0.0, "end": 1.0, "text": " Hello.", "words": [{"word": " Hello.", "start": 0.0, "end": 1.0}]},
            {"start": 1.0, "end": 2.0, "text": " Bye.", "words": [{"word": " Bye.", "start": 1.0, "end": 2.0}]},
        ]
    }
    captured: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        captured.append(cmd)
        output_dir = Path(cmd[cmd.index("--output-dir") + 1])
        (output_dir / "note.json").write_text(json.dumps(payload), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(transcribe, "_find_mlx_whisper", lambda: "/fake/mlx_whisper")
    monkeypatch.setattr(subprocess, "run", fake_run)

    text, segments = transcribe._run_mlx_whisper(config, audio, word_timestamps=True)

    cmd = captured[0]
    assert cmd[cmd.index("--output-format") + 1] == "json"
    assert cmd[cmd.index("--word-timestamps") + 1] == "True"
    assert "--clip-timestamps" not in cmd
    assert text == "Hello.\nBye."
    assert [segment.text for segment in segments] == [" Hello.", " Bye."]


def test_run_mlx_whisper_keeps_plain_text_path_without_word_timestamps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _make_config(tmp_path)
    audio = tmp_path / "input" / "note.wav"
    audio.write_bytes(b"audio")

    captured: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        captured.append(cmd)
        return _fake_run_writing("note.txt", "Hello.\nBye.\n")(cmd, **kwargs)

    monkeypatch.setattr(transcribe, "_find_mlx_whisper", lambda: "/fake/mlx_whisper")
    monkeypatch.setattr(subprocess, "run", fake_run)

    text, segments = transcribe._run_mlx_whisper(config, audio, word_timestamps=False)

    cmd = captured[0]
    assert cmd[cmd.index("--output-format") + 1] == "txt"
    assert "--word-timestamps" not in cmd
    assert text == "Hello.\nBye."
    assert segments == []


def test_run_mlx_whisper_reports_unreadable_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _make_config(tmp_path)
    audio = tmp_path / "input" / "note.wav"
    audio.write_bytes(b"audio")

    monkeypatch.setattr(transcribe, "_find_mlx_whisper", lambda: "/fake/mlx_whisper")
    monkeypatch.setattr(subprocess, "run", _fake_run_writing("note.json", "not json at all"))

    with pytest.raises(RuntimeError) as excinfo:
        transcribe._run_mlx_whisper(config, audio, word_timestamps=True)

    assert "diarization.enabled" in str(excinfo.value)
