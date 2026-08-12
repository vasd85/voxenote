from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import load_config, resolve_config_path, resolve_state_dir
from .models import AppConfig


@dataclass(frozen=True)
class RuntimeContext:
    config_path: Path
    config: AppConfig
    project_root: Path
    state_dir: Path


def build_runtime(config_path: Path | None = None) -> RuntimeContext:
    cfg_path = resolve_config_path(config_path)
    config = load_config(cfg_path)
    project_root = cfg_path.parent
    state_dir = resolve_state_dir(cfg_path)
    state_dir.mkdir(parents=True, exist_ok=True)
    return RuntimeContext(
        config_path=cfg_path,
        config=config,
        project_root=project_root,
        state_dir=state_dir,
    )
