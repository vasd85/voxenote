from __future__ import annotations

import json
import plistlib
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class AudioMetadata:
    recorded_at: Optional[datetime]
    recorded_at_source: Optional[str]
    mdls: Optional[Dict[str, Any]]
    ffprobe: Optional[Dict[str, Any]]
    stat: Dict[str, Any]


def _run_mdls_plist(path: Path) -> Optional[Dict[str, Any]]:
    try:
        proc = subprocess.run(
            ["mdls", "-plist", "-", str(path)],
            check=True,
            capture_output=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None

    try:
        obj = plistlib.loads(proc.stdout)
    except Exception:
        return None

    if isinstance(obj, dict):
        return obj
    return None


def _run_ffprobe_json(path: Path) -> Optional[Dict[str, Any]]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None

    try:
        proc = subprocess.run(
            [
                ffprobe,
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None

    try:
        obj = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    if isinstance(obj, dict):
        return obj
    return None


def _parse_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None

    s = value.strip()
    if not s:
        return None

    s = s.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _extract_recorded_at_from_ffprobe(ffprobe: Dict[str, Any]) -> Optional[datetime]:
    fmt = ffprobe.get("format")
    if not isinstance(fmt, dict):
        return None
    tags = fmt.get("tags")
    if not isinstance(tags, dict):
        return None
    creation_time = tags.get("creation_time")
    return _parse_datetime(creation_time)


def _extract_recorded_at_from_mdls(mdls: Dict[str, Any]) -> Optional[datetime]:
    for key in (
        "kMDItemContentCreationDate",
        "kMDItemRecordingDate",
        "kMDItemFSCreationDate",
        "kMDItemFSContentChangeDate",
        "kMDItemContentModificationDate",
    ):
        dt = _parse_datetime(mdls.get(key))
        if dt:
            return dt
    return None


def collect_audio_metadata(audio_path: Path) -> AudioMetadata:
    audio_path = audio_path.expanduser().resolve()

    stat_obj = audio_path.stat()
    stat_meta: Dict[str, Any] = {
        "st_mtime": stat_obj.st_mtime,
        "st_ctime": stat_obj.st_ctime,
    }
    if hasattr(stat_obj, "st_birthtime"):
        stat_meta["st_birthtime"] = getattr(stat_obj, "st_birthtime")

    mdls = _run_mdls_plist(audio_path)
    ffprobe = _run_ffprobe_json(audio_path)

    recorded_at: Optional[datetime] = None
    recorded_at_source: Optional[str] = None

    if ffprobe:
        dt = _extract_recorded_at_from_ffprobe(ffprobe)
        if dt:
            recorded_at = dt
            recorded_at_source = "ffprobe.format.tags.creation_time"

    if not recorded_at and mdls:
        dt = _extract_recorded_at_from_mdls(mdls)
        if dt:
            recorded_at = dt
            recorded_at_source = "mdls"

    if not recorded_at:
        if "st_birthtime" in stat_meta:
            recorded_at = datetime.fromtimestamp(float(stat_meta["st_birthtime"]))
            recorded_at_source = "stat.st_birthtime"
        else:
            recorded_at = datetime.fromtimestamp(float(stat_meta["st_mtime"]))
            recorded_at_source = "stat.st_mtime"

    return AudioMetadata(
        recorded_at=recorded_at,
        recorded_at_source=recorded_at_source,
        mdls=mdls,
        ffprobe=ffprobe,
        stat=stat_meta,
    )


def _json_safe(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, bytes):
        try:
            return obj.decode("utf-8")
        except UnicodeDecodeError:
            return obj.hex()
    if isinstance(obj, set):
        return sorted(obj)
    return str(obj)


def format_audio_metadata_for_console(meta: AudioMetadata) -> str:
    # Extract a compact, human-oriented summary instead of dumping raw mdls/ffprobe.
    mdls: Dict[str, Any] = meta.mdls or {}
    ffprobe: Dict[str, Any] = meta.ffprobe or {}

    fmt: Dict[str, Any] = {}
    if isinstance(ffprobe.get("format"), dict):
        fmt = ffprobe["format"]  # type: ignore[assignment]

    streams = ffprobe.get("streams")
    audio_stream: Dict[str, Any] = {}
    if isinstance(streams, list):
        for s in streams:
            if isinstance(s, dict) and s.get("codec_type") == "audio":
                audio_stream = s
                break

    def _to_float(value: Any) -> Optional[float]:
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                return None
        return None

    def _to_int(value: Any) -> Optional[int]:
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            try:
                return int(value)
            except ValueError:
                return None
        return None

    # Duration
    duration_seconds: Optional[float] = None
    if "kMDItemDurationSeconds" in mdls:
        duration_seconds = _to_float(mdls.get("kMDItemDurationSeconds"))
    if duration_seconds is None and "duration" in fmt:
        duration_seconds = _to_float(fmt.get("duration"))

    duration_hms: Optional[str] = None
    if duration_seconds is not None:
        total = int(duration_seconds)
        h, m = divmod(total, 3600)
        m, s = divmod(m, 60)
        duration_hms = f"{h:02d}:{m:02d}:{s:02d}"

    # Audio technical params
    codec = audio_stream.get("codec_name")
    codec_long_name = audio_stream.get("codec_long_name")
    sample_rate = _to_int(audio_stream.get("sample_rate"))
    channels = _to_int(audio_stream.get("channels"))
    channel_layout = audio_stream.get("channel_layout")
    bit_rate = _to_int(audio_stream.get("bit_rate") or mdls.get("kMDItemAudioBitRate"))
    total_bit_rate = _to_int(fmt.get("bit_rate") or mdls.get("kMDItemTotalBitRate"))
    nb_frames = _to_int(audio_stream.get("nb_frames"))

    # Semantic / tags
    tags = fmt.get("tags") if isinstance(fmt.get("tags"), dict) else {}
    title = tags.get("title")
    voice_memo_uuid = tags.get("voice-memo-uuid")
    encoder = tags.get("encoder")

    stream_tags = audio_stream.get("tags") if isinstance(audio_stream.get("tags"), dict) else {}
    language = stream_tags.get("language")

    # File / container description
    kind = mdls.get("kMDItemKind")
    content_type = mdls.get("kMDItemContentType")

    # File timestamps from stat
    file_times: Dict[str, Any] = {}
    if "st_birthtime" in meta.stat:
        file_times["created"] = datetime.fromtimestamp(float(meta.stat["st_birthtime"]))
    if "st_mtime" in meta.stat:
        file_times["modified"] = datetime.fromtimestamp(float(meta.stat["st_mtime"]))
    if "st_ctime" in meta.stat:
        file_times["changed"] = datetime.fromtimestamp(float(meta.stat["st_ctime"]))

    payload = {
        "recorded_at": meta.recorded_at.isoformat() if meta.recorded_at else None,
        "recorded_at_source": meta.recorded_at_source,
        "duration_seconds": duration_seconds,
        "duration_hms": duration_hms,
        "codec": codec,
        "codec_long_name": codec_long_name,
        "sample_rate": sample_rate,
        "channels": channels,
        "channel_layout": channel_layout,
        "bit_rate": bit_rate,
        "total_bit_rate": total_bit_rate,
        "nb_frames": nb_frames,
        "title": title,
        "voice_memo_uuid": voice_memo_uuid,
        "encoder": encoder,
        "language": language,
        "kind": kind,
        "content_type": content_type,
        "file_times": file_times,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, default=_json_safe)

