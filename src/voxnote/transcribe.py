from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Iterable, List

from .config import load_config, resolve_config_path, resolve_state_dir
from .models import AppConfig, TranscriptionResult, TranscriptSegment, TranscriptWord


def _ensure_supported_extension(config: AppConfig, path: Path) -> None:
    ext = path.suffix.lower().lstrip(".")
    if ext not in config.processing.supported_formats:
        supported = ", ".join(config.processing.supported_formats)
        raise ValueError(
            f"Unsupported audio format: {path.suffix}. "
            f"Supported formats: {supported}. "
            f"Convert the file or add the format to config.yaml processing.supported_formats."
        )


def _remove_repetitions(text: str, max_repeats: int = 3) -> str:
    """
    Remove excessive repetitions of phrases or words from transcription.

    Handles repeated lines (same line repeated many times consecutively).
    This is a common issue with Whisper when it encounters silence or noise.

    Args:
        text: Input transcription text
        max_repeats: Maximum number of allowed consecutive repetitions (default: 3)

    Returns:
        Cleaned text with excessive repetitions removed
    """
    if not text:
        return text

    lines = text.split("\n")
    cleaned_lines: List[str] = []
    i = 0

    while i < len(lines):
        line = lines[i]
        line_stripped = line.strip()

        if not line_stripped:
            cleaned_lines.append(line)
            i += 1
            continue

        repeat_count = 1
        j = i + 1

        while j < len(lines):
            next_line_stripped = lines[j].strip()
            if next_line_stripped == line_stripped:
                repeat_count += 1
                j += 1
            elif not next_line_stripped:
                j += 1
            else:
                break

        if repeat_count > max_repeats:
            for _ in range(min(max_repeats, 2)):
                cleaned_lines.append(line_stripped)
            i = j
        else:
            for k in range(i, j):
                cleaned_lines.append(lines[k])
            i = j

    result = "\n".join(cleaned_lines)
    return result.strip()


def _drop_repeated_segments(segments: List[TranscriptSegment], max_repeats: int = 3) -> List[TranscriptSegment]:
    """Segment-level twin of `_remove_repetitions`.

    Whisper writes one line per segment, so filtering segments keeps the plain text and the
    speaker-labeled transcript (built from the same segments) free of the same hallucinated loops.
    """
    kept: List[TranscriptSegment] = []
    index = 0
    total = len(segments)

    while index < total:
        current = segments[index].text.strip()
        if not current:
            kept.append(segments[index])
            index += 1
            continue

        repeat_count = 1
        lookahead = index + 1
        while lookahead < total:
            candidate = segments[lookahead].text.strip()
            if candidate == current:
                repeat_count += 1
                lookahead += 1
            elif not candidate:
                lookahead += 1
            else:
                break

        run = segments[index:lookahead]
        if repeat_count > max_repeats:
            kept.extend([segment for segment in run if segment.text.strip() == current][: min(max_repeats, 2)])
        else:
            kept.extend(run)
        index = lookahead

    return kept


def _as_float(value: object, fallback: float) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return fallback


def _parse_whisper_json(payload: dict) -> List[TranscriptSegment]:
    """Parse mlx-whisper JSON output into segments with word timestamps."""
    raw_segments = payload.get("segments")
    if not isinstance(raw_segments, list):
        return []

    segments: List[TranscriptSegment] = []
    for raw_segment in raw_segments:
        if not isinstance(raw_segment, dict):
            continue
        text = str(raw_segment.get("text") or "")
        start = _as_float(raw_segment.get("start"), 0.0)
        end = max(_as_float(raw_segment.get("end"), start), start)

        words: List[TranscriptWord] = []
        raw_words = raw_segment.get("words")
        if isinstance(raw_words, list):
            # Missing/invalid word timings fall back to the previous word's end (segment
            # start for the first word), not the segment start: a zero-length token at the
            # segment's opening instant would be attributed to the wrong speaker turn.
            cursor = start
            for raw_word in raw_words:
                if not isinstance(raw_word, dict):
                    continue
                word_text = str(raw_word.get("word") or "")
                if not word_text.strip():
                    continue
                word_start = _as_float(raw_word.get("start"), cursor)
                word_end = max(_as_float(raw_word.get("end"), word_start), word_start)
                words.append(TranscriptWord(text=word_text, start=word_start, end=word_end))
                cursor = word_end

        segments.append(TranscriptSegment(text=text, start=start, end=end, words=words))

    return segments


