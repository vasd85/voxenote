from __future__ import annotations

from pathlib import Path

from voxnote import config


def _touch_config(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("paths: {}\n", encoding="utf-8")
    return path


def test_explicit_wins_over_env(isolated_home, monkeypatch, tmp_path) -> None:
    explicit = tmp_path / "explicit.yaml"
    monkeypatch.setenv("VOXNOTE_CONFIG", str(tmp_path / "env.yaml"))
    assert config.resolve_config_path(explicit) == explicit.resolve()


def test_env_wins_over_user_path(isolated_home, monkeypatch, tmp_path) -> None:
    env_cfg = tmp_path / "env.yaml"
    monkeypatch.setenv("VOXNOTE_CONFIG", str(env_cfg))
    _touch_config(config.user_config_path())  # exists, but env should still win
    assert config.resolve_config_path() == env_cfg.resolve()


def test_user_path_when_it_exists(isolated_home) -> None:
    user = _touch_config(config.user_config_path())
    assert config.resolve_config_path() == user.resolve()


def test_xdg_config_home_respected(isolated_home, monkeypatch, tmp_path) -> None:
    xdg = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    user = _touch_config(xdg / "voxnote" / "config.yaml")
    assert config.resolve_config_path() == user.resolve()


def test_cwd_fallback(isolated_home, monkeypatch, tmp_path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    cwd_cfg = _touch_config(work / "config.yaml")
    monkeypatch.chdir(work)
    assert config.resolve_config_path() == cwd_cfg.resolve()


def test_user_path_beats_cwd(isolated_home, monkeypatch, tmp_path) -> None:
    user = _touch_config(config.user_config_path())
    work = tmp_path / "work"
    work.mkdir()
    _touch_config(work / "config.yaml")
    monkeypatch.chdir(work)
    assert config.resolve_config_path() == user.resolve()


def test_dev_fallback_last(isolated_home, monkeypatch, tmp_path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.chdir(empty)
    # No env var, no user config, no ./config.yaml -> repo-root dev fallback.
    assert config.resolve_config_path() == config.DEFAULT_CONFIG_PATH.expanduser().resolve()
