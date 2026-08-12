from __future__ import annotations

from click.testing import CliRunner

from voxnote import config
from voxnote.cli import main


def test_load_template_text_falls_back_to_packaged(monkeypatch, tmp_path) -> None:
    # No repo-local template next to the (patched) dev config -> packaged asset is used.
    monkeypatch.setattr(config, "DEFAULT_CONFIG_PATH", tmp_path / "config.yaml")
    text = config.load_template_text()
    assert "system_prompt" in text
    assert "paths:" in text


def test_load_template_text_prefers_repo_local(monkeypatch, tmp_path) -> None:
    repo_local = tmp_path / "config.example.yaml"
    repo_local.write_text("sentinel: true\n", encoding="utf-8")
    monkeypatch.setattr(config, "DEFAULT_CONFIG_PATH", tmp_path / "config.yaml")
    assert config.load_template_text() == "sentinel: true\n"


def test_init_seeds_user_config(isolated_home) -> None:
    result = CliRunner().invoke(main, ["init"])
    assert result.exit_code == 0, result.output

    cfg = config.user_config_path()
    assert cfg.exists()
    assert "paths:" in cfg.read_text(encoding="utf-8")
    # Template paths (~/Documents/voxnote/...) resolve under the patched HOME.
    assert (isolated_home / "Documents" / "voxnote" / "input").is_dir()


def test_init_no_force_on_existing_does_not_overwrite(isolated_home) -> None:
    cfg = config.user_config_path()
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("existing: true\n", encoding="utf-8")

    result = CliRunner().invoke(main, ["init"])
    assert result.exit_code == 0
    assert "already exists" in result.output
    assert cfg.read_text(encoding="utf-8") == "existing: true\n"


def test_init_force_overwrites(isolated_home) -> None:
    cfg = config.user_config_path()
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("existing: true\n", encoding="utf-8")

    result = CliRunner().invoke(main, ["init", "--force"])
    assert result.exit_code == 0
    assert "existing: true" not in cfg.read_text(encoding="utf-8")


def test_init_honors_explicit_config(isolated_home, tmp_path) -> None:
    target = tmp_path / "custom" / "myconfig.yaml"
    result = CliRunner().invoke(main, ["--config", str(target), "init"])
    assert result.exit_code == 0, result.output
    assert target.exists()
    assert not config.user_config_path().exists()  # did not seed the user path
