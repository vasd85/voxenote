# Changelog

All notable changes to this project will be documented in this file.

This project follows [Semantic Versioning](https://semver.org/).
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- Optional speaker diarization for multi-speaker recordings: `process` splits the transcript into `Speaker N:` blocks,
  passes them to the LLM analysis, and records the speaker count in the note. Off by default; enable with
  `diarization.enabled` or per run with `voxnote process --diarize` / `--no-diarize`.
- Diarization runs fully offline via `sherpa-onnx`; its two models (~35 MB) are downloaded once from the k2-fsa GitHub
  releases into `.voxnote/diarization/`. `voxnote doctor` reports their status when diarization is enabled.

### Changed

- `process` re-runs a file when the diarization settings that produced its note have changed. State entries written
  before this release stay readable and keep being skipped.

## [0.1.0] - 2026-01-09

### Added

- Fully local pipeline: optional VAD trimming, transcription via `mlx-whisper`, analysis via Ollama, Markdown note generation, and audio archiving.
- CLI commands: `init`, `collect`, `prepare-vad`, `vad-trim`, `process`, `status`, `doctor`.
- Local state in `.voxnote/` (JSONL indexes + caches), gitignored by default.

### Privacy

- No cloud LLMs.
- `.cursorignore` excludes `.voxnote/` directory from indexing to reduce the risk of accidental transcript exposure.

