from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import yaml

from .models import AppConfig


# Dev fallback only: repo root, three levels up (voxnote/src/voxnote/config.py -> voxnote).
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.yaml"

# Environment variable that overrides config discovery when set.
ENV_CONFIG_VAR = "VOXNOTE_CONFIG"


def _user_config_dir() -> Path:
    """User-level config directory (~/.config/voxnote, honoring XDG_CONFIG_HOME)."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg and Path(xdg).is_absolute() else (Path.home() / ".config")
    return base / "voxnote"


def user_config_path() -> Path:
    """User-level config file path (~/.config/voxnote/config.yaml)."""
    return _user_config_dir() / "config.yaml"


def default_state_dir() -> Path:
    """macOS app-support state directory used for a global install."""
    return Path.home() / "Library" / "Application Support" / "voxnote"


def resolve_config_path(explicit: Optional[Path] = None) -> Path:
    """Resolve which config.yaml to use.

    Priority: explicit (--config) > $VOXNOTE_CONFIG > ~/.config/voxnote/config.yaml (if it
    exists) > ./config.yaml in CWD (if it exists) > repo-root dev fallback. Existence gates
    only the user and CWD rungs; explicit/env/dev-fallback are returned as-is so a missing
    explicit/env path still surfaces the FileNotFoundError in load_config.
    """
    if explicit is not None:
        return explicit.expanduser().resolve()

    env = os.environ.get(ENV_CONFIG_VAR)
    if env and env.strip():
        return Path(env).expanduser().resolve()

    user = user_config_path()
    if user.exists():
        return user.resolve()

    cwd_config = Path.cwd() / "config.yaml"
    if cwd_config.exists():
        return cwd_config.resolve()

    return DEFAULT_CONFIG_PATH.expanduser().resolve()


def resolve_state_dir(config_path: Path) -> Path:
    """Resolve the .voxnote-equivalent state dir for a config path.

    Keeps an existing project-local `.voxnote/` (backward compat), uses the macOS app-support
    dir for the global user config, and otherwise places state beside the config.
    """
    config_path = config_path.expanduser().resolve()
    local = config_path.parent / ".voxnote"
    if local.exists():
        return local
    if config_path == user_config_path().expanduser().resolve():
        return default_state_dir()
    return local


def load_config(path: Optional[Path] = None) -> AppConfig:
    """Load application configuration from YAML file."""
    config_path = resolve_config_path(path)
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