def _find_mlx_whisper() -> str:
    """Find mlx_whisper executable in PATH or virtual environment.

    Supports both naming variants: mlx_whisper (underscore) and mlx-whisper (dash).
    """
    mlx_whisper = shutil.which("mlx_whisper") or shutil.which("mlx-whisper")
    if mlx_whisper:
        return mlx_whisper

    venv_bin_underscore = Path(sys.executable).parent / "mlx_whisper"
    venv_bin_dash = Path(sys.executable).parent / "mlx-whisper"
    if venv_bin_underscore.exists():
        return str(venv_bin_underscore)
    if venv_bin_dash.exists():
        return str(venv_bin_dash)

    raise RuntimeError(
        "mlx_whisper CLI not found. Install it with 'pip install mlx-whisper' "
        "and ensure it is on PATH or in the virtual environment."
    )


def _debug_log_whisper(
    config: AppConfig,
    *,
    audio_path: Path,
    cmd: List[str],
    returncode: int | None,
    stdout: str,
    stderr: str,
    error: str,
    state_dir: Path | None = None,
) -> None:
    """Append raw mlx_whisper exchange to debug log when debug mode is enabled."""
    if not getattr(config.llm, "debug", False):
        return

    try:
        state_root = (state_dir or resolve_state_dir(resolve_config_path())).expanduser().resolve()
        state_root.mkdir(parents=True, exist_ok=True)
        path = state_root / "whisper_debug.jsonl"

        stdout = stdout or ""
        stderr = stderr or ""

        entry = {
            "ts": datetime.now().isoformat(),
            "audio_path": str(audio_path),
            "error": error,
            "returncode": returncode,
            "cmd": cmd,
            "stdout_len": len(stdout),
            "stderr_len": len(stderr),
            "stdout_sha256": hashlib.sha256(stdout.encode("utf-8")).hexdigest(),
            "stderr_sha256": hashlib.sha256(stderr.encode("utf-8")).hexdigest(),
            "stdout": stdout,
            "stderr": stderr,
        }

        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        # Best-effort debug logging; never break main flow
        pass


def _find_output_file(output_dir: Path, audio_path: Path, extension: str) -> Path | None:
    """Locate the file mlx-whisper wrote, tolerating stem/name naming differences."""
    expected_output_file = output_dir / f"{audio_path.stem}.{extension}"
    if expected_output_file.exists():
        return expected_output_file

    alt_output_file = output_dir / f"{audio_path.name}.{extension}"
    if alt_output_file.exists():
        return alt_output_file

    candidates = sorted(output_dir.glob(f"*.{extension}"))
    return candidates[0] if len(candidates) == 1 else None


