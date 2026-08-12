# Voxnote (MVP)

CLI tool for fully local processing of personal audio notes on macOS with Apple Silicon (M1/M2/M3).

- Prepares audio files for processing (mono 16kHz WAV with denoise/normalization) to improve transcription quality.
- Removes silence from audio files using Silero VAD (optional preprocessing step).
- Transcribes audio files to text using `mlx-whisper` (Apple Silicon optimized).
- Optionally labels who says what in multi-speaker recordings (`Speaker 1:` / `Speaker 2:` blocks) using `sherpa-onnx`.
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
- `sherpa-onnx` — installed automatically via `uv sync`; CPU-only ONNX runtime used for optional speaker diarization.

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
│       ├── diarize.py       # Speaker diarization via sherpa-onnx (models + engine)
│       ├── speaker_merge.py # Word -> speaker assignment, `Speaker N:` blocks
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
git clone https://github.com/vasd85/voxenote.git voxnote
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

### 4. (Optional) Install globally to run from any directory

`uv sync` is enough for development from the project root. To run `voxnote` from **any** directory, install it as a uv tool:

```bash
uv tool install .
```

This puts a `voxnote` command on your `PATH` (usually `~/.local/bin/voxnote`). After that, use `voxnote <command>` anywhere instead of `uv run voxnote <command>`. Re-run `uv tool install . --force` to update after pulling changes.

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

### 5. Speaker diarization models (optional)

Only needed if you turn on `diarization.enabled`. Two models (~35 MB total) are downloaded once from the
[k2-fsa/sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) GitHub releases — no Hugging Face account or token — and cached in
`diarization/` inside the state dir (`.voxnote/diarization/` for a project-local config, otherwise
`~/Library/Application Support/voxnote/diarization/` — see "Where data lives" under Configuration):

- segmentation: `sherpa-onnx-pyannote-segmentation-3-0/model.onnx` (MIT),
- speaker embedding: `3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx`.

