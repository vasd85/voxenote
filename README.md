# Voxnote (MVP)

CLI tool for fully local processing of personal audio notes on macOS with Apple Silicon (M1/M2/M3).

- Prepares audio files for processing (mono 16kHz WAV with denoise/normalization) to improve transcription quality.
- Removes silence from audio files using Silero VAD (optional preprocessing step).
- Transcribes audio files to text using `mlx-whisper` (Apple Silicon optimized).
- Analyzes note content with a local LLM via Ollama (`qwen2.5:32b-instruct-q4_K_M`).
- Generates a meaningful title and high-level category.
- Creates a structured Markdown note file.
- Archives the original audio file and links it to the note via a UUID.

I built this because I had accumulated a large number of voice memos. Recording audio is frictionless, but audio is hard to search, reuse, or organize. This CLI turns audio notes into Markdown notes that you can browse, link, and refine over time — while keeping the entire pipeline on-device.

The repo also includes Cursor rules (`.cursor/rules`) describing the engineering approach: architecture boundaries, Python style, CLI UX, and testing practices.

No cloud LLMs are used: all processing happens on your machine.

---

## Requirements

- macOS with Apple Silicon (M1 / M1 Pro / M1 Max / etc.).
- Tested primarily on macOS (Apple Silicon) on an M1 Max with 32GB RAM. Other platforms may work, but are not the main target.
- Python **3.11+**.
- Homebrew (for installing Ollama and ffmpeg).
- `mlx-whisper` and `Ollama` with model `qwen2.5:32b-instruct-q4_K_M` installed.
- `ffmpeg` (for VAD trimming feature): `brew install ffmpeg`
- `torch`, `torchaudio`, and `torchcodec` (PyTorch) — installed automatically via `uv sync` for Silero VAD.

---

## Project layout

```text
voxnote/
├── pyproject.toml           # Project metadata and dependencies
├── config.example.yaml      # Config template (committed)
├── config.yaml              # User config (generated; gitignored)
├── src/
│   └── voxnote/
│       ├── __init__.py
│       ├── cli.py           # CLI commands (process, status, init)
│       ├── config.py        # Config loading & validation
│       ├── transcribe.py    # Transcription via mlx-whisper
│       ├── analyze.py       # Text analysis via Ollama (Qwen)
│       ├── organize.py      # Markdown creation & audio archiving
│       ├── vad_trim.py      # Silence removal via Silero VAD
│       ├── models.py        # Pydantic models (config, results, contexts)
│       ├── audio_metadata.py # Audio metadata extraction (recorded_at, etc.)
│       ├── audio_prepare.py  # Audio preparation (mono 16kHz WAV + denoise)
│       ├── cache_paths.py    # Cache path management (prepared, trimmed)
│       ├── collect_plan.py   # Collect command source planning
│       ├── doctor.py         # Environment diagnostics
│       ├── runtime.py        # Runtime context (config, paths, state dir)
│       ├── state.py          # State management (processed_audio.jsonl, metadata)
│       ├── workflow.py       # Main processing workflow orchestration
│       └── assets/
│           └── denoise/      # Denoise model (std.rnnn)
├── tests/                   # Test suite (pytest)
├── input/                   # Incoming audio files
├── output/                  # Structured Markdown notes
├── archive/                 # Archived processed audio files
└── .voxnote/            # Local state & caches (gitignored)
```

---

## Installation

### 1. Clone the project

```bash
git clone <repo-url> voxnote
cd voxnote
```

(or just place the project files in a directory and `cd` into it.)

### 2. Install `uv` (recommended)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Check:

```bash
uv --version
```

### 3. Install Python dependencies

From the project root (`voxnote/`):

```bash
uv sync
```

This will create a virtual environment and install dependencies from `pyproject.toml`.

If you need to activate the environment manually:

```bash
source .venv/bin/activate
```

(Path may vary depending on your `uv`/IDE settings.)

---

## Models setup

### 1. Install Ollama and Qwen 2.5 32B

**Ollama:**

```bash
brew install ollama
```

Start Ollama (it usually runs as a background service, but you can be explicit):

```bash
ollama serve
```

**Model `qwen2.5:32b-instruct-q4_K_M`:**

```bash
ollama pull qwen2.5:32b-instruct-q4_K_M
```

Check that the model is available:

```bash
ollama list
```

### 2. Install ffmpeg

For VAD trimming feature:

```bash
brew install ffmpeg
```

### 3. Whisper models

