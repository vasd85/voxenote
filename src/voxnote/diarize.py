"""Speaker diarization boundary.

Owns the sherpa-onnx offline diarization engine, its model files, and the audio decoding
it needs. The rest of the pipeline only sees `diarize_audio()` and the helpers below, so a
second backend can be added here without touching workflow logic.

Models are downloaded once from the k2-fsa GitHub releases (no Hugging Face account) into
`<state_dir>/diarization/`; everything works offline afterwards.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import wave
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional, Tuple

import requests

from .config import DEFAULT_CONFIG_PATH
from .models import AppConfig, DiarizationResult, SpeakerTurn

DIARIZATION_SAMPLE_RATE = 16000
DIARIZATION_MODELS_DIRNAME = "diarization"
DIARIZATION_DISABLED_FINGERPRINT = "off"

SEGMENTATION_ARCHIVE_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-segmentation-models/"
    "sherpa-onnx-pyannote-segmentation-3-0.tar.bz2"
)
SEGMENTATION_ARCHIVE_MEMBER = "sherpa-onnx-pyannote-segmentation-3-0/model.onnx"
SEGMENTATION_RELEASE_PAGE = "https://github.com/k2-fsa/sherpa-onnx/releases/tag/speaker-segmentation-models"
DEFAULT_SEGMENTATION_RELPATH = SEGMENTATION_ARCHIVE_MEMBER

# The upstream release tag really is misspelled like this.
EMBEDDING_RELEASE_BASE_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/"
EMBEDDING_RELEASE_PAGE = "https://github.com/k2-fsa/sherpa-onnx/releases/tag/speaker-recongition-models"
DEFAULT_EMBEDDING_FILENAME = "3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx"


def _state_dir(base: Optional[Path] = None) -> Path:
    return (base or (DEFAULT_CONFIG_PATH.parent / ".voxnote")).expanduser().resolve()


def diarization_models_dir(state_dir: Optional[Path] = None) -> Path:
    return _state_dir(state_dir) / DIARIZATION_MODELS_DIRNAME


def _resolve_model_path(value: str, *, models_dir: Path, default_relpath: str) -> Path:
    """Resolve a config override: empty = default, relative = under models_dir, absolute = as is."""
    raw = (value or "").strip()
    if not raw:
        return models_dir / default_relpath
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path
    return models_dir / path


def _is_downloadable_embedding(value: str) -> bool:
    """Only a bare filename maps to a file in the k2-fsa release; a path means "user supplied"."""
    raw = (value or "").strip()
    if not raw:
        return True
    path = Path(raw)
    return not path.is_absolute() and path.parent == Path(".")


def resolve_diarization_models(config: AppConfig, *, state_dir: Optional[Path] = None) -> Tuple[Path, Path]:
    """Return (segmentation_model_path, embedding_model_path) without touching the network."""
    models_dir = diarization_models_dir(state_dir)
    segmentation = _resolve_model_path(
        config.diarization.segmentation_model,
        models_dir=models_dir,
        default_relpath=DEFAULT_SEGMENTATION_RELPATH,
    )
    embedding = _resolve_model_path(
        config.diarization.embedding_model,
        models_dir=models_dir,
        default_relpath=DEFAULT_EMBEDDING_FILENAME,
    )
    return segmentation, embedding


def _download_file(url: str, target: Path, *, timeout_s: float, what: str, manual_hint: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    try:
        with requests.get(url, stream=True, timeout=(10, timeout_s)) as resp:
            resp.raise_for_status()
            with partial.open("wb") as handle:
                for chunk in resp.iter_content(chunk_size=1 << 20):
                    if chunk:
                        handle.write(chunk)
        os.replace(str(partial), str(target))
    except Exception as exc:
        partial.unlink(missing_ok=True)
        raise RuntimeError(f"Failed to download the {what} from {url}: {exc}. {manual_hint}") from exc


def _extract_archive_member(archive: Path, member_name: str, target: Path) -> None:
    """Extract one known member by exact name (never uses archive paths as output paths)."""
    with tarfile.open(archive, "r:bz2") as tar:
        try:
            member = tar.getmember(member_name)
        except KeyError as exc:
            raise RuntimeError(
                f"Archive {archive.name} does not contain '{member_name}'. "
                f"Download the model manually from {SEGMENTATION_RELEASE_PAGE} and place it at {target}."
            ) from exc
        if not member.isfile():
            raise RuntimeError(f"Archive member '{member_name}' is not a regular file.")
        extracted = tar.extractfile(member)
        if extracted is None:
            raise RuntimeError(f"Could not read '{member_name}' from {archive.name}.")

        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + ".part")
        with partial.open("wb") as handle:
            shutil.copyfileobj(extracted, handle)
        os.replace(str(partial), str(target))


def _ensure_segmentation_model(config: AppConfig, target: Path) -> None:
    if (config.diarization.segmentation_model or "").strip():
        raise RuntimeError(
            f"Diarization segmentation model not found: {target}. "
            f"Download {SEGMENTATION_ARCHIVE_URL}, extract '{SEGMENTATION_ARCHIVE_MEMBER}' and put it there, "
            "or clear `diarization.segmentation_model` in config.yaml to download the default model."
        )

    with tempfile.TemporaryDirectory() as tmpdir:
        archive = Path(tmpdir) / "sherpa-onnx-pyannote-segmentation-3-0.tar.bz2"
        _download_file(
            SEGMENTATION_ARCHIVE_URL,
            archive,
            timeout_s=config.diarization.download_timeout_s,
            what="diarization segmentation model",
            manual_hint=(
                f"Download it manually from {SEGMENTATION_RELEASE_PAGE}, "
                f"extract '{SEGMENTATION_ARCHIVE_MEMBER}' and place it at {target}."
            ),
        )
        _extract_archive_member(archive, SEGMENTATION_ARCHIVE_MEMBER, target)


def _ensure_embedding_model(config: AppConfig, target: Path) -> None:
    if not _is_downloadable_embedding(config.diarization.embedding_model):
        raise RuntimeError(
            f"Diarization speaker embedding model not found: {target}. "
            "Put the model file there, or set `diarization.embedding_model` to a file name from "
            f"{EMBEDDING_RELEASE_PAGE} to have voxnote download it."
        )

    _download_file(
        EMBEDDING_RELEASE_BASE_URL + target.name,
        target,
        timeout_s=config.diarization.download_timeout_s,
        what="diarization speaker embedding model",
        manual_hint=(
            f"Check that '{target.name}' exists on {EMBEDDING_RELEASE_PAGE}, "
            f"or download it manually and place it at {target}."
        ),
    )


def diarization_models_ready(config: AppConfig, *, state_dir: Optional[Path] = None) -> bool:
    """True when nothing has to be fetched, so callers can announce a one-time download."""
    return all(path.exists() for path in resolve_diarization_models(config, state_dir=state_dir))


def ensure_diarization_models(config: AppConfig, *, state_dir: Optional[Path] = None) -> Tuple[Path, Path]:
    """Resolve both model files, downloading the defaults once if they are missing."""
    segmentation, embedding = resolve_diarization_models(config, state_dir=state_dir)
    if not segmentation.exists():
        _ensure_segmentation_model(config, segmentation)
    if not embedding.exists():
        _ensure_embedding_model(config, embedding)
    return segmentation, embedding


def _import_sherpa_onnx() -> Any:
    try:
        import sherpa_onnx
    except ImportError as exc:
        raise RuntimeError(
            f"sherpa-onnx is not available: {exc}. "
            "Install it with `uv sync` (or `pip install sherpa-onnx sherpa-onnx-core`), "
            "then verify with `voxnote doctor`."
        ) from exc
    return sherpa_onnx


def probe_diarization_engine() -> Optional[str]:
    """Return None when the engine is importable, otherwise an actionable error message."""
    try:
        _import_sherpa_onnx()
    except RuntimeError as exc:
        return str(exc)
    return None


def _find_ffmpeg() -> str:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError(
            "ffmpeg not found. Install it with 'brew install ffmpeg' and ensure it is on PATH. "
            "Verify installation: `ffmpeg -version` or run `voxnote doctor`."
        )
    return ffmpeg


def _decode_to_mono_16k_wav(audio_path: Path, output_path: Path, *, timeout_s: float) -> None:
    cmd = [
        _find_ffmpeg(),
        "-i",
        str(audio_path),
        "-ac",
        "1",
        "-ar",
        str(DIARIZATION_SAMPLE_RATE),
        "-c:a",
        "pcm_s16le",
        "-f",
        "wav",
        "-y",
        str(output_path),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"ffmpeg decode for diarization timed out after {exc.timeout} seconds. "
            "Increase processing.ffmpeg_prepare_timeout_s in config.yaml or process a shorter file."
        ) from exc
    except subprocess.CalledProcessError as exc:
        error_detail = exc.stderr.strip() if exc.stderr else str(exc)
        raise RuntimeError(
            f"ffmpeg decode for diarization failed: {error_detail}. "
            "Check that the audio file is valid, or run `voxnote prepare-vad` first."
        ) from exc


def _read_wav_mono_16k(path: Path) -> Optional[Any]:
    """Read a 16 kHz mono 16-bit WAV as float32 samples; return None when the format differs."""
    import numpy as np

    try:
        with wave.open(str(path), "rb") as wav_file:
            if (
                wav_file.getnchannels() != 1
                or wav_file.getframerate() != DIARIZATION_SAMPLE_RATE
                or wav_file.getsampwidth() != 2
            ):
                return None
            frames = wav_file.readframes(wav_file.getnframes())
    except (wave.Error, OSError, EOFError):
        return None

    return np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0


def _load_samples(config: AppConfig, audio_path: Path) -> Any:
    """Load mono float32 samples at 16 kHz, decoding with ffmpeg only when needed."""
    samples = _read_wav_mono_16k(audio_path)
    if samples is not None:
        return samples

    with tempfile.TemporaryDirectory() as tmpdir:
        decoded = Path(tmpdir) / "diarization.wav"
        _decode_to_mono_16k_wav(audio_path, decoded, timeout_s=config.processing.ffmpeg_prepare_timeout_s)
        samples = _read_wav_mono_16k(decoded)
        if samples is None:
            raise RuntimeError(
                f"Could not decode {audio_path.name} to 16 kHz mono for diarization. "
                "Run `voxnote prepare-vad` for this file, or check the audio with `ffprobe`."
            )
        return samples


def _build_engine(sherpa_onnx: Any, config: AppConfig, *, segmentation: Path, embedding: Path) -> Any:
    diarization = config.diarization
    engine_config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(model=str(segmentation)),
            num_threads=diarization.num_threads,
            debug=False,
            provider="cpu",
        ),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=str(embedding),
            num_threads=diarization.num_threads,
            debug=False,
            provider="cpu",
        ),
        clustering=sherpa_onnx.FastClusteringConfig(
            num_clusters=diarization.num_speakers if diarization.num_speakers > 0 else -1,
            threshold=diarization.cluster_threshold,
        ),
        min_duration_on=diarization.min_duration_on,
        min_duration_off=diarization.min_duration_off,
    )
    if not engine_config.validate():
        raise RuntimeError(
            "Invalid diarization configuration. Check the `diarization:` section in config.yaml "
            f"and that both model files are readable: {segmentation}, {embedding}. "
            "Run `voxnote doctor` for details."
        )
    return sherpa_onnx.OfflineSpeakerDiarization(engine_config)


def _debug_log_diarization(
    config: AppConfig,
    *,
    audio_path: Path,
    result: DiarizationResult,
    sample_count: int,
    state_dir: Optional[Path] = None,
) -> None:
    """Dump turn timings (never transcript text) when debug mode is enabled."""
    if not getattr(config.llm, "debug", False):
        return

    try:
        state_root = _state_dir(state_dir)
        state_root.mkdir(parents=True, exist_ok=True)
        path = state_root / "diarization_debug.jsonl"

        entry = {
            "ts": datetime.now().isoformat(),
            "audio_path": str(audio_path),
            "sample_count": sample_count,
            "duration_s": round(sample_count / DIARIZATION_SAMPLE_RATE, 3),
            "speaker_count": result.speaker_count,
            "turn_count": len(result.turns),
            "config": _fingerprint_payload(config),
            "turns": [[round(turn.start, 3), round(turn.end, 3), turn.speaker] for turn in result.turns],
        }

        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        # Best-effort debug logging; never break main flow
        pass


def diarize_audio(
    config: AppConfig,
    audio_path: Path,
    *,
    state_dir: Optional[Path] = None,
    progress_callback: Optional[Callable[[int, int], int]] = None,
) -> DiarizationResult:
    """Run offline speaker diarization on one audio file."""
    audio_path = audio_path.expanduser().resolve()
    if not audio_path.exists():
        raise FileNotFoundError(audio_path)

    if config.diarization.backend != "sherpa_onnx":
        raise RuntimeError(
            f"Unsupported diarization backend: {config.diarization.backend}. "
            "Set `diarization.backend: sherpa_onnx` in config.yaml."
        )

    segmentation, embedding = ensure_diarization_models(config, state_dir=state_dir)
    sherpa_onnx = _import_sherpa_onnx()
    samples = _load_samples(config, audio_path)

    engine = _build_engine(sherpa_onnx, config, segmentation=segmentation, embedding=embedding)
    if engine.sample_rate != DIARIZATION_SAMPLE_RATE:
        raise RuntimeError(
            f"The segmentation model expects {engine.sample_rate} Hz audio but voxnote feeds "
            f"{DIARIZATION_SAMPLE_RATE} Hz. Use the default segmentation model "
            "(clear `diarization.segmentation_model` in config.yaml)."
        )

    if progress_callback is not None:
        raw_segments = engine.process(samples, callback=progress_callback).sort_by_start_time()
    else:
        raw_segments = engine.process(samples).sort_by_start_time()

    turns = [
        SpeakerTurn(start=float(segment.start), end=float(segment.end), speaker=int(segment.speaker))
        for segment in raw_segments
    ]
    result = DiarizationResult(
        audio_path=audio_path,
        turns=turns,
        speaker_count=len({turn.speaker for turn in turns}),
    )

    _debug_log_diarization(
        config,
        audio_path=audio_path,
        result=result,
        sample_count=int(len(samples)),
        state_dir=state_dir,
    )
    return result


def _fingerprint_payload(config: AppConfig) -> dict:
    """Only the settings that change diarization output — not threads or timeouts."""
    diarization = config.diarization
    return {
        "backend": diarization.backend,
        "num_speakers": diarization.num_speakers,
        "cluster_threshold": diarization.cluster_threshold,
        "min_duration_on": diarization.min_duration_on,
        "min_duration_off": diarization.min_duration_off,
        "segmentation_model": (diarization.segmentation_model or "").strip(),
        "embedding_model": (diarization.embedding_model or "").strip(),
    }


def diarization_fingerprint(config: AppConfig) -> str:
    """Identify the diarization settings a note was produced with (see `process` idempotency)."""
    if not config.diarization.enabled:
        return DIARIZATION_DISABLED_FINGERPRINT

    payload = json.dumps(_fingerprint_payload(config), sort_keys=True, ensure_ascii=False)
    return "on:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
