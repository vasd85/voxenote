from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Set

from .audio_metadata import AudioMetadata
from .config import DEFAULT_CONFIG_PATH

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProcessedAudioEntry:
    processed_at: datetime
    original_hash: str
    original_name: str
    original_path: str
    archive_path: str
    note_path: str
    recorded_at: Optional[datetime] = None
    recorded_at_source: Optional[str] = None
    transcribed_file_hash: Optional[str] = None
    transcribed_path: Optional[str] = None


@dataclass(frozen=True)
class FailedTranscriptionEntry:
    created_at: datetime
    audio_path: str
    text: str
    error: str


@dataclass(frozen=True)
class CollectedAudioEntry:
    collected_at: datetime
    original_hash: str
    original_source_path: str
    original_source_name: str
    input_path: str


@dataclass(frozen=True)
class OriginalMetadataEntry:
    collected_at: datetime
    original_hash: str
    original_source_path: str
    original_source_name: str
    recorded_at: Optional[datetime]
    recorded_at_source: Optional[str]
    mdls: Optional[Dict[str, Any]]
    ffprobe: Optional[Dict[str, Any]]
    stat: Dict[str, Any]


def _state_dir(base: Optional[Path] = None) -> Path:
    return (base or (DEFAULT_CONFIG_PATH.parent / ".voxnote")).expanduser().resolve()


def processed_index_path(state_dir: Optional[Path] = None) -> Path:
    return _state_dir(state_dir) / "processed_audio.jsonl"


def failed_transcriptions_path(state_dir: Optional[Path] = None) -> Path:
    return _state_dir(state_dir) / "failed_transcriptions.jsonl"


def collected_audio_index_path(state_dir: Optional[Path] = None) -> Path:
    return _state_dir(state_dir) / "collected_audio.jsonl"

def original_metadata_index_path(state_dir: Optional[Path] = None) -> Path:
    return _state_dir(state_dir) / "original_metadata.jsonl"


def _json_default(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return obj.isoformat()
    return str(obj)


def compute_file_hash(file_path: Path, chunk_size: int = 65536) -> str:
    sha256 = hashlib.sha256()
    with file_path.open("rb") as f:
        while chunk := f.read(chunk_size):
            sha256.update(chunk)
    return sha256.hexdigest()


def load_processed_hashes(path: Optional[Path] = None, state_dir: Optional[Path] = None) -> Set[str]:
    index_path = path or processed_index_path(state_dir)
    if not index_path.exists():
        return set()

    hashes: Set[str] = set()
    for line in index_path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s:
            continue
        try:
            obj = json.loads(s)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            original_hash = obj.get("original_hash") or obj.get("file_hash")
            if isinstance(original_hash, str) and original_hash:
                hashes.add(original_hash)
    return hashes


def find_processed_entry(
    original_hash: str, path: Optional[Path] = None, state_dir: Optional[Path] = None
) -> Optional[Dict[str, Any]]:
    """Return stored processed entry for given original_hash, if present."""
    index_path = path or processed_index_path(state_dir)
    if not index_path.exists():
        return None

    for line in index_path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s:
            continue
        try:
            obj = json.loads(s)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        obj_hash = obj.get("original_hash")
        if obj_hash == original_hash:
            return obj
    return None


def purge_processed_entry(
    original_hash: str,
    path: Optional[Path] = None,
    state_dir: Optional[Path] = None,
) -> None:
    """Remove entries for given original_hash from processed_audio index."""
    index_path = path or processed_index_path(state_dir)
    if not index_path.exists():
        return

    lines = index_path.read_text(encoding="utf-8").splitlines()
    if not lines:
        return

    kept: list[str] = []
    changed = False
    for line in lines:
        s = line.strip()
        if not s:
            continue
        try:
            obj = json.loads(s)
        except json.JSONDecodeError:
            kept.append(line)
            continue
        if not isinstance(obj, dict):
            kept.append(line)
            continue
        obj_hash = obj.get("original_hash")
        if obj_hash == original_hash:
            changed = True
            continue
        kept.append(line)

    if not changed:
        return

    if kept:
        index_path.write_text("\n".join(kept) + "\n", encoding="utf-8")
    else:
        index_path.unlink()


def append_processed_entry(
    entry: ProcessedAudioEntry, path: Optional[Path] = None, state_dir: Optional[Path] = None
) -> None:
    index_path = path or processed_index_path(state_dir)
    index_path.parent.mkdir(parents=True, exist_ok=True)

    # Ensure there is at most one entry per original_hash: remove old ones, then append new.
    try:
        purge_processed_entry(entry.original_hash, path=index_path)
    except Exception as exc:
        logger.warning(
            f"Failed to purge old processed entry for original_hash={entry.original_hash}: {exc}",
            exc_info=True,
        )

    payload: Dict[str, Any] = asdict(entry)
    with index_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False, default=_json_default) + "\n")


