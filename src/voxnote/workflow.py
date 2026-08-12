from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Generator, Iterator, List, Optional

from .audio_prepare import prepare_wav_for_vad
from .cache_paths import build_prepared_cache_path, find_prepared_cache_path
from .analyze import analyze_text
from .audio_metadata import AudioMetadata, collect_audio_metadata
from .collect_plan import build_collect_source_plan
from .diarize import (
    DIARIZATION_DISABLED_FINGERPRINT,
    diarization_fingerprint,
    diarization_models_dir,
    diarization_models_ready,
    diarize_audio,
    ensure_diarization_models,
    probe_diarization_engine,
)
from .models import NoteContext, TranscriptionResult
from .organize import organize_note
from .runtime import RuntimeContext
from .speaker_merge import assign_speakers, count_speakers, render_labeled_transcript
from .state import (
    CollectedAudioEntry,
    FailedTranscriptionEntry,
    load_original_metadata,
    save_original_metadata,
    ProcessedAudioEntry,
    append_collected_entry,
    append_failed_transcription_entry,
    append_processed_entry,
    compute_file_hash,
    find_processed_entry,
    get_failed_transcription,
    load_processed_hashes,
    load_collected_original_hashes,
    purge_failed_transcription,
)
from .transcribe import transcribe_file
from .vad_trim import get_trimmed_cache_path, trim_audio_file

import shutil

logger = logging.getLogger(__name__)


@dataclass
class WorkflowEvent:
    type: str
    message: str
    file: Optional[Path] = None
    data: Optional[dict] = None


def _permission_denied_message(source_dir: Path) -> str:
    """Build a human-friendly message for a denied source directory."""
    source_str = str(source_dir)
    base = f"Permission denied reading source: {source_str}."
    # macOS TCC: ~/Library/Group Containers/... (Voice Memos etc.)
    # and other protected locations require explicit user consent.
    protected_hint = any(
        marker in source_str
        for marker in (
            "/Library/Group Containers/",
            "/Library/Mobile Documents/",
            "/Library/Containers/",
            "/Library/Application Support/",
        )
    )
    if protected_hint:
        base += (
            " This folder is protected by macOS. Grant Full Disk Access to the "
            "terminal you run voxnote from: System Settings > Privacy & Security "
            "> Full Disk Access. Then fully quit and reopen the terminal."
        )
    else:
        base += (
            " On macOS, grant the terminal Full Disk Access in System Settings > Privacy & Security > Full Disk Access."
        )
    return base


def _iter_source_files(
    source_dir: Path,
    *,
    recursive: bool,
    onerror: Optional[Callable[[OSError], None]] = None,
) -> Iterator[Path]:
    """Yield file paths under source_dir, reporting per-directory read errors.

    Unlike Path.rglob(), this surfaces PermissionError on subdirectories via
    the onerror callback instead of silently skipping them.
    """
    if not recursive:
        try:
            with os.scandir(source_dir) as it:
                for entry in it:
                    yield Path(entry.path)
        except OSError as exc:
            if onerror is not None:
                onerror(exc)
        return

    for dirpath, _dirnames, filenames in os.walk(source_dir, onerror=onerror):
        base = Path(dirpath)
        for name in filenames:
            yield base / name


