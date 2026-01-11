from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import List

import requests

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


def _check_denoise_model(project_root: Path) -> CheckResult:
    model_path = project_root / "src" / "voxnote" / "assets" / "denoise" / "std.rnnn"
    if model_path.exists():
        return CheckResult(name="Denoise model", ok=True, info=str(model_path))
    return CheckResult(name="Denoise model", ok=False, info="std.rnnn is missing")


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
    results.append(_check_denoise_model(runtime.project_root))

    return results
