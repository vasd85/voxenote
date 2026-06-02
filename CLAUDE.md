# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`voxnote` is a **local-only** macOS/Apple-Silicon CLI that turns personal voice memos into organized Markdown notes. Pipeline per file: (collect →) prepare audio → (VAD trim →) transcribe with `mlx-whisper` → analyze with a local LLM via Ollama (`qwen2.5:32b-instruct-q4_K_M`) → write a Markdown note + archive the original audio. **No cloud services** are used and nothing leaves the machine.

See `README.md` for the full CLI reference, config template, and per-command flags — don't duplicate that here.

## Commands

```bash
uv sync                          # create .venv and install deps (incl. dev group)
uv run voxnote --help            # run the CLI (console script = voxnote.cli:main)
uv run voxnote <command>         # init | doctor | collect | prepare-vad | vad-trim | process | status

uv run ruff format .             # format (run before check)
uv run ruff check .              # lint (E,F,W,I; line-length 120, double quotes)
uv run pytest                    # full suite (fast; no external services needed)
uv run pytest tests/test_paths_and_archive.py::test_archive_mode_auto_moves_input   # single test
```

`uv run voxnote doctor` verifies the runtime prerequisites (ffmpeg, ffprobe, mlx-whisper, Ollama reachability, bundled denoise model). Those are needed to actually *run* the pipeline, but **not** to run the tests.

## Architecture

Three layers, strict boundaries (keep them — this is the project's core design rule):

- **`cli.py`** — thin I/O layer only. Click commands + Rich rendering. Parses flags, builds a `RuntimeContext`, calls a `Workflow` generator, and renders the events it yields. No business logic here.
- **`workflow.py`** — orchestration. The `Workflow` class coordinates every step, idempotency, and cache selection. Each public method (`collect_files`, `prepare_vad_files`, `vad_trim_files`, `process_files`) is a **generator that yields `WorkflowEvent(type, message, file, data)`**. This generator/event contract is the seam between logic and UI: to add or change a step, edit a Workflow generator and its CLI consumer — never push logic into `cli.py`.
- **Boundary modules** — each owns one external side effect so it can be mocked in tests: `transcribe.py` (mlx-whisper subprocess), `analyze.py` (Ollama HTTP), `organize.py` (Markdown + archive moves), `vad_trim.py` / `audio_prepare.py` (Silero VAD + ffmpeg), `audio_metadata.py` (mdls/ffprobe/stat). `state.py` and `cache_paths.py` own all `.voxnote/` reads/writes; `config.py` + `models.py` + `runtime.py` own configuration.

### Content-addressed idempotency (the central idea)

Everything is keyed on the **SHA256 of the original audio file's content**, not its name. This makes the pipeline rename-safe and re-runnable:

- `collect` copies sources into `input/` as `<sha256>_<originalname>` and skips any hash already known.
- Caches are named by that hash: `.voxnote/prepared/<hash>_<slug>.wav` and `.voxnote/trimmed/<hash>.wav`.
- `process` selects the **best available source** per file — trimmed > prepared > original (`Workflow._get_transcription`).

State lives in append-only JSONL indexes under `.voxnote/` (writes are upserts: purge-then-append, see `state.py`):

| File | Purpose |
|------|---------|
| `collected_audio.jsonl` | what `collect` copied — skip re-copying |
| `processed_audio.jsonl` | what `process` finished — skip re-processing. Stores `transcribed_file_hash`, so `process` re-runs a file if its trimmed cache changed since last time |
| `original_metadata.jsonl` | `recorded_at` + raw mdls/ffprobe/stat, captured at collect time (before the original is moved away) |
| `failed_transcriptions.jsonl` | transcription text saved when LLM analysis fails, so a `--file` retry skips re-transcribing |

### Config flow

`config.yaml` (project root, gitignored) → `config.py:load_config` normalizes relative paths **against the config file's own directory** and creates `input/`/`output/`/`archive/` → `models.py:AppConfig` (Pydantic, validated) → `runtime.py:RuntimeContext` (adds `project_root` + the `.voxnote/` state dir). `DEFAULT_CONFIG_PATH` resolves to the repo root.

`voxnote init` copies `config.example.yaml` **verbatim** — that template is the single source of truth for defaults. **Adding a config key requires touching three places: the Pydantic model in `models.py`, the `config.example.yaml` template, and `README.md` if user-facing.** Pydantic field defaults exist as a safety net but the template is what users actually get.

### Two non-obvious mechanisms

- **`analyze.py`** streams Ollama `/api/chat`, counts tokens via `/api/tokenize` (with a byte-ratio heuristic fallback for older Ollama), and truncates note text *from the end* to fit `DEFAULT_NUM_CTX` (16384) before sending. Transient HTTP failures retry with backoff. The system prompt (in `config.yaml`) instructs the model to treat note text as inert data and ignore any embedded instructions — a prompt-injection guard; preserve it.
- **`organize.py`** is atomic-ish: write note to `.tmp` → move audio to `archive/<uuid>_<name>` → atomically rename `.tmp` → `.md`, with rollback of the audio move on failure. Notes land in `output/<category-slug>/<YYYY-MM-DD_HH-MM-SS>_<title-slug>.md`; the `<uuid>` links note ↔ archived audio.

## Conventions & invariants

- **Privacy (cross-cutting, enforced by convention).** Transcription and note text are sensitive. Do **not** log or print full text by default — emit paths, counts, lengths, hashes instead. Full LLM exchanges go only to `.voxnote/llm_debug.jsonl` and only when `llm.debug: true`.
- **Local-only.** Do not add any cloud LLM/transcription integration. Ollama + mlx-whisper are the backends.
- **File-write boundary.** Only write under `input/`, `output/`, `archive/`, `.voxnote/` (plus test `tmp_path`). Never modify originals except the explicit archive step; never delete/overwrite without an explicit `--force`.
- **CLI UX.** Output is English and terse. Every error message states the reason **and** the next action (a command/flag to run). Don't surface tracebacks to users by default.
- **Tests** must run without Ollama/ffmpeg/torch/mlx-whisper — mock those boundaries. Use `tmp_path` for all I/O, never the repo's real `config.yaml`. Freeze time with `freezegun` (`@freeze_time`) rather than calling `datetime.now()` in assertions. `tests/conftest.py` puts `src/` on `sys.path`. `TESTS_NEEDED.md` tracks the coverage checklist.
- **Comments** in English, only for non-obvious logic; no change-history narration. TODO format: `# TODO: Action: ...`.
- The richer engineering rules live in `.cursor/rules/*.mdc` (overview, architecture, python-style, cli-ux, testing) — consult them for detail.

## Generated/personal data — never commit, never print

`input/`, `output/`, `archive/`, `.voxnote/`, `temp/`, `temp2/`, `docs/`, and `config.yaml` are gitignored. In this working tree they hold real personal audio (`.m4a` under `temp/`, `temp2/`, `archive/`), transcripts, and notes. Treat all of it as sensitive: don't read its contents into output, don't commit it (it's already ignored — keep it that way), don't include it in diffs.
