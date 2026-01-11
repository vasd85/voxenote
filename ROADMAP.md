# Roadmap & Known Limitations

This repository is a usable CLI tool for local audio notes processing. This document combines:

- **Known limitations** (user-facing): what can go wrong in the current MVP and what to do about it.
- **Roadmap** (dev-facing): what I plan to improve next, in priority order.

The project is designed for **fully local processing** (no cloud LLMs).

---

## Terminology

- **original**: the user's original audio note (`.m4a/.mp3/...`) stored in `input/` until archived.
- **prepared**: derived WAV (mono 16kHz + denoise/normalization). Cache: `.voxnote/prepared/`.
- **trimmed**: derived audio with non-speech removed (VAD trimming). Cache: `.voxnote/trimmed/`.

---

## Known limitations (MVP)

### Cache invalidation (prepared/trimmed)

**Status:** Known limitation

**Issue:** Cache keys for `prepared/` and `trimmed/` caches are based only on `original_hash`. They do not include a signature of processing parameters (VAD thresholds, denoise pipeline version, etc.).

**Impact:** If you change VAD parameters (`threshold`, `min_silence_duration_ms`, `speech_pad_ms`, etc.) or update the denoise pipeline, old caches may be reused silently, leading to incorrect results.

**Workaround:**

- Rebuild caches explicitly after changing processing params:
  - `voxnote prepare-vad --force`
  - `voxnote vad-trim --force`
- If you also want to regenerate notes for already processed audio, use `voxnote process --force`.

**Planned fix:** Include a signature of relevant parameters in cache keys.

---

### Trimmed without prepared (quality pitfall)

**Status:** Known limitation

**Issue:** If `vad-trim` runs without an existing `prepared` cache, it generates `trimmed` directly from `original`. Later creating `prepared` will not automatically replace that `trimmed`, and `process` may keep using the lower-quality `trimmed` (from original instead of prepared).

**Impact:** Lower quality transcription due to missing denoise/normalization step.

**Workaround:** Always run `prepare-vad` before `vad-trim`, or use `vad-trim --force` after preparing.

**Planned fix:** Store `trimmed_from=original|prepared` metadata and prefer `trimmed_from_prepared`, or make `vad-trim` auto-create `prepared` first.

---

### Files with no speech detected

**Status:** Known limitation

**Issue:** When `vad-trim` does not detect any speech segments in a file, it skips creating a trimmed cache and marks the file as `skipped_no_speech`. However, this information is not persisted in state. When `process` runs later, it will still attempt to transcribe the file using the `prepared` cache or `original` file, even though it contains no speech.

**Impact:** Files without speech will be transcribed anyway, resulting in empty or meaningless transcriptions, wasting processing time and resources, and potentially creating notes with empty content.

**Current behavior:** Original and prepared files remain in their directories. The `process` command will process them using the fallback chain: trimmed → prepared → original.

**Planned fix:** Store information about files with no speech detected (e.g., in `.voxnote/no_speech.jsonl`) and allow `process` to skip such files with a warning, or add a `--skip-no-speech` option.

---

### Performance notes (SHA256 on large files)

**Status:** Known limitation

**Issue:** Computing SHA256 on large audio files can be expensive. Repeated hashing may be slow on big batches.

**Current behavior:** Hashes are computed once per file and cached in state indexes. However, if the same file is processed multiple times in different contexts, it may be hashed again.

**Planned improvement:** Minimize redundant hashing by reusing hashes already computed in the current run or maintaining a hash→path mapping.

---

### Platform assumptions

**Status:** Platform-specific behavior

**Current behavior:**

- Designed primarily for macOS (Apple Silicon).
- `mdls` (macOS metadata extraction) is macOS-only; falls back gracefully on other platforms.
- First-time Silero VAD load via `torch.hub` may require network access.

**Limitation:** Some metadata features (like `mdls`) are not available on Linux/Windows, but the system degrades gracefully.

---

### Supply chain risk (Silero VAD)

**Status:** Known limitation

**Issue:** Silero VAD model is loaded via `torch.hub.load(..., trust_repo=True)` without explicit integrity verification.

**Impact:** Medium supply chain risk if the upstream repository is compromised.

**Current mitigation:** Model is cached locally after first download. Network access is only needed on first run.

**Planned improvement:** Add optional hash verification for the model.

---

### Test coverage gaps

**Status:** Known limitation

**Missing tests:**

- Regression: "skip already processed" by SHA256
- Regression: "reuse failed transcriptions"
- Performance tests for large files and batch operations

**Planned:** See `TESTS_NEEDED.md` for the full checklist.

---

## Roadmap

This is an MVP that will evolve into a **local knowledge management system** for audio notes: from a batch processing pipeline to a complete solution for transforming, storing, searching, and organizing personal audio content — all with full privacy control.

### Migration to SQLite

Replace `.jsonl` files with SQLite as the primary state storage to enable efficient querying, full-text search, and better data integrity.

### Extended functionality: Local knowledge system

Transform Voxnote from a batch CLI tool into a **local knowledge management platform** that combines audio processing, structured storage, and intelligent search.

### Global application installation

Enable system-wide installation of Voxnote as a standard application, not just a project-local CLI.

### GUI application

Provide a graphical interface for users who prefer visual interaction over CLI commands.

### Improvements

#### Multi-audio notes

Support notes that span multiple audio files (e.g., a conversation recorded in parts).

**What this enables:**
- Merge transcriptions from multiple audio files into a single note
- Link multiple audio assets to one note with clear relationships
- Preserve chronological order and context across file boundaries

#### Better categorization

Improve category consistency and organization.

**Features:**
- **Context-aware categorization:** Provide the LLM with existing category structure so it extends rather than duplicates
- **Subcategories:** Support hierarchical organization (e.g., `Work/Projects`, `Personal/Health`)
- **Category management:** Commands to merge, rename, or reorganize categories

#### Obsidian integration

Seamless integration with Obsidian vaults for users who use Obsidian as their knowledge base.