After that first download everything runs offline. If the download fails, the error names the file and where to put it;
you can also fetch them by hand from the
[segmentation](https://github.com/k2-fsa/sherpa-onnx/releases/tag/speaker-segmentation-models) and
[embedding](https://github.com/k2-fsa/sherpa-onnx/releases/tag/speaker-recongition-models) release pages and drop them
into that directory (`uv run voxnote doctor` prints the exact paths it expects, and reports whether both files are
present and readable).

---

## Processing pipeline

The typical processing flow consists of several optional steps:

1. **Collect** audio files from source directories into `input/`
2. **Prepare** audio for VAD (recommended): converts to mono 16kHz WAV with denoise/normalization
3. **VAD trim** (optional): removes silence segments to speed up transcription
4. **Process**: transcribes, optionally labels speakers, analyzes, and organizes notes

Steps 2-3 create cached intermediate files (`.voxnote/prepared/` and `.voxnote/trimmed/`) that are reused on subsequent runs. The `process` command automatically uses the best available cache (trimmed > prepared > original).

Instead of running each step by hand, you can run the whole flow in one command with `voxnote run`. It reads the `pipeline:` section of `config.yaml` to decide which steps to execute (see [Configuration](#configuration-configyaml)). The individual commands stay available for when you want to run a single step.

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
  input: ~/Documents/voxnote/input
  output: ~/Documents/voxnote/output
  archive: ~/Documents/voxnote/archive

pipeline:
  # Steps that `voxnote run` executes, in order. Set any to false to skip it.
  collect: true       # copy audio from `sources` into input/
  prepare_vad: true   # build prepared WAV cache (mono 16kHz + denoise)
  vad_trim: true      # remove silence via Silero VAD (optional)
  process: true       # transcribe, analyze, and write notes

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

diarization:
  enabled: false           # off by default
  backend: sherpa_onnx
  num_speakers: 0          # exact speaker count when you know it; 0 = detect automatically
  cluster_threshold: 0.5   # used only when num_speakers is 0
  min_duration_on: 0.3
  min_duration_off: 0.5
  num_threads: 2
  segmentation_model: ""
  embedding_model: ""
  download_timeout_s: 600
```

See `config.example.yaml` for the full template (single source of truth).

**Where config lives (discovery order).** With no `--config` flag, voxnote looks for `config.yaml` in this order:

1. `$VOXNOTE_CONFIG`, if set
2. `~/.config/voxnote/config.yaml` (or `$XDG_CONFIG_HOME/voxnote/config.yaml`) — the global-install default; `voxnote init` creates it here
3. `./config.yaml` in the current directory
4. the repo root `voxnote/config.yaml` (development fallback)

Relative `paths:` are resolved against the config file's own directory; absolute and `~` paths are used as-is. To pin a specific config, pass `--config /path/to/config.yaml` (or set `VOXNOTE_CONFIG`) on any command.

**Where data lives.** With the global user config, pipeline state and caches live in `~/Library/Application Support/voxnote/`, and notes/audio default to `~/Documents/voxnote/{input,output,archive}` (edit `paths:` to change). When run from a project that already has a `.voxnote/` directory beside its config, that local state is kept (backward compatible).

---

## Full Disk Access (macOS Voice Memos)

Voice Memos recordings live in a macOS-protected folder (`~/Library/Group Containers/group.com.apple.VoiceMemos.shared/Recordings` on recent macOS). Reading it requires **Full Disk Access (FDA)**.

macOS grants FDA to the *terminal* that launches a command-line tool, not to the tool itself (the terminal is the "responsible process"). To avoid giving your everyday terminal full disk access, use a **dedicated terminal just for collecting**:

1. Install a second terminal app (or create a separate Terminal/iTerm profile) used only for voxnote.
2. System Settings → Privacy & Security → Full Disk Access → add that terminal → enable it → fully quit and reopen it.
3. Run only `voxnote collect` from that terminal. Everything else (`prepare-vad`, `vad-trim`, `process`) reads `input/` and needs no special access, so run it from your normal terminal.

Add the Voice Memos folder to `sources:` in your config, then verify access with `voxnote doctor` — it probes each source and reports `permission denied` when FDA is missing.

Only `collect` needs FDA. Granting voxnote *itself* its own FDA (rather than a terminal) would require code-signing plus a LaunchAgent/app wrapper — intentionally out of scope for now.

---

## Speaker diarization (optional)

For recordings with more than one voice, voxnote can split the transcript into speaker blocks instead of one flat text
blob. It is **off by default**. Turn it on in `config.yaml`:

```yaml
diarization:
  enabled: true
```

or per run, in either direction:

```bash
uv run voxnote process --diarize
```

```bash
uv run voxnote process --no-diarize
```

How it works: `process` transcribes with word-level timestamps, runs offline diarization (`sherpa-onnx`) on the **same**
audio file it transcribed (trimmed > prepared > original, so both share one timeline), assigns every word to the speaker
turn it overlaps most, and merges consecutive words of one speaker into `Speaker N:` blocks. Labels are numbered in order
of first appearance and are local to a single note — `Speaker 1` in two notes is not the same person.

The labeled transcript goes into both the note body and the LLM analysis, so titles and summaries can reflect the
dialogue. Notes also gain a `- **Speakers:** N` header field.

### Tuning

| Setting | What it does |
|---|---|
| `num_speakers` | Exact number of speakers when you know it. **The most effective knob** — set it to 2 for an interview. `0` = detect automatically. |
| `cluster_threshold` | Only used when `num_speakers: 0`. Distance threshold for grouping voices: **lower = more speakers**, higher = fewer. Start at `0.5` and lower it if voices get merged. |
| `min_duration_on` | Speech turns shorter than this (seconds) are dropped. |
| `min_duration_off` | Silence shorter than this (seconds) does not split a turn. |
| `num_threads` | ONNX threads. Diarization is CPU-only and runs far faster than real time. |
| `segmentation_model` / `embedding_model` | Leave empty for the defaults. A bare file name for `embedding_model` (e.g. `nemo_en_titanet_small.onnx`) is downloaded from the k2-fsa embedding release; a path (absolute, or relative to the `diarization/` models dir) is used as-is and never downloaded. |

`prompts.speaker_labels_hint` in `config.yaml` is the extra instruction appended to the system prompt for labeled
transcripts only — edit it if you want the LLM to treat the dialogue differently. It repeats the prompt-injection guard,
so keep that part when you change it.

### Limitations

- **Similar voices may be merged into one speaker.** This is the most common failure; set `num_speakers` when you know
  the count, or lower `cluster_threshold`.
- Whisper word timestamps are a heuristic, so a word or two may land on the wrong side of a speaker change.
- `vad-trim` removes the pauses that segmentation partly relies on, so turn boundaries near splice points can blur.
  Word-level assignment limits the damage, but for tricky recordings try `pipeline.vad_trim: false`.
- Overlapping speech is not modeled: each word gets exactly one speaker.
- Speakers are not tracked across recordings — no voice profiles, no names.
- If diarization finds a single speaker, the note is written exactly as it would be with diarization off.
- Diarization makes whisper emit word timestamps, which slightly changes how it advances through the audio. The wording
  of a diarized transcript can therefore differ here and there from a non-diarized run of the same file.

Changing any of these settings makes `process` re-run files whose notes were produced with the old settings; nothing is
re-processed when the settings are unchanged.

---

## Privacy & local state

- Everything is designed to run locally: transcription (`mlx-whisper`) and LLM analysis (Ollama) both run on your machine.
- Runtime state and caches are stored under `.voxnote/` (JSONL indexes + caches like `prepared/` and `trimmed/`).
- `.voxnote/` is gitignored. For extra safety, `.cursorignore` excludes `.voxnote/*.jsonl` to avoid IDE indexing of transcripts.
- The CLI avoids printing full transcription / note text to the terminal by default (treat it as sensitive data). Speaker diarization reports only counts (speakers, turns), never text.
- Diarization models are the only thing fetched from the network, once, from GitHub releases. Nothing is uploaded, ever.
- With `llm.debug: true`, diarization also appends turn timings (start/end/speaker — no text) to `.voxnote/diarization_debug.jsonl`.

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

With diarization enabled and more than one speaker detected, the header gains a `Speakers` field and the transcript is
split into blocks:

```markdown
- **Transcription language:** auto
- **Speakers:** 2

---

Short summary.

---

Speaker 1: So what did you think of the proposal?

Speaker 2: It looks solid, but the timeline is tight.
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

### Quick start: run the whole pipeline

If you don't want to run each step by hand, run the entire flow with a single command:

```bash
uv run voxnote run
```

`run` executes the steps enabled under `pipeline:` in `config.yaml`, in order:
`collect → prepare-vad → vad-trim → process`. Disabled steps are skipped, and steps
never abort one another — `process` always falls back to the best available source
(trimmed > prepared > original), so a skipped or failed preprocessing step still
produces notes. The run is idempotent: cached and already-processed files are reused.

```yaml
pipeline:
  collect: true       # set false if you drop files into input/ yourself
  prepare_vad: true
  vad_trim: true       # set false to skip silence removal
  process: true
```

To rebuild caches and reprocess everything from scratch:

```bash
uv run voxnote run --force
```

`run` also accepts `--diarize` / `--no-diarize` to override `diarization.enabled` for that run.

The individual step commands below remain available for running a single step.

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

- Write a default `config.yaml` at `~/.config/voxnote/config.yaml` (or at `--config`/`$VOXNOTE_CONFIG` if set).
- Use the packaged `config.example.yaml` as the template (single source of truth), so it works even outside the repo.
- Ensure the `input/`, `output/`, `archive/` directories from `paths:` exist.

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
- To force speaker diarization on or off for this run, pass `--diarize` or `--no-diarize` (overrides
  `diarization.enabled`; see [Speaker diarization](#speaker-diarization-optional)).

What happens for each file:

1. The best available audio source is selected: trimmed cache (if exists) > prepared cache (if exists) > original file.
2. `mlx-whisper` transcribes audio to text.
3. If diarization is enabled, the same audio file is diarized and the transcript is split into `Speaker N:` blocks.
4. The text is sent to `qwen2.5:32b-instruct-q4_K_M` via Ollama.
5. The model returns a JSON payload with `title`, `category`, and `short_summary`.
6. A Markdown note is created at `output/<category-slug>/<YYYY-MM-DD_HH-MM-SS>_<title-slug>.md`.
7. The original audio file is archived as `archive/<uuid>_<original_name>` (moved from `input/` after success).
8. The file's SHA256 hash and metadata are appended to `.voxnote/processed_audio.jsonl` so it won't be imported again.

If a file was already processed (same content hash), it is skipped unless `--force` is used, or unless the diarization
settings changed since its note was written.

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

When `diarization.enabled` is true it also checks that `sherpa-onnx` is importable and that both diarization models are
present and readable; when it is false the table just reports diarization as disabled.

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

   With config in place, steps 4-7 can be replaced by a single `uv run voxnote run`
   (it runs the steps enabled under `pipeline:`). The granular steps below are for
   running one step at a time.

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
