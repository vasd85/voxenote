# Changelog

All notable changes to this project will be documented in this file.

This project follows [Semantic Versioning](https://semver.org/).
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [0.1.0] - 2026-01-09

### Added

- Fully local pipeline: optional VAD trimming, transcription via `mlx-whisper`, analysis via Ollama, Markdown note generation, and audio archiving.
- CLI commands: `init`, `collect`, `prepare-vad`, `vad-trim`, `process`, `status`, `doctor`.
- Local state in `.voxnote/` (JSONL indexes + caches), gitignored by default.

### Privacy

- No cloud LLMs.
- `.cursorignore` excludes `.voxnote/` directory from indexing to reduce the risk of accidental transcript exposure.