def append_failed_transcription_entry(
    entry: FailedTranscriptionEntry,
    path: Optional[Path] = None,
    state_dir: Optional[Path] = None,
) -> None:
    index_path = path or failed_transcriptions_path(state_dir)
    index_path.parent.mkdir(parents=True, exist_ok=True)

    # Ensure there is at most one entry per audio_path: remove old ones, then append new
    try:
        purge_failed_transcription(Path(entry.audio_path), path=index_path)
    except Exception as exc:
        logger.warning(
            f"Failed to purge old failed transcription entry for {entry.audio_path}: {exc}",
            exc_info=True,
        )

    payload: Dict[str, Any] = asdict(entry)
    # Normalize for macOS (/tmp is often a symlink to /private/tmp) and for consistency with readers.
    payload["audio_path"] = str(Path(entry.audio_path).expanduser().resolve())
    with index_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False, default=_json_default) + "\n")


def get_failed_transcription_text(
    audio_path: Path,
    path: Optional[Path] = None,
    state_dir: Optional[Path] = None,
) -> Optional[str]:
    """Return previously saved transcription text for given audio, if any."""
    index_path = path or failed_transcriptions_path(state_dir)
    if not index_path.exists():
        return None

    target = str(audio_path.expanduser().resolve())
    for line in index_path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s:
            continue
        try:
            obj = json.loads(s)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        stored = obj.get("audio_path")
        if not isinstance(stored, str):
            continue
        try:
            stored_norm = str(Path(stored).expanduser().resolve())
        except Exception:
            stored_norm = stored
        if stored_norm == target and isinstance(obj.get("text"), str):
            return obj["text"]
    return None


def purge_failed_transcription(
    audio_path: Path,
    path: Optional[Path] = None,
    state_dir: Optional[Path] = None,
) -> None:
    """Remove entries for given audio from failed_transcriptions log."""
    index_path = path or failed_transcriptions_path(state_dir)
    if not index_path.exists():
        return

    target = str(audio_path.expanduser().resolve())
    lines = index_path.read_text(encoding="utf-8").splitlines()
    if not lines:
        return

    kept: list[str] = []
    changed = False
    for line in lines:
        s = line.strip()
        if not s:
            continue
        try:
            obj = json.loads(s)
        except json.JSONDecodeError:
            kept.append(line)
            continue
        if isinstance(obj, dict):
            stored = obj.get("audio_path")
            if isinstance(stored, str):
                try:
                    stored_norm = str(Path(stored).expanduser().resolve())
                except Exception:
                    stored_norm = stored
                if stored_norm == target:
                    changed = True
                    continue
        kept.append(line)

    if not changed:
        return

    if kept:
        index_path.write_text("\n".join(kept) + "\n", encoding="utf-8")
    else:
        index_path.unlink()


def append_collected_entry(
    entry: CollectedAudioEntry, path: Optional[Path] = None, state_dir: Optional[Path] = None
) -> None:
    index_path = path or collected_audio_index_path(state_dir)
    index_path.parent.mkdir(parents=True, exist_ok=True)

    payload: Dict[str, Any] = asdict(entry)
    with index_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False, default=_json_default) + "\n")


