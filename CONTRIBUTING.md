# Contributing

Thanks for your interest in contributing!

This project is intentionally **local-only** (no cloud LLMs) and treats transcripts/notes as **sensitive data**. Please keep that in mind when proposing changes.

## Development setup

From the repository root:

```bash
uv sync
uv run voxnote --help
```

## Running checks

```bash
uv run ruff format .
uv run ruff check .
uv run pytest
```

## Project principles

- **Architecture boundaries**: keep `cli.py` thin; put logic in modules; keep side effects at boundaries so they can be mocked in tests. See `.cursor/rules/01-architecture.mdc`.
- **Privacy-first**: avoid logging/printing full transcription or note text by default. Never commit generated data (`input/`, `output/`, `archive/`, `.voxnote/`) or personal `config.yaml`.
- **Local-only**: do not add cloud LLM integrations. Ollama is the default analysis backend.
- **Docs**: documentation is English; code comments are English and only for non-obvious things.

## Tests

- Tests must not require Ollama/ffmpeg/torch/mlx-whisper — mock external calls.
- Use `tmp_path` for `input/`, `output/`, `archive/`, `.voxnote/`.
- Do not write to the real `config.yaml` in the repo root.
- See `TESTS_NEEDED.md` for the coverage checklist.

## Pull request checklist

- [ ] Tests added/updated when behavior changes
- [ ] `uv run ruff check .` passes
- [ ] `uv run pytest` passes
- [ ] No sensitive data committed (`input/`, `output/`, `archive/`, `.voxnote/`, `config.yaml`)

