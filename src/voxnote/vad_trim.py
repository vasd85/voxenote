from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from errno import EXDEV
from pathlib import Path
from typing import Any, List, Optional, Tuple

import torch

from .cache_paths import build_trimmed_cache_path, find_prepared_cache_path
from .config import DEFAULT_CONFIG_PATH
from .models import AppConfig, VADConfig
from .state import compute_file_hash


def _state_dir(base: Path | None = None) -> Path:
    return (base or (DEFAULT_CONFIG_PATH.parent / ".voxnote")).expanduser().resolve()


def get_trimmed_cache_path(
    *, original_hash: str, state_dir: Optional[Path] = None
) -> Path:
    state_root = _state_dir(state_dir)
    return build_trimmed_cache_path(original_hash=original_hash, state_dir=state_root)


def _find_ffmpeg() -> str:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError(
            "ffmpeg not found. Install it with 'brew install ffmpeg' "
            "and ensure it is on PATH. "
            "Verify installation: `ffmpeg -version` or run `voxnote doctor`."
        )
    return ffmpeg


def _load_silero_vad_model(state_dir: Optional[Path] = None) -> Tuple[Any, Any]:
    state_dir = _state_dir(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)

    torch_cache_dir = state_dir / "torch_cache"
    torch_cache_dir.mkdir(parents=True, exist_ok=True)

    os.environ.setdefault("TORCH_HOME", str(torch_cache_dir))

    try:
        model, utils = torch.hub.load(
            repo_or_dir="snakers4/silero-vad",
            model="silero_vad",
            force_reload=False,
            onnx=False,
            trust_repo=True,
        )
        return model, utils
    except ImportError as exc:
        if "torchaudio" in str(exc).lower():
            raise RuntimeError(
                "torchaudio is required for Silero VAD. "
                "Install it with 'uv sync' (it should be in dependencies). "
                "Or install manually: `pip install torchaudio`"
            ) from exc
        raise RuntimeError(
            f"Failed to load Silero VAD model: missing dependency: {exc}. "
            "Install required dependencies: `uv sync` or run `voxnote doctor`."
        ) from exc
    except Exception as exc:
        raise RuntimeError(
            f"Failed to load Silero VAD model: {exc}. "
            "Check your internet connection for first-time download. "
            "The model will be cached after the first successful download."
        ) from exc


def _decode_audio_to_wav(audio_path: Path, output_path: Path, timeout_s: float = 3600) -> None:
    ffmpeg = _find_ffmpeg()

    cmd = [
        ffmpeg,
        "-i",
        str(audio_path),
        "-ar",
        "16000",
        "-ac",
        "1",
        "-f",
        "wav",
        "-y",
        str(output_path),
    ]

    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"ffmpeg decode timed out after {exc.timeout} seconds. "
            "The audio file may be too large or the system is overloaded. "
            f"Increase timeout in config.yaml (processing.ffmpeg_trim_timeout_s) or "
            "try processing a shorter audio file."
        ) from exc
    except subprocess.CalledProcessError as exc:
        error_detail = exc.stderr.strip() if exc.stderr else str(exc)
        raise RuntimeError(
            f"ffmpeg decode failed: {error_detail}. "
            "Check that the audio file is valid and not corrupted. "
            "Try converting it manually: `ffmpeg -i input.m4a output.wav`"
        ) from exc


def _detect_speech_segments(
    wav_path: Path, model: Any, utils: Any, config: VADConfig
) -> List[dict]:
    get_speech_timestamps, save_audio, read_audio, collect_chunks, drop_chunks = utils

    wav = read_audio(str(wav_path), sampling_rate=16000)
    speech_timestamps = get_speech_timestamps(
        wav,
        model,
        threshold=config.threshold,
        neg_threshold=config.neg_threshold,
        min_silence_duration_ms=config.min_silence_duration_ms,
        min_speech_duration_ms=config.min_speech_duration_ms,
        return_seconds=True,
    )

    return speech_timestamps


def _build_ffmpeg_filter(
    segments: List[dict], pad_ms: int, sample_rate: int = 16000
) -> str:
    if not segments:
        return ""

    intervals: list[tuple[float, float]] = []
    pad_s = pad_ms / 1000.0
    for seg in segments:
        start_sec = max(0.0, float(seg["start"]) - pad_s)
        end_sec = float(seg["end"]) + pad_s
        if end_sec > start_sec:
            intervals.append((start_sec, end_sec))

    if not intervals:
        return ""

    intervals.sort(key=lambda x: x[0])
    merged: list[tuple[float, float]] = []
    cur_s, cur_e = intervals[0]
    for s, e in intervals[1:]:
        if s <= cur_e:
            cur_e = max(cur_e, e)
        else:
            merged.append((cur_s, cur_e))
            cur_s, cur_e = s, e
    merged.append((cur_s, cur_e))

    filter_parts = []
    for i, (start_sec, end_sec) in enumerate(merged):
        filter_parts.append(
            f"[0:a]atrim=start={start_sec}:end={end_sec},asetpts=PTS-STARTPTS[a{i}]"
        )

    concat_inputs = "".join([f"[a{i}]" for i in range(len(merged))])
    filter_parts.append(f"{concat_inputs}concat=n={len(merged)}:v=0:a=1[out]")

    return ";".join(filter_parts)