`mlx-whisper` is installed automatically when you run `uv sync` (it's in project dependencies).

Models are downloaded automatically on first use via Hugging Face Hub. The default config expects:

```yaml
transcription:
  model: mlx-community/whisper-large-v3-turbo
```

You can change this later in `config.yaml`.

### 4. Silero VAD model

The Silero VAD model is downloaded automatically on first use via `torch.hub` and cached in `.voxnote/torch_cache/`. No manual setup required.

---

## Processing pipeline

The typical processing flow consists of several optional steps:

1. **Collect** audio files from source directories into `input/`
2. **Prepare** audio for VAD (recommended): converts to mono 16kHz WAV with denoise/normalization
3. **VAD trim** (optional): removes silence segments to speed up transcription
4. **Process**: transcribes, analyzes, and organizes notes

Steps 2-3 create cached intermediate files (`.voxnote/prepared/` and `.voxnote/trimmed/`) that are reused on subsequent runs. The `process` command automatically uses the best available cache (trimmed > prepared > original).

---

## Configuration (`config.yaml`)

This repository commits `config.example.yaml` only. Your local `config.yaml` is intentionally not committed because it may contain personal paths.

You can generate `config.yaml` from the template via:

```bash
uv run voxnote init
```

Template excerpt (`config.example.yaml`):

```yaml
paths:
  input: ./input
  output: ./output
  archive: ./archive

transcription:
  model: mlx-community/whisper-large-v3-turbo
  language: auto

llm:
  model: qwen2.5:32b-instruct-q4_K_M
  base_url: http://localhost:11434

processing:
  supported_formats: [m4a, mp3, wav, ogg, flac]

vad:
  threshold: 0.28
  neg_threshold: 0.18
  min_silence_duration_ms: 1200
  min_speech_duration_ms: 200
  speech_pad_ms: 300
```

See `config.example.yaml` for the full template (single source of truth).

- `config.yaml` lives in the project root: `voxnote/config.yaml`.
- `config.py` resolves it relative to the project root, so you normally do not need to pass any paths manually.
- If you keep multiple configs, pass `--config path/to/config.yaml` to any command.

---

## Privacy & local state

- Everything is designed to run locally: transcription (`mlx-whisper`) and LLM analysis (Ollama) both run on your machine.
- Runtime state and caches are stored under `.voxnote/` (JSONL indexes + caches like `prepared/` and `trimmed/`).
- `.voxnote/` is gitignored. For extra safety, `.cursorignore` excludes `.voxnote/*.jsonl` to avoid IDE indexing of transcripts.
- The CLI avoids printing full transcription / note text to the terminal by default (treat it as sensitive data).

---

## Output format

Each processed note becomes a Markdown file similar to this (synthetic example):

```markdown
# Planning my next side project

- **ID:** abc123
- **Audio:** archive/abc123_voice_note.m4a
- **Source:** voice_note.m4a
- **Recorded at:** 2024-11-26 12:34:56
- **Category:** Ideas
- **Whisper model:** mlx-community/whisper-large-v3-turbo
- **Transcription language:** auto

---

Short summary.

---

Full transcription text...
```

Key points:

- `ID` is a UUID used to link audio and text.
- `Audio` is a **relative** path from the project root (`archive/...`).
- Archived audio filename: `<uuid>_<original_name>`.
- Notes are created under `output/<category-slug>/` with filename `<YYYY-MM-DD_HH-MM-SS>_<title-slug>.md`.

Category and title are generated by the LLM.

---

## CLI usage

The project installs a console script named `voxnote` (see `[project.scripts]` in `pyproject.toml`).

From the project root, using `uv`:

```bash
uv run voxnote --help
```

Commands are described below in the order they are typically used in a workflow.

### 1. Initialize config

If you want to (re)create `config.yaml` with default values:

```bash
uv run voxnote init
```

If the file already exists and you want to overwrite it:

```bash
uv run voxnote init --force
```

This command will:

- Write default `config.yaml` at the expected path.
- Use `config.example.yaml` as the template (single source of truth).
- Ensure `input/`, `output/`, `archive/` directories exist.

### 2. Collect audio files from sources

The `collect` command copies supported audio files from one or more source directories into `input/`.
It skips files whose **SHA256 hash** is already present in `.voxnote/processed_audio.jsonl` (same file content = same hash, regardless of filename).
When copying into `input/`, it prefixes the filename with the file hash (`<sha256>_<original_name>`) to avoid name collisions.

Using explicit source dirs:

```bash
uv run voxnote collect --source "/path/to/Voice Memos" --source "/path/to/AnotherFolder"
```

Recursion control:

```bash
uv run voxnote collect --source "/path/to/Voice Memos" --recursive-mode off
```

Rules:
- `--recursive-mode auto` (default): if the source exists in `config.yaml`, uses its `recursive` flag; otherwise uses `collect.recursive_default`.
- `--recursive-mode on|off`: overrides recursion for all sources in the current run.

Using sources from `config.yaml`:

```yaml
sources:
  - path: ~/VoiceMemos
    recursive: true
```

```bash
uv run voxnote collect
```

### 3. Prepare audio for VAD (recommended)

The `prepare-vad` command creates prepared WAV files (mono 16kHz + denoise/normalization) for faster and more reliable VAD and transcription:

```bash
uv run voxnote prepare-vad
```

To process a single file (path relative to `input/`):

```bash
uv run voxnote prepare-vad --file "note.m4a"
```

**Rebuild prepared cache** (ignore existing cached prepared files):

```bash
uv run voxnote prepare-vad --force
```

Prepared WAV cache is stored in `.voxnote/prepared/`. This step improves transcription quality by normalizing audio format and reducing noise.

### 4. Remove silence from audio files (VAD trim, optional)

The `vad-trim` command uses Silero VAD to detect speech segments and prepares trimmed copies of audio files (originals stay untouched):

```bash
uv run voxnote vad-trim
```

This processes all audio files in `input/` directory. To process a single file:

```bash
uv run voxnote vad-trim --file "note.m4a"
```

**Dry run mode** (detect segments without modifying files or cache):

```bash
uv run voxnote vad-trim --dry-run
```

**Rebuild trimmed cache** (ignore existing cached trimmed copies):

```bash
uv run voxnote vad-trim --force
```

**Override VAD parameters** via command-line flags:

```bash
uv run voxnote vad-trim --threshold 0.6 --min-silence-duration-ms 800 --speech-pad-ms 300
```

VAD parameters (from `config.yaml` or flags):
- `threshold` (0.0-1.0): Speech detection sensitivity. Higher = more strict.
- `neg_threshold` (0.0-1.0): Non-speech sensitivity. Higher = more strict.
- `min-silence-duration-ms`: Minimum silence duration to split segments.
- `min-speech-duration-ms`: Minimum speech duration to keep a segment.
- `speech-pad-ms`: Padding around speech segments to preserve word boundaries.
 
**Note:** Trimmed copies are stored in `.voxnote/trimmed/`, originals in `input/` are not modified. If a prepared cache exists, `vad-trim` uses it; otherwise it uses the original file.
If VAD detects no speech, no trimmed copy is created. This step is optional but recommended for long recordings with significant silence, as it speeds up transcription and improves accuracy.

### 5. Process audio files

Process all files in `input/`:

```bash
uv run voxnote process
```

Process a single file:

```bash
uv run voxnote process --file "note.m4a"
```

- `--file` is a **path relative to `input/`** (you can also use subdirectories, e.g. `subdir/note.m4a`).
- To print per-file metadata, pass `--show-metadata`.
- To reprocess files that were already processed, use `--force`.

What happens for each file:

1. The best available audio source is selected: trimmed cache (if exists) > prepared cache (if exists) > original file.
2. `mlx-whisper` transcribes audio to text.
3. The text is sent to `qwen2.5:32b-instruct-q4_K_M` via Ollama.
4. The model returns a JSON payload with `title`, `category`, and `short_summary`.
5. A Markdown note is created at `output/<category-slug>/<YYYY-MM-DD_HH-MM-SS>_<title-slug>.md`.
6. The original audio file is archived as `archive/<uuid>_<original_name>` (moved from `input/` after success).
7. The file's SHA256 hash and metadata are appended to `.voxnote/processed_audio.jsonl` so it won't be imported again.

If a file was already processed (same content hash), it is skipped unless `--force` is used.

If you keep multiple configs, add `--config path/to/config.yaml` to any command.

### 6. Check status

```bash
uv run voxnote status
```

The command prints:

- `Pending audio files` — how many supported audio files are currently present in `input/`.
- `Notes created` — how many `*.md` files exist under `output/`.

### 7. Diagnose environment

```bash
uv run voxnote doctor
```

Checks presence of `ffmpeg`, `ffprobe`, `mlx-whisper`, reachability of Ollama, and that the shipped denoise model `std.rnnn` is present.

---

## Typical workflow

1. Start Ollama:

   ```bash
   ollama serve
   ```

2. Install Python dependencies (once):

   ```bash
   cd voxnote
   uv sync
   ```

3. Optionally initialize config:

   ```bash
   uv run voxnote init
   ```

4. Use `collect` to copy audio notes into `input/`:

   ```bash
   uv run voxnote collect
   ```

5. (Recommended) Prepare audio for VAD:

   ```bash
   uv run voxnote prepare-vad
   ```

6. (Optional) Remove silence from audio files:

   ```bash
   uv run voxnote vad-trim
   ```

   This step is optional but recommended for long recordings with significant silence, as it speeds up transcription and improves accuracy.

7. Process them:

   ```bash
   uv run voxnote process
   ```

8. Read results:

   - Markdown notes under `output/` (organized by category).
   - Original audio in `archive/`, linked via the `ID` field and filename.

---

## Debugging and manual testing

- **Check `mlx-whisper` works:**

  ```bash
  mlx-whisper transcribe path/to/audio.m4a
  ```

- **Check LLM analysis:**

  You can call the functions in `analyze.py` from a Python REPL or add a temporary wrapper. The module already exposes `analyze_text(config, text)` returning `NoteAnalysis`.

- **Check note organization without real audio:**

  `organize.py` contains `cli_organize_dummy` helper which you can call manually to see how a note is created and how audio is archived.

---

## Known issues

See `ROADMAP.md` in the project root for known limitations and planned improvements.

---

## Contributing

Contributions are welcome. See `CONTRIBUTING.md`.

## Changelog

See `CHANGELOG.md`.