class Workflow:
    def __init__(self, runtime: RuntimeContext):
        self.runtime = runtime
        self.config = runtime.config
        self.state_dir = runtime.state_dir

    def _assert_in_input(self, path: Path) -> None:
        input_root = self.config.input_dir.expanduser().resolve()
        try:
            path.expanduser().resolve().relative_to(input_root)
        except ValueError as exc:
            raise ValueError(
                f"File must be inside input/ directory. "
                f"File: {path}, Input directory: {input_root}. "
                f"Move the file to input/ or use a relative path from input/."
            ) from exc

    def run_pipeline(
        self, *, force: bool = False, diarize: Optional[bool] = None
    ) -> Generator[WorkflowEvent, None, None]:
        """Run the full pipeline (collect -> prepare-vad -> vad-trim -> process).

        Which steps run is driven by `config.pipeline`; disabled steps are skipped.
        Steps run in order and never abort one another: `process` falls back to the
        best available source (trimmed > prepared > original), so a skipped or failed
        preprocessing step still yields notes. The run is idempotent — cached and
        already-processed files are reused on re-runs. `force` rebuilds the prepared
        and trimmed caches and reprocesses files that were already processed.
        `diarize` overrides `diarization.enabled` for this run.
        """
        pipeline = self.config.pipeline
        step_defs: list[tuple[str, str, Callable[[], Generator[WorkflowEvent, None, None]]]] = [
            ("collect", "Collect", lambda: self.collect_files([], "auto")),
            ("prepare_vad", "Prepare for VAD", lambda: self.prepare_vad_files(files=None, force=force)),
            ("vad_trim", "VAD trim", lambda: self.vad_trim_files(files=None, force=force)),
            (
                "process",
                "Process",
                lambda: self.process_files(files=None, force_reprocess=force, diarize=diarize),
            ),
        ]
        enabled = [step for step in step_defs if getattr(pipeline, step[0])]

        if not enabled:
            yield WorkflowEvent(
                "info",
                "No pipeline steps enabled. Enable at least one under `pipeline:` in config.yaml.",
            )
            return

        yield WorkflowEvent(
            "pipeline_plan",
            "Pipeline plan",
            data={"steps": [{"name": name, "label": label} for name, label, _ in enabled]},
        )

        step_summaries: list[dict] = []
        total = len(enabled)
        for index, (name, label, make_generator) in enumerate(enabled, start=1):
            yield WorkflowEvent(
                "step",
                label,
                data={"step": name, "label": label, "index": index, "total": total},
            )
            step_summary: Optional[dict] = None
            last_info: Optional[str] = None
            for event in make_generator():
                if event.type == "summary":
                    step_summary = {"step": name, "label": label, "stats": event.data or {}}
                    yield WorkflowEvent("step_summary", f"{label} complete", data=step_summary)
                else:
                    if event.type == "info":
                        last_info = event.message
                    yield event
            if step_summary is None:
                # Step ran but emitted no summary (early-return on empty input). Record a
                # row anyway so the final recap lists every step that ran; the info event
                # was already passed through, so don't emit a redundant step_summary.
                step_summary = {"step": name, "label": label, "stats": {}, "message": last_info or "nothing to do"}
            step_summaries.append(step_summary)

        yield WorkflowEvent("summary", "Pipeline complete", data={"steps": step_summaries})

    def prepare_vad_files(
        self,
        files: Optional[List[Path]] = None,
        *,
        force: bool = False,
    ) -> Generator[WorkflowEvent, None, None]:
        """
        Prepare audio for VAD: original -> prepared WAV (mono 16kHz + denoise).
        """
        if files:
            target_files = files
        else:
            target_files = []
            for entry in sorted(self.config.input_dir.iterdir()):
                if not entry.is_file():
                    continue
                ext = entry.suffix.lower().lstrip(".")
                if ext in self.config.processing.supported_formats:
                    target_files.append(entry)

        if not target_files:
            yield WorkflowEvent("info", "No audio files to prepare.")
            return

        prepared_count = 0
        skipped_count = 0
        errors = 0

        for original_path in target_files:
            try:
                original_path = original_path.expanduser().resolve()
                self._assert_in_input(original_path)
                original_hash = compute_file_hash(original_path)

                existing = find_prepared_cache_path(original_hash=original_hash, state_dir=self.state_dir)
                if existing is not None and existing.exists() and not force:
                    skipped_count += 1
                    yield WorkflowEvent(
                        "skipped", f"Skipping (already prepared): {original_path.name}", file=original_path
                    )
                    continue

                yield WorkflowEvent("processing", f"Preparing: {original_path.name}", file=original_path)

                meta = load_original_metadata(original_hash, state_dir=self.state_dir)
                if meta is None:
                    meta = collect_audio_metadata(original_path)
                    save_original_metadata(
                        original_hash=original_hash,
                        original_source_path=original_path,
                        original_source_name=original_path.name,
                        meta=meta,
                        state_dir=self.state_dir,
                    )

                prepared_path = build_prepared_cache_path(
                    original_hash=original_hash,
                    original_name=original_path.name,
                    state_dir=self.state_dir,
                )
                if prepared_path.exists() and force:
                    prepared_path.unlink(missing_ok=True)

                prepare_wav_for_vad(
                    original_path, prepared_path, timeout_s=self.config.processing.ffmpeg_prepare_timeout_s
                )
                prepared_count += 1
                yield WorkflowEvent("completed", f"Prepared: {original_path.name}", file=original_path)
            except Exception as exc:
                errors += 1
                error_msg = f"Error preparing {original_path.name}: {exc}"
                if "ffmpeg" in str(exc).lower():
                    error_msg += " Check that ffmpeg is installed: `brew install ffmpeg` or run `voxnote doctor`."
                elif "permission" in str(exc).lower():
                    error_msg += " On macOS, grant Full Disk Access in System Settings > Privacy & Security."
                yield WorkflowEvent("error", error_msg, file=original_path)

        yield WorkflowEvent(
            "summary",
            "Preparation complete",
            data={"prepared": prepared_count, "skipped": skipped_count, "errors": errors},
        )

    def process_files(
        self,
        files: Optional[List[Path]] = None,
        force_reprocess: bool = False,
        diarize: Optional[bool] = None,
    ) -> Generator[WorkflowEvent, None, None]:
        """
        Process audio files: transcribe, optionally diarize, analyze, and organize.
        `diarize` overrides `diarization.enabled` for this run.
        Yields WorkflowEvent updates for the UI.
        """
        if diarize is not None:
            self.config.diarization.enabled = diarize

        if files:
            target_files = files
        else:
            target_files = []
            for entry in sorted(self.config.input_dir.iterdir()):
                if not entry.is_file():
                    continue
                ext = entry.suffix.lower().lstrip(".")
                if ext in self.config.processing.supported_formats:
                    target_files.append(entry)

        if not target_files:
            yield WorkflowEvent("info", "No audio files to process.")
            return

        current_fingerprint = diarization_fingerprint(self.config)

        if self.config.diarization.enabled:
            # Fail the whole step once instead of repeating the same error per file.
            preflight_error = yield from self._prepare_diarization()
            if preflight_error is not None:
                yield WorkflowEvent("error", preflight_error)
                yield WorkflowEvent(
                    "summary",
                    "Processing complete",
                    data={"processed": 0, "skipped": 0, "failed": 0},
                )
                return

        processed_hashes = load_processed_hashes(state_dir=self.state_dir)

        # Track stats
        processed_count = 0
        skipped_count = 0
        failed_count = 0

        for audio in target_files:
            try:
                original_path = audio.expanduser().resolve()
                self._assert_in_input(original_path)
                original_hash = compute_file_hash(original_path)

                # Check if already processed
                if not force_reprocess and original_hash in processed_hashes:
                    needs_reprocess = False
                    processed_entry = find_processed_entry(original_hash, state_dir=self.state_dir)
                    prev_transcribed_hash = None
                    if processed_entry:
                        prev_transcribed_hash = processed_entry.get("transcribed_file_hash")

                    # Entries written before diarization existed were produced with it off.
                    prev_fingerprint = (processed_entry or {}).get(
                        "diarization_fingerprint"
                    ) or DIARIZATION_DISABLED_FINGERPRINT
                    if prev_fingerprint != current_fingerprint:
                        needs_reprocess = True
                        yield WorkflowEvent(
                            "info",
                            f"Reprocessing {audio.name} (diarization settings changed)",
                            file=audio,
                        )

                    if prev_transcribed_hash:
                        cache_path = get_trimmed_cache_path(original_hash=original_hash, state_dir=self.state_dir)
                        if cache_path.exists():
                            current_trimmed_hash = compute_file_hash(cache_path)
                            if current_trimmed_hash != prev_transcribed_hash:
                                needs_reprocess = True
                                yield WorkflowEvent(
                                    "info", f"Reprocessing {audio.name} (trimmed cache changed)", file=audio
                                )

                    if not needs_reprocess:
                        skipped_count += 1
                        yield WorkflowEvent("skipped", f"Skipped {audio.name} (already processed)", file=audio)
                        continue

                yield WorkflowEvent("processing", f"Processing {audio.name}", file=audio)

                # Metadata collection (prefer stored metadata collected before copying)
                meta = load_original_metadata(original_hash, state_dir=self.state_dir)
                if meta is None:
                    meta = collect_audio_metadata(original_path)
                    save_original_metadata(
                        original_hash=original_hash,
                        original_source_path=original_path,
                        original_source_name=original_path.name,
                        meta=meta,
                        state_dir=self.state_dir,
                    )
                yield WorkflowEvent("metadata", "Metadata loaded", file=audio, data={"meta": meta})

                # Transcription (with diarization when enabled)
                transcription = self._get_transcription(original_path, original_hash, fingerprint=current_fingerprint)
                yield WorkflowEvent("transcribed", "Transcription complete", file=audio)

                speaker_count = transcription.speaker_count
                if speaker_count is not None:
                    turn_count = transcription.turn_count
                    message = f"Diarization: {speaker_count} speaker(s)"
                    if turn_count is not None:
                        message += f", {turn_count} turn(s)"
                    yield WorkflowEvent(
                        "diarized",
                        message,
                        file=audio,
                        data={"speakers": speaker_count, "turns": turn_count},
                    )

                # Analysis
                try:
                    analysis = analyze_text(
                        self.config,
                        transcription.text,
                        state_dir=self.state_dir,
                        speaker_labeled=bool(speaker_count and speaker_count > 1),
                    )
                    yield WorkflowEvent("analyzed", "LLM Analysis complete", file=audio)

                    ctx = organize_note(
                        config=self.config,
                        transcription=transcription,
                        analysis=analysis,
                        recorded_at=meta.recorded_at or datetime.now(),
                        audio_metadata_dump=None,  # We can handle formatting in UI or pass object
                        source_audio_path=original_path,
                    )

                    # Cleanup failed entry if successful
                    purge_failed_transcription(original_path, state_dir=self.state_dir)

                    # Record processed state
                    self._record_processed(
                        original_path,
                        original_hash,
                        transcription,
                        ctx,
                        meta,
                        processed_hashes,
                        diarization_fingerprint_value=current_fingerprint,
                    )

                    processed_count += 1
                    yield WorkflowEvent(
                        "completed",
                        f"Created note: {ctx.paths.note_path.name}",
                        file=audio,
                        data={"note_path": ctx.paths.note_path},
                    )

                except Exception as exc:
                    failed_count += 1
                    self._handle_analysis_failure(
                        original_path,
                        transcription,
                        exc,
                        diarization_fingerprint_value=current_fingerprint,
                    )
                    yield WorkflowEvent(
                        "error",
                        f"Analysis failed: {str(exc)}",
                        file=audio,
                        data={"error": str(exc), "saved_transcription": True},
                    )
            except Exception as exc:
                failed_count += 1
                error_msg = f"Processing error: {str(exc)}"
                if "mlx_whisper" in str(exc).lower():
                    error_msg += (
                        " Check that mlx_whisper is installed: `pip install mlx-whisper` or run `voxnote doctor`."
                    )
                elif "sherpa" in str(exc).lower() or "diarization" in str(exc).lower():
                    error_msg += (
                        " Check the diarization setup with `voxnote doctor`, "
                        "or disable it for this run: `voxnote process --no-diarize`."
                    )
                elif "ollama" in str(exc).lower():
                    error_msg += " Check that Ollama is running: `ollama serve` or verify config.yaml (llm.base_url)."
                elif "permission" in str(exc).lower():
                    error_msg += " On macOS, grant Full Disk Access in System Settings > Privacy & Security."
                yield WorkflowEvent("error", error_msg, file=audio, data={"error": str(exc)})

        yield WorkflowEvent(
            "summary",
            "Processing complete",
            data={"processed": processed_count, "skipped": skipped_count, "failed": failed_count},
        )

    def _prepare_diarization(self) -> Generator[WorkflowEvent, None, Optional[str]]:
        """Check the engine and fetch the models once. Returns an error message on failure."""
        engine_error = probe_diarization_engine()
        if engine_error is not None:
            return f"Diarization is enabled but the engine is not usable: {engine_error}"

        try:
            if not diarization_models_ready(self.config, state_dir=self.state_dir):
                models_dir = diarization_models_dir(self.state_dir)
                yield WorkflowEvent(
                    "info",
                    f"Fetching the missing diarization models (one-time, up to ~35 MB) into {models_dir}...",
                )
            ensure_diarization_models(self.config, state_dir=self.state_dir)
        except Exception as exc:
            return (
                f"Diarization is enabled but its models are not ready: {exc} "
                "Fix the models or disable diarization for this run: `voxnote process --no-diarize`."
            )
        return None

    def _select_transcription_source(self, original_path: Path, original_hash: str) -> Path:
        """Best available source for both transcription and diarization: trimmed > prepared > original."""
        trimmed_path = get_trimmed_cache_path(original_hash=original_hash, state_dir=self.state_dir)
        if trimmed_path.exists():
            return trimmed_path
        prepared = find_prepared_cache_path(original_hash=original_hash, state_dir=self.state_dir)
        if prepared is not None and prepared.exists():
            return prepared
        return original_path

    def _get_transcription(self, original_path: Path, original_hash: str, *, fingerprint: str) -> TranscriptionResult:
        """Helper to get transcription (fresh, or reused from a failed-analysis retry)."""
        failed = get_failed_transcription(original_path, state_dir=self.state_dir)
        if failed is not None:
            stored_fingerprint = failed.get("diarization_fingerprint") or DIARIZATION_DISABLED_FINGERPRINT
            text = failed.get("text")
            # Reuse the saved text only when it was produced with the current settings,
            # so speaker labels survive a retry and a settings change forces a re-run.
            if isinstance(text, str) and stored_fingerprint == fingerprint:
                stored_speakers = failed.get("speaker_count")
                stored_turns = failed.get("turn_count")
                return TranscriptionResult(
                    audio_path=original_path,
                    text=text,
                    speaker_count=stored_speakers if isinstance(stored_speakers, int) else None,
                    turn_count=stored_turns if isinstance(stored_turns, int) else None,
                )

        diarization_enabled = self.config.diarization.enabled
        audio_for_transcription = self._select_transcription_source(original_path, original_hash)
        if not diarization_enabled:
            return transcribe_file(
                self.config,
                audio_for_transcription,
                state_dir=self.state_dir,
                word_timestamps=False,
            )

        # Diarization runs first: it is far cheaper than transcription, so a broken
        # setup fails before minutes of mlx-whisper work are spent and lost. Both
        # steps use the same file, so they share one timeline.
        try:
            diarization = diarize_audio(self.config, audio_for_transcription, state_dir=self.state_dir)
        except Exception as exc:
            # Engine errors (e.g. raw onnxruntime text) may not mention diarization
            # at all; re-wrap so the per-file error hint always fires.
            raise RuntimeError(f"Diarization failed: {type(exc).__name__}: {exc}") from exc

        transcription = transcribe_file(
            self.config,
            audio_for_transcription,
            state_dir=self.state_dir,
            word_timestamps=True,
        )
        blocks = assign_speakers(transcription.segments, diarization.turns)
        speaker_count = count_speakers(blocks)
        # A single speaker must look exactly like a run without diarization.
        text = render_labeled_transcript(blocks) if speaker_count > 1 else transcription.text
        return transcription.model_copy(
            update={
                "text": text,
                "speaker_count": speaker_count,
                "turn_count": len(diarization.turns),
            }
        )

    def _handle_analysis_failure(
        self,
        original_path: Path,
        transcription: TranscriptionResult,
        exc: Exception,
        *,
        diarization_fingerprint_value: str,
    ) -> None:
        """Save transcription to failed log."""
        append_failed_transcription_entry(
            FailedTranscriptionEntry(
                created_at=datetime.now(),
                audio_path=str(original_path),
                text=transcription.text,
                error=str(exc),
                diarization_fingerprint=diarization_fingerprint_value,
                speaker_count=transcription.speaker_count,
                turn_count=transcription.turn_count,
            ),
            state_dir=self.state_dir,
        )

    def _record_processed(
        self,
        original_path: Path,
        original_hash: str,
        transcription: TranscriptionResult,
        ctx: NoteContext,
        meta: AudioMetadata,
        processed_hashes: set,
        *,
        diarization_fingerprint_value: str,
    ) -> None:
        """Record the processed entry to state."""
        if transcription.audio_path == original_path:
            transcribed_hash = original_hash
        else:
            transcribed_hash = compute_file_hash(transcription.audio_path)

        append_processed_entry(
            ProcessedAudioEntry(
                processed_at=datetime.now(),
                original_hash=original_hash,
                original_name=original_path.name,
                original_path=str(original_path),
                archive_path=str(ctx.paths.audio_archive_path),
                note_path=str(ctx.paths.note_path),
                recorded_at=meta.recorded_at,
                recorded_at_source=meta.recorded_at_source,
                transcribed_file_hash=transcribed_hash,
                transcribed_path=str(transcription.audio_path),
                diarization_fingerprint=diarization_fingerprint_value,
                speaker_count=transcription.speaker_count,
            ),
            state_dir=self.state_dir,
        )
        processed_hashes.add(original_hash)

    def collect_files(
        self,
        sources: List[Path],
        recursive_mode: str,
    ) -> Generator[WorkflowEvent, None, None]:
        """Collect audio files from sources."""
        collected_hashes = load_collected_original_hashes(state_dir=self.state_dir)
        processed_hashes = load_processed_hashes(state_dir=self.state_dir)
        all_known_hashes = collected_hashes | processed_hashes

        source_plan = build_collect_source_plan(
            self.config,
            cli_sources=sources,
            recursive_mode=recursive_mode,
        )

        if not source_plan:
            yield WorkflowEvent("info", "No sources configured.")
            return

        yield WorkflowEvent("plan", "Source plan built", data={"plan": source_plan})

        copied_count = 0
        skipped_count = 0

        for src in source_plan:
            source_dir = src.source_dir
            if not source_dir.exists():
                yield WorkflowEvent("warning", f"Source does not exist: {source_dir}")
                continue

            # Probe top-level read access upfront: rglob() silently returns
            # nothing on PermissionError (e.g. macOS TCC on ~/Library/...),
            # which makes "Copied: 0, Skipped: 0" indistinguishable from an
            # empty folder. An explicit scandir() surfaces the real cause.
            try:
                with os.scandir(source_dir) as probe:
                    for _ in probe:
                        break
            except PermissionError:
                yield WorkflowEvent("error", _permission_denied_message(source_dir))
                continue
            except OSError as exc:
                yield WorkflowEvent("error", f"Cannot read source {source_dir}: {exc}")
                continue

            access_errors: list[tuple[str, str]] = []

            def _on_walk_error(exc: OSError) -> None:
                access_errors.append((str(exc.filename or source_dir), exc.strerror or str(exc)))

            iterator = _iter_source_files(source_dir, recursive=src.recursive, onerror=_on_walk_error)

            for source_path in iterator:
                if not source_path.is_file():
                    continue
                ext = source_path.suffix.lower().lstrip(".")
                if ext not in self.config.processing.supported_formats:
                    yield WorkflowEvent(
                        "skipped", f"Ignored {source_path.name} (unsupported format: {ext})", file=source_path
                    )
                    continue

                original_hash = compute_file_hash(source_path)
                if original_hash in all_known_hashes:
                    skipped_count += 1
                    continue

                target_name = f"{original_hash}_{source_path.name}"
                target_path = self.config.input_dir / target_name

                if target_path.exists():
                    skipped_count += 1
                    continue

                yield WorkflowEvent("processing", f"Copying: {source_path.name}", file=source_path)

                try:
                    meta = collect_audio_metadata(source_path)
                    save_original_metadata(
                        original_hash=original_hash,
                        original_source_path=source_path,
                        original_source_name=source_path.name,
                        meta=meta,
                        state_dir=self.state_dir,
                    )

                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(str(source_path), str(target_path))

                    entry = CollectedAudioEntry(
                        collected_at=datetime.now(),
                        original_hash=original_hash,
                        original_source_path=str(source_path.expanduser().resolve()),
                        original_source_name=source_path.name,
                        input_path=str(target_path),
                    )
                    append_collected_entry(entry, state_dir=self.state_dir)
                    copied_count += 1
                    yield WorkflowEvent("completed", "Copied", file=source_path)
                except PermissionError as exc:
                    yield WorkflowEvent(
                        "error",
                        f"Permission denied: {source_path}. "
                        "On macOS, grant Full Disk Access in System Settings > Privacy & Security.",
                        file=source_path,
                    )
                except Exception as exc:
                    error_msg = f"Error copying {source_path.name}: {exc}"
                    if "permission" in str(exc).lower():
                        error_msg += " On macOS, grant Full Disk Access in System Settings > Privacy & Security."
                    elif "not found" in str(exc).lower() or "no such file" in str(exc).lower():
                        error_msg += f" Check that the source path exists: {source_path}"
                    yield WorkflowEvent("error", error_msg, file=source_path)

            for path_str, reason in access_errors:
                if "permission" in reason.lower() or "not permitted" in reason.lower():
                    yield WorkflowEvent(
                        "warning",
                        f"Skipped {path_str}: {reason}. "
                        "On macOS, grant Full Disk Access to your terminal "
                        "(System Settings > Privacy & Security > Full Disk Access).",
                    )
                else:
                    yield WorkflowEvent("warning", f"Skipped {path_str}: {reason}")

        yield WorkflowEvent("summary", "Collection complete", data={"copied": copied_count, "skipped": skipped_count})

    def vad_trim_files(
        self,
        files: Optional[List[Path]] = None,
        dry_run: bool = False,
        force: bool = False,
        threshold: Optional[float] = None,
        min_silence_duration_ms: Optional[int] = None,
        min_speech_duration_ms: Optional[int] = None,
        speech_pad_ms: Optional[int] = None,
    ) -> Generator[WorkflowEvent, None, None]:
        """Run VAD trimming on files."""
        # Update config overrides
        if threshold is not None:
            self.config.vad.threshold = threshold
        if min_silence_duration_ms is not None:
            self.config.vad.min_silence_duration_ms = min_silence_duration_ms
        if min_speech_duration_ms is not None:
            self.config.vad.min_speech_duration_ms = min_speech_duration_ms
        if speech_pad_ms is not None:
            self.config.vad.speech_pad_ms = speech_pad_ms

        if files:
            target_files = files
        else:
            target_files = []
            for entry in sorted(self.config.input_dir.iterdir()):
                if not entry.is_file():
                    continue
                ext = entry.suffix.lower().lstrip(".")
                if ext in self.config.processing.supported_formats:
                    target_files.append(entry)

        if not target_files:
            yield WorkflowEvent("info", "No audio files to process.")
            return

        processed = 0
        skipped_cached = 0
        skipped_no_speech = 0
        errors = 0

        for audio in target_files:
            try:
                audio = audio.expanduser().resolve()
                self._assert_in_input(audio)

                if not force:
                    original_hash = compute_file_hash(audio)
                    cache_path = get_trimmed_cache_path(original_hash=original_hash, state_dir=self.state_dir)
                    if cache_path.exists() and cache_path.stat().st_size > 0:
                        skipped_cached += 1
                        yield WorkflowEvent("skipped", f"Skipping (already trimmed): {audio.name}", file=audio)
                        continue

                yield WorkflowEvent("processing", f"Trimming: {audio.name}", file=audio)

                success = trim_audio_file(self.config, audio, dry_run=dry_run, state_dir=self.state_dir)

                if success:
                    processed += 1
                    msg = f"Would trim: {audio.name}" if dry_run else f"Trimmed: {audio.name}"
                    yield WorkflowEvent("completed", msg, file=audio)
                else:
                    skipped_no_speech += 1
                    yield WorkflowEvent("skipped", f"No speech segments: {audio.name}", file=audio)

            except Exception as exc:
                errors += 1
                error_msg = f"Error trimming {audio.name}: {exc}"
                if "ffmpeg" in str(exc).lower():
                    error_msg += " Check that ffmpeg is installed: `brew install ffmpeg` or run `voxnote doctor`."
                elif "torch" in str(exc).lower() or "silero" in str(exc).lower():
                    error_msg += " Check that torch and torchaudio are installed: `uv sync` or run `voxnote doctor`."
                yield WorkflowEvent("error", error_msg, file=audio)

        yield WorkflowEvent(
            "summary",
            "VAD Trim complete",
            data={
                "processed": processed,
                "skipped_cached": skipped_cached,
                "skipped_no_speech": skipped_no_speech,
                "errors": errors,
            },
        )
