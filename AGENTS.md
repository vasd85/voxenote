# AGENTS.md

## Cursor Cloud specific instructions

### Project overview

VoxNote is a local CLI for audio notes: VAD trim → transcription (mlx-whisper) → LLM analysis (Ollama) → Markdown note + audio archiving. Designed for macOS Apple Silicon but the dev/test workflow runs on Linux.

### Quick reference

- **Package manager**: `uv` (installed at `~/.local/bin/uv`; ensure `$HOME/.local/bin` is on `PATH`)
- **Install deps**: `uv sync`
- **Run CLI**: `uv run voxnote <command>` (see `uv run voxnote --help`)
- **Lint**: `uv run ruff check .` and `uv run ruff format --check .`
- **Tests**: `uv run pytest` — all tests are fully mocked (no Ollama/ffmpeg/torch/mlx-whisper required)
- **Config init**: `uv run voxnote init` creates `config.yaml` from `config.example.yaml`

### Non-obvious caveats

- Tests **must not** require external services. All calls to Ollama, ffmpeg, torch, and mlx-whisper are mocked. See `CONTRIBUTING.md` and `.cursor/rules/04-testing.mdc`.
- `config.yaml` is gitignored. Tests should use `tmp_path` and never write to the real `config.yaml`.
- The `mlx-whisper` package installs on Linux but only runs on Apple Silicon (MLX framework). The CLI entry point and `doctor` command still work, but actual transcription will fail on non-macOS.
- Ollama is an external service (`localhost:11434`); it is not available in the Cloud VM. `voxnote doctor` will show Ollama as FAIL — this is expected.
- `ffmpeg` and `ffprobe` are pre-installed in the Cloud VM at `/usr/bin/`.
- The repo has existing ruff lint/format issues in the committed code; these are pre-existing and not regressions.