def _run_mlx_whisper(
    config: AppConfig,
    audio_path: Path,
    *,
    state_dir: Path | None = None,
    word_timestamps: bool = False,
) -> tuple[str, List[TranscriptSegment]]:
    """Run mlx-whisper CLI and return plain text plus segments (only when word_timestamps)."""
    mlx_whisper_cmd = _find_mlx_whisper()
    output_format = "json" if word_timestamps else "txt"

    with tempfile.TemporaryDirectory(dir=str(state_dir) if state_dir else None) as tmpdir:
        output_dir = Path(tmpdir)

        cmd: List[str] = [
            mlx_whisper_cmd,
            "--output-format",
            output_format,
            "--output-dir",
            str(output_dir),
            str(audio_path),
        ]

        if config.transcription.model:
            cmd.extend(["--model", config.transcription.model])
        if config.transcription.language != "auto":
            cmd.extend(["--language", config.transcription.language])
        if word_timestamps:
            # mlx-whisper's flag parser only accepts the literal string "True".
            cmd.extend(["--word-timestamps", "True"])

        try:
            result = subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
                timeout=config.transcription.whisper_timeout_s,
            )
        except subprocess.TimeoutExpired as exc:
            _debug_log_whisper(
                config,
                audio_path=audio_path,
                cmd=cmd,
                returncode=None,
                stdout="",
                stderr="",
                error=f"mlx_whisper process timed out after {exc.timeout} seconds",
                state_dir=state_dir,
            )
            raise RuntimeError(
                f"mlx_whisper process timed out after {exc.timeout} seconds. "
                "The audio file may be too large or the system is overloaded. "
                f"Increase timeout in config.yaml (transcription.whisper_timeout_s) or "
                "try processing a shorter audio file."
            ) from exc
        except FileNotFoundError as exc:
            raise RuntimeError(
                "mlx_whisper CLI not found. Install it with 'pip install mlx-whisper' "
                "and ensure it is on PATH or in the virtual environment."
            ) from exc
        except subprocess.CalledProcessError as exc:
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            _debug_log_whisper(
                config,
                audio_path=audio_path,
                cmd=cmd,
                returncode=exc.returncode,
                stdout=stdout,
                stderr=stderr,
                error="mlx_whisper process failed",
                state_dir=state_dir,
            )
            msg = (
                "mlx_whisper failed "
                f"(exit_code={exc.returncode}, stdout={len(stdout)} chars, stderr={len(stderr)} chars). "
                "Check that the audio file is valid and not corrupted."
            )
            if getattr(config.llm, "debug", False):
                msg += " Details: .voxnote/whisper_debug.jsonl"
            else:
                msg += " Enable debug mode in config.yaml (llm.debug: true) for detailed logs."
            raise RuntimeError(msg) from exc

        output_file = _find_output_file(output_dir, audio_path, output_format)

        if output_file is None:
            _debug_log_whisper(
                config,
                audio_path=audio_path,
                cmd=cmd,
                returncode=result.returncode,
                stdout=result.stdout or "",
                stderr=result.stderr or "",
                error=f"mlx_whisper did not create a {output_format} output file",
                state_dir=state_dir,
            )
            # Do not treat stdout as transcription: CLI may print progress or other text.
            stdout_len = len(result.stdout or "")
            stderr_len = len(result.stderr or "")
            msg = (
                f"mlx_whisper did not create a {output_format} output file "
                f"(stdout={stdout_len} chars, stderr={stderr_len} chars). "
                "The transcription may have failed silently. "
                "Check that mlx_whisper is working correctly: `mlx_whisper --help`"
            )
            if getattr(config.llm, "debug", False):
                msg += " Details: .voxnote/whisper_debug.jsonl"
            else:
                msg += " Enable debug mode in config.yaml (llm.debug: true) for detailed logs."
            raise RuntimeError(msg)

        raw_output = output_file.read_text(encoding="utf-8")
        segments: List[TranscriptSegment] = []

        if word_timestamps:
            try:
                payload = json.loads(raw_output)
            except json.JSONDecodeError as exc:
                _debug_log_whisper(
                    config,
                    audio_path=audio_path,
                    cmd=cmd,
                    returncode=result.returncode,
                    stdout=result.stdout or "",
                    stderr=result.stderr or "",
                    error="mlx_whisper json output is not valid JSON",
                    state_dir=state_dir,
                )
                raise RuntimeError(
                    "mlx_whisper produced an unreadable JSON transcript. "
                    "Check that mlx_whisper supports `--output-format json --word-timestamps True`: "
                    "`mlx_whisper --help`. Or disable diarization in config.yaml (diarization.enabled: false)."
                ) from exc
            if not isinstance(payload, dict):
                raise RuntimeError(
                    "mlx_whisper JSON transcript has an unexpected shape (expected an object). "
                    "Disable diarization in config.yaml (diarization.enabled: false) to fall back to plain text."
                )
            segments = _drop_repeated_segments(_parse_whisper_json(payload))
            text = "\n".join(segment.text.strip() for segment in segments).strip()
        else:
            text = raw_output.strip()

        if not text:
            _debug_log_whisper(
                config,
                audio_path=audio_path,
                cmd=cmd,
                returncode=result.returncode,
                stdout=result.stdout or "",
                stderr=result.stderr or "",
                error=f"empty transcription in {output_format} output file",
                state_dir=state_dir,
            )
            raise RuntimeError(
                "Empty transcription from mlx_whisper. "
                "The audio file may contain only silence or be too quiet. "
                "Check the audio file or try with a different file."
            )

        return _remove_repetitions(text), segments


def transcribe_file(
    config: AppConfig,
    audio_path: Path,
    *,
    state_dir: Path | None = None,
    word_timestamps: bool = False,
) -> TranscriptionResult:
    """Transcribe one file. `word_timestamps` also returns segments (needed for diarization)."""
    audio_path = audio_path.expanduser().resolve()
    if not audio_path.exists():
        raise FileNotFoundError(audio_path)

    _ensure_supported_extension(config, audio_path)

    text, segments = _run_mlx_whisper(config, audio_path, state_dir=state_dir, word_timestamps=word_timestamps)
    return TranscriptionResult(audio_path=audio_path, text=text, segments=segments)


def transcribe_many(
    config: AppConfig, audio_files: Iterable[Path], *, state_dir: Path | None = None
) -> List[TranscriptionResult]:
    results: List[TranscriptionResult] = []
    for path in audio_files:
        results.append(transcribe_file(config, path, state_dir=state_dir))
    return results


def cli_transcribe_single(path: str) -> None:
    config = load_config()
    res = transcribe_file(config, Path(path))
    print(res.text)