def _get_audio_codec(ext: str) -> str:
    ext_lower = ext.lower().lstrip(".")
    codec_map = {
        "m4a": "aac",
        "mp3": "libmp3lame",
        "wav": "pcm_s16le",
        "ogg": "libvorbis",
        "flac": "flac",
    }
    return codec_map.get(ext_lower, "aac")


def _trim_audio_with_ffmpeg(
    input_path: Path, output_path: Path, segments: List[dict], pad_ms: int, timeout_s: float = 3600
) -> None:
    if not segments:
        raise ValueError(
            "No speech segments detected. "
            "The audio file may contain only silence or be too quiet. "
            "Try adjusting VAD threshold in config.yaml (vad.threshold) or check the audio file."
        )

    ffmpeg = _find_ffmpeg()
    filter_complex = _build_ffmpeg_filter(segments, pad_ms)
    codec = _get_audio_codec(output_path.suffix)

    cmd = [
        ffmpeg,
        "-i",
        str(input_path),
        "-filter_complex",
        filter_complex,
        "-map",
        "[out]",
        "-c:a",
        codec,
        "-y",
        str(output_path),
    ]

    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"ffmpeg trim timed out after {exc.timeout} seconds. "
            "The audio file may be too large or the system is overloaded. "
            f"Increase timeout in config.yaml (processing.ffmpeg_trim_timeout_s) or "
            "try processing a shorter audio file."
        ) from exc
    except subprocess.CalledProcessError as exc:
        error_detail = exc.stderr.strip() if exc.stderr else str(exc)
        raise RuntimeError(
            f"ffmpeg trim failed: {error_detail}. "
            "Check that the audio file is valid and the VAD segments are correct. "
            "Try running with --dry-run to see detected segments."
        ) from exc


def trim_audio_file(
    config: AppConfig, audio_path: Path, dry_run: bool = False, state_dir: Optional[Path] = None
) -> bool:
    audio_path = audio_path.expanduser().resolve()
    if not audio_path.exists():
        raise FileNotFoundError(
            f"Audio file not found: {audio_path}. "
            f"Check that the file exists and the path is correct."
        )

    ext = audio_path.suffix.lower().lstrip(".")
    if ext not in config.processing.supported_formats:
        supported = ", ".join(config.processing.supported_formats)
        raise ValueError(
            f"Unsupported audio format: {ext}. "
            f"Supported formats: {supported}. "
            f"Convert the file or add the format to config.yaml processing.supported_formats."
        )

    _find_ffmpeg()

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        wav_path = tmp_path / "decoded.wav"
        trimmed_tmp_path = tmp_path / "trimmed.wav"

        original_hash = compute_file_hash(audio_path)
        prepared = find_prepared_cache_path(original_hash=original_hash, state_dir=_state_dir(state_dir))

        vad_input: Path
        if prepared is not None and prepared.exists():
            vad_input = prepared
        else:
            _decode_audio_to_wav(audio_path, wav_path, timeout_s=config.processing.ffmpeg_trim_timeout_s)
            vad_input = wav_path

        model, utils = _load_silero_vad_model(state_dir)
        segments = _detect_speech_segments(vad_input, model, utils, config.vad)

        if not segments:
            return False

        _trim_audio_with_ffmpeg(
            vad_input, trimmed_tmp_path, segments, config.vad.speech_pad_ms, timeout_s=config.processing.ffmpeg_trim_timeout_s
        )

        if not trimmed_tmp_path.exists() or trimmed_tmp_path.stat().st_size == 0:
            raise RuntimeError(
                "Trimmed file is empty or missing. "
                "The VAD processing may have failed. "
                "Check the audio file or try adjusting VAD parameters in config.yaml."
            )

        if dry_run:
            return True

        cache_path = get_trimmed_cache_path(original_hash=original_hash, state_dir=state_dir)

        cache_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(str(trimmed_tmp_path), str(cache_path))
        except OSError as exc:
            if exc.errno != EXDEV:
                raise
            shutil.move(str(trimmed_tmp_path), str(cache_path))
        return True

