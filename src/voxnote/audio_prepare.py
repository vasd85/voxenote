from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from errno import EXDEV
from pathlib import Path


def _find_ffmpeg() -> str:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError(
            "ffmpeg not found. Install it with 'brew install ffmpeg' "
            "and ensure it is on PATH. "
            "Verify installation: `ffmpeg -version` or run `voxnote doctor`."
        )
    return ffmpeg


def _denoise_model_path() -> Path:
    """
    Return the path to the shipped `std.rnnn` denoise model.

    The model is stored in the repository/package at:
    `src/voxnote/assets/denoise/std.rnnn`.
    """
    return Path(__file__).resolve().parent / "assets" / "denoise" / "std.rnnn"


def _ffmpeg_escape_filter_value(value: str) -> str:
    """
    Escape a value for use inside an ffmpeg filtergraph option.

    In filtergraphs, `:`, `,`, `\\` and spaces have special meaning.
    """
    return "".join(f"\\{ch}" if ch in {"\\", ":", ",", " "} else ch for ch in value)


def _default_filter_str() -> str:
    model_path = _denoise_model_path()
    if not model_path.exists():
        raise RuntimeError(
            "Denoise model file is missing. Expected: "
            f"{model_path}. "
            "Please ensure the denoise model is included with the application. "
            "Check the repository or reinstall the package."
        )

    # Keep the filtergraph robust even when the repository path contains spaces.
    model_arg = _ffmpeg_escape_filter_value(str(model_path))
    return (
        "highpass=f=80, lowpass=f=7800, "
        f"arnndn=m={model_arg}:mix=0.8, "
        "loudnorm=I=-16:LRA=11:TP=-1.5"
    )


def prepare_wav_for_vad(
    original_path: Path,
    out_wav_path: Path,
    filter_str: str | None = None,
    timeout_s: float = 3600,
) -> None:
    """
    Prepare audio file for Silero VAD: convert to WAV mono 16kHz with noise reduction.

    Args:
        original_path: Path to original audio file
        out_wav_path: Path where prepared WAV will be written
        filter_str: FFmpeg audio filter chain (default: highpass + denoise + loudnorm)

    Raises:
        RuntimeError: If ffmpeg fails or file cannot be processed
    """
    original_path = original_path.expanduser().resolve()
    if not original_path.exists():
        raise FileNotFoundError(
            f"Original audio file not found: {original_path}. "
            f"Check that the file exists and the path is correct."
        )

    ffmpeg = _find_ffmpeg()
    out_wav_path = out_wav_path.expanduser().resolve()
    out_wav_path.parent.mkdir(parents=True, exist_ok=True)

    if filter_str is None:
        filter_str = _default_filter_str()

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_file:
        tmp_path = Path(tmp_file.name)

    try:
        cmd = [
            ffmpeg,
            "-y",
            "-i",
            str(original_path),
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            "-af",
            filter_str,
            str(tmp_path),
        ]

        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=timeout_s)
        except subprocess.TimeoutExpired as exc:
            if tmp_path.exists():
                tmp_path.unlink()
            raise RuntimeError(
                f"ffmpeg preparation timed out after {exc.timeout} seconds. "
                "The audio file may be too large or the system is overloaded. "
                f"Increase timeout in config.yaml (processing.ffmpeg_prepare_timeout_s) or "
                "try processing a shorter audio file."
            ) from exc

        if not tmp_path.exists() or tmp_path.stat().st_size == 0:
            raise RuntimeError(
                "Prepared WAV file is empty or missing. "
                "The audio file may be corrupted or in an unsupported format. "
                "Check the original file or try converting it manually with ffmpeg."
            )

        try:
            os.replace(str(tmp_path), str(out_wav_path))
        except OSError as exc:
            if exc.errno != EXDEV:
                raise
            shutil.move(str(tmp_path), str(out_wav_path))
    except subprocess.CalledProcessError as exc:
        if tmp_path.exists():
            tmp_path.unlink()
        error_detail = exc.stderr.strip() if exc.stderr else str(exc)
        raise RuntimeError(
            f"ffmpeg preparation failed: {error_detail}. "
            "Check that the audio file is valid and not corrupted. "
            "Try converting it manually: `ffmpeg -i input.m4a output.wav`"
        ) from exc
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink()
        raise

