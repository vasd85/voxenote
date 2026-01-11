from __future__ import annotations

import re
from pathlib import Path
from typing import Optional


_HASH_PREFIX_RE = re.compile(r"^(?P<hash>[0-9a-f]{64})_(?P<rest>.+)$")


def strip_hash_prefix(filename: str) -> tuple[Optional[str], str]:
    """
    If filename starts with '<64-hex>_', return (hash, rest); otherwise (None, filename).
    """
    m = _HASH_PREFIX_RE.match(filename)
    if not m:
        return None, filename
    return m.group("hash"), m.group("rest")


def prepared_cache_dir(state_dir: Path) -> Path:
    return state_dir / "prepared"


def trimmed_cache_dir(state_dir: Path) -> Path:
    return state_dir / "trimmed"


def find_prepared_cache_path(*, original_hash: str, state_dir: Path) -> Optional[Path]:
    """
    Find existing prepared cache file for an original hash.

    We allow any '<hash>_*.wav' name to keep the cache resilient to input renames.
    """
    cache_dir = prepared_cache_dir(state_dir)
    if not cache_dir.exists():
        return None

    matches = sorted(cache_dir.glob(f"{original_hash}_*.wav"), key=lambda p: p.stat().st_mtime, reverse=True)
    return matches[0] if matches else None


def build_prepared_cache_path(*, original_hash: str, original_name: str, state_dir: Path) -> Path:
    """
    Build deterministic prepared cache path for an original hash.
    """
    cache_dir = prepared_cache_dir(state_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    _, rest = strip_hash_prefix(original_name)
    stem = Path(rest).stem or "audio"
    safe_stem = _slug_stem(stem)
    return cache_dir / f"{original_hash}_{safe_stem}.wav"


def build_trimmed_cache_path(*, original_hash: str, state_dir: Path) -> Path:
    cache_dir = trimmed_cache_dir(state_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"{original_hash}.wav"


def _slug_stem(value: str, *, max_len: int = 80) -> str:
    value = value.strip()
    if not value:
        return "audio"
    out: list[str] = []
    prev_dash = False
    for ch in value:
        if ch.isalnum():
            out.append(ch.lower())
            prev_dash = False
        elif ch in {" ", "-", "_"}:
            if out and not prev_dash:
                out.append("-")
                prev_dash = True
        if len(out) >= max_len:
            break
    res = "".join(out).strip("-")
    return res or "audio"

