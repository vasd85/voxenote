from __future__ import annotations

from pathlib import Path

from voxnote import config


def _touch_config(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("paths: {}\n", encoding="utf-8")
    return path


def test_existing_local_voxnote_wins(isolated_home, tmp_path) -> None:
    cfg = _touch_config(tmp_path / "proj" / "config.yaml")
    local = cfg.parent / ".voxnote"
    local.mkdir()
    assert config.resolve_state_dir(cfg) == local.resolve()


def test_user_config_uses_app_support(isolated_home) -> None:
    cfg = _touch_config(config.user_config_path())
    assert config.resolve_state_dir(cfg) == config.default_state_dir()


def test_custom_config_keeps_state_beside_it(isolated_home, tmp_path) -> None:
    cfg = _touch_config(tmp_path / "custom" / "myconfig.yaml")
    # No pre-existing .voxnote and not the user path -> state lives beside the config.
    assert config.resolve_state_dir(cfg) == (cfg.parent / ".voxnote").resolve()
