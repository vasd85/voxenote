from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import List

import requests

from .models import AppConfig
from .runtime import RuntimeContext


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    info: str


def _check_executable(name: str, description: str) -> CheckResult:
    path = shutil.which(name)
    if path:
        return CheckResult(name=description, ok=True, info=path)
    return CheckResult(name=description, ok=False, info="Not found in PATH")


def _check_ollama(base_url: str) -> CheckResult:
    url = base_url.rstrip("/") + "/api/tags"
    try:
        resp = requests.get(url, timeout=5)
    except requests.RequestException as exc:
        return CheckResult(name="Ollama API", ok=False, info=f"Connection error: {exc}")

    if resp.status_code == 200:
        return CheckResult(name="Ollama API", ok=True, info="Reachable")
    return CheckResult(name="Ollama API", ok=False, info=f"HTTP {resp.status_code}")


def _check_denoise_model() -> CheckResult:
    from .audio_prepare import _denoise_model_path

    model_path = _denoise_model_path()
    if model_path.exists():
        return CheckResult(name="Denoise model", ok=True, info=str(model_path))
    return CheckResult(name="Denoise model", ok=False, info="std.rnnn is missing")


def _check_source_access(config: AppConfig) -> CheckResult:
    """Probe read access to each configured source dir (surfaces macOS Full Disk Access gaps)."""
    sources = config.sources
    if not sources:
        return CheckResult(
            name="Source access",
            ok=True,
            info="No sources configured (set `sources` in config.yaml or pass --source).",
        )

    problems: list[str] = []
    for src in sources:
        path = Path(src.path).expanduser()
        try:
            with os.scandir(path) as it:
                for _ in it:
                    break
        except FileNotFoundError:
            problems.append(f"{path}: missing")
        except PermissionError:
            problems.append(f"{path}: permission denied")
        except OSError as exc:
            problems.append(f"{path}: {exc.strerror or exc}")

    if not problems:
        return CheckResult(name="Source access", ok=True, info=f"{len(sources)} source(s) readable")

    info = "; ".join(problems)
    if any("permission denied" in p for p in problems):
        info += (
            ". Grant Full Disk Access to the terminal you use for `voxnote collect` "
            "(a dedicated terminal is recommended), then fully quit & reopen it."
        )
    return CheckResult(name="Source access", ok=False, info=info)


def _check_mlx_whisper() -> CheckResult:
    """Check for mlx_whisper executable, supporting both naming variants."""
    import sys
    from pathlib import Path

    path = shutil.which("mlx_whisper") or shutil.which("mlx-whisper")
    if path:
        return CheckResult(name="mlx_whisper", ok=True, info=path)

    venv_bin_underscore = Path(sys.executable).parent / "mlx_whisper"
    venv_bin_dash = Path(sys.executable).parent / "mlx-whisper"
    if venv_bin_underscore.exists():
        return CheckResult(name="mlx_whisper", ok=True, info=str(venv_bin_underscore))
    if venv_bin_dash.exists():
        return CheckResult(name="mlx_whisper", ok=True, info=str(venv_bin_dash))

    return CheckResult(name="mlx_whisper", ok=False, info="Not found in PATH or venv")


def run_doctor(runtime: RuntimeContext) -> List[CheckResult]:
    results: List[CheckResult] = []

    results.append(_check_executable("ffmpeg", "ffmpeg"))
    results.append(_check_executable("ffprobe", "ffprobe"))
    results.append(_check_mlx_whisper())
    results.append(_check_ollama(runtime.config.llm.base_url))
    results.append(_check_denoise_model())
    results.append(_check_source_access(runtime.config))

    return results
