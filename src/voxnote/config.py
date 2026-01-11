from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml

from .models import AppConfig


# Project root is three levels up: voxnote/src/voxnote/config.py -> voxnote
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.yaml"


def load_config(path: Optional[Path] = None) -> AppConfig:
    """Load application configuration from YAML file."""
    config_path = (path or DEFAULT_CONFIG_PATH).expanduser().resolve()
    if not config_path.exists():
        raise FileNotFoundError(
            f"Config file not found at {config_path}. "
            "Run `voxnote init` to create a default config. "
            f"Or specify a custom config path with `--config /path/to/config.yaml`"
        )

    with config_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    _normalize_paths(data, base_dir=config_path.parent)
    config = AppConfig.model_validate(data)

    # Ensure directories exist
    config.input_dir.mkdir(parents=True, exist_ok=True)
    config.output_dir.mkdir(parents=True, exist_ok=True)
    config.archive_dir.mkdir(parents=True, exist_ok=True)

    return config


def _normalize_paths(data: dict, base_dir: Path) -> None:
    paths = data.get("paths")
    if isinstance(paths, dict):
        for key in ("input", "output", "archive"):
            raw = paths.get(key)
            if not isinstance(raw, str) or not raw.strip():
                continue
            p = Path(raw).expanduser()
            if not p.is_absolute():
                p = (base_dir / p).resolve()
            paths[key] = str(p)

    sources = data.get("sources")
    if isinstance(sources, list):
        for src in sources:
            if not isinstance(src, dict):
                continue
            raw_src = src.get("path")
            if not isinstance(raw_src, str) or not raw_src.strip():
                continue
            p_src = Path(raw_src).expanduser()
            if not p_src.is_absolute():
                p_src = (base_dir / p_src).resolve()
            src["path"] = str(p_src)
