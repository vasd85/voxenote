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

from .config import DEFAULT_CONFIG_PATH, load_config
from .models import AppConfig, TranscriptionResult


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
    
    lines = text.split('\n')
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
    
    result = '\n'.join(cleaned_lines)
    return result.strip()


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
        state_root = (state_dir or (DEFAULT_CONFIG_PATH.parent / ".voxnote")).expanduser().resolve()
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


def _run_mlx_whisper(config: AppConfig, audio_path: Path, *, state_dir: Path | None = None) -> str:
    """Run mlx-whisper CLI and return plain text transcription."""
    mlx_whisper_cmd = _find_mlx_whisper()

    with tempfile.TemporaryDirectory(dir=str(state_dir) if state_dir else None) as tmpdir:
        output_dir = Path(tmpdir)
        expected_output_file = output_dir / f"{audio_path.stem}.txt"
        alt_output_file = output_dir / f"{audio_path.name}.txt"

        cmd: List[str] = [
            mlx_whisper_cmd,
            "--output-format",
            "txt",
            "--output-dir",
            str(output_dir),
            str(audio_path),
        ]

        if config.transcription.model:
            cmd.extend(["--model", config.transcription.model])
        if config.transcription.language != "auto":
            cmd.extend(["--language", config.transcription.language])

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

        output_file: Path | None = None
        if expected_output_file.exists():
            output_file = expected_output_file
        elif alt_output_file.exists():
            output_file = alt_output_file
        else:
            txt_files = sorted(output_dir.glob("*.txt"))
            if len(txt_files) == 1:
                output_file = txt_files[0]

        if output_file is None:
            _debug_log_whisper(
                config,
                audio_path=audio_path,
                cmd=cmd,
                returncode=result.returncode,
                stdout=result.stdout or "",
                stderr=result.stderr or "",
                error="mlx_whisper did not create a txt output file",
                state_dir=state_dir,
            )
            # Do not treat stdout as transcription: CLI may print progress or other text.
            stdout_len = len(result.stdout or "")
            stderr_len = len(result.stderr or "")
            msg = (
                "mlx_whisper did not create a txt output file "
                f"(stdout={stdout_len} chars, stderr={stderr_len} chars). "
                "The transcription may have failed silently. "
                "Check that mlx_whisper is working correctly: `mlx_whisper --help`"
            )
            if getattr(config.llm, "debug", False):
                msg += " Details: .voxnote/whisper_debug.jsonl"
            else:
                msg += " Enable debug mode in config.yaml (llm.debug: true) for detailed logs."
            raise RuntimeError(msg)

        text = output_file.read_text(encoding="utf-8").strip()
        if not text:
            _debug_log_whisper(
                config,
                audio_path=audio_path,
                cmd=cmd,
                returncode=result.returncode,
                stdout=result.stdout or "",
                stderr=result.stderr or "",
                error="empty transcription in txt output file",
                state_dir=state_dir,
            )
            raise RuntimeError(
                "Empty transcription from mlx_whisper. "
                "The audio file may contain only silence or be too quiet. "
                "Check the audio file or try with a different file."
            )

        return _remove_repetitions(text)


def transcribe_file(
    config: AppConfig, audio_path: Path, *, state_dir: Path | None = None
) -> TranscriptionResult:
    audio_path = audio_path.expanduser().resolve()
    if not audio_path.exists():
        raise FileNotFoundError(audio_path)

    _ensure_supported_extension(config, audio_path)

    text = _run_mlx_whisper(config, audio_path, state_dir=state_dir)
    return TranscriptionResult(audio_path=audio_path, text=text)


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