def load_collected_original_hashes(path: Optional[Path] = None, state_dir: Optional[Path] = None) -> Set[str]:
    index_path = path or collected_audio_index_path(state_dir)
    if not index_path.exists():
        return set()

    hashes: Set[str] = set()
    for line in index_path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s:
            continue
        try:
            obj = json.loads(s)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            original_hash = obj.get("original_hash")
            if isinstance(original_hash, str) and original_hash:
                hashes.add(original_hash)
    return hashes


def purge_original_metadata(
    original_hash: str,
    path: Optional[Path] = None,
    state_dir: Optional[Path] = None,
) -> None:
    """Remove entries for given original_hash from original_metadata index."""
    index_path = path or original_metadata_index_path(state_dir)
    if not index_path.exists():
        return

    lines = index_path.read_text(encoding="utf-8").splitlines()
    if not lines:
        return

    kept: list[str] = []
    changed = False
    for line in lines:
        s = line.strip()
        if not s:
            continue
        try:
            obj = json.loads(s)
        except json.JSONDecodeError:
            kept.append(line)
            continue
        if isinstance(obj, dict) and obj.get("original_hash") == original_hash:
            changed = True
            continue
        kept.append(line)

    if not changed:
        return

    if kept:
        index_path.write_text("\n".join(kept) + "\n", encoding="utf-8")
    else:
        index_path.unlink()


def save_original_metadata(
    *,
    original_hash: str,
    original_source_path: Path,
    original_source_name: str,
    meta: AudioMetadata,
    path: Optional[Path] = None,
    state_dir: Optional[Path] = None,
) -> None:
    """Upsert metadata for a given original_hash."""
    index_path = path or original_metadata_index_path(state_dir)
    index_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        purge_original_metadata(original_hash, path=index_path)
    except Exception as exc:
        logger.warning(
            f"Failed to purge old metadata entry for original_hash={original_hash}: {exc}",
            exc_info=True,
        )

    entry = OriginalMetadataEntry(
        collected_at=datetime.now(),
        original_hash=original_hash,
        original_source_path=str(original_source_path.expanduser().resolve()),
        original_source_name=original_source_name,
        recorded_at=meta.recorded_at,
        recorded_at_source=meta.recorded_at_source,
        mdls=meta.mdls,
        ffprobe=meta.ffprobe,
        stat=meta.stat,
    )
    payload: Dict[str, Any] = asdict(entry)
    with index_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False, default=_json_default) + "\n")


def load_original_metadata(
    original_hash: str,
    *,
    path: Optional[Path] = None,
    state_dir: Optional[Path] = None,
) -> Optional[AudioMetadata]:
    """Load stored metadata for given original_hash, if present."""
    index_path = path or original_metadata_index_path(state_dir)
    if not index_path.exists():
        return None

    last: Optional[Dict[str, Any]] = None
    for line in index_path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s:
            continue
        try:
            obj = json.loads(s)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("original_hash") == original_hash:
            last = obj

    if not last:
        return None

    recorded_at = last.get("recorded_at")
    recorded_at_dt: Optional[datetime]
    if isinstance(recorded_at, str):
        try:
            recorded_at_dt = datetime.fromisoformat(recorded_at)
        except ValueError:
            recorded_at_dt = None
    else:
        recorded_at_dt = None

    return AudioMetadata(
        recorded_at=recorded_at_dt,
        recorded_at_source=last.get("recorded_at_source") if isinstance(last.get("recorded_at_source"), str) else None,
        mdls=last.get("mdls") if isinstance(last.get("mdls"), dict) else None,
        ffprobe=last.get("ffprobe") if isinstance(last.get("ffprobe"), dict) else None,
        stat=last.get("stat") if isinstance(last.get("stat"), dict) else {},
    )

