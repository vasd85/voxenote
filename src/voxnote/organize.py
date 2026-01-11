from __future__ import annotations

import logging
import shutil
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional
from uuid import uuid4

from .config import load_config
from .models import (
    AppConfig,
    NoteAnalysis,
    NoteContext,
    NotePaths,
    TranscriptionResult,
)

logger = logging.getLogger(__name__)


def _slugify(value: str, max_length: int = 80) -> str:
    # Unicode-safe slugify for macOS filesystem
    value_norm = unicodedata.normalize("NFKC", value).strip().lower()
    result_chars = []
    prev_dash = False
    for ch in value_norm:
        if ch.isalnum():
            result_chars.append(ch)
            prev_dash = False
        elif ch in {" ", "-", "_"}:
            if not prev_dash and result_chars:
                result_chars.append("-")
                prev_dash = True
        # ignore other chars
        if len(result_chars) >= max_length:
            break

    slug = "".join(result_chars).strip("-") or "note"
    return slug


def _build_markdown(
    note_id: str,
    config: AppConfig,
    analysis: NoteAnalysis,
    audio_rel_path: Path,
    recorded_at: datetime,
    source_audio_name: str,
    audio_metadata_dump: Optional[str],
) -> str:
    ts_str = recorded_at.isoformat(sep=" ", timespec="seconds")
    lines = [
        f"# {analysis.title}",
        "",
        f"- **ID:** {note_id}",
        f"- **Audio:** {audio_rel_path.as_posix()}",
        f"- **Source:** {source_audio_name}",
        f"- **Recorded at:** {ts_str}",
        f"- **Category:** {analysis.category}",
        f"- **Whisper model:** {config.transcription.model}",
        f"- **Transcription language:** {config.transcription.language}",
        "",
        "---",
        "",
    ]
    if audio_metadata_dump:
        lines.extend(
            [
                "## Audio metadata",
                "",
                "```json",
                audio_metadata_dump.strip(),
                "```",
                "",
                "---",
                "",
            ]
        )
    return "\n".join(lines)


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _archive_audio_file(
    source_path: Path,
    config: AppConfig,
    note_id: str,
) -> Path:
    """
    Archive the audio file and return the absolute path to the archived file.
    """
    source_path = source_path.expanduser().resolve()
    archive_filename = f"{note_id}_{source_path.name}"
    target_path = config.archive_dir / archive_filename
    config.archive_dir.mkdir(parents=True, exist_ok=True)

    shutil.move(str(source_path), str(target_path))
    return target_path


def _build_note_path(
    config: AppConfig,
    analysis: NoteAnalysis,
    note_id: str,
    created_at: datetime,
) -> Path:
    """Build the path for the note file, handling collisions."""
    category_slug = _slugify(analysis.category or "misc")
    title_slug = _slugify(analysis.title or note_id)

    category_dir = config.output_dir / category_slug
    category_dir.mkdir(parents=True, exist_ok=True)

    ts = created_at.strftime("%Y-%m-%d_%H-%M-%S")
    note_filename = f"{ts}_{title_slug}.md"
    note_path = category_dir / note_filename
    
    # Handle collision by appending ID fragment
    if note_path.exists():
        note_path = category_dir / f"{ts}_{title_slug}_{note_id[:8]}.md"

    return note_path


def _build_note_content(
    note_id: str,
    config: AppConfig,
    analysis: NoteAnalysis,
    transcription: TranscriptionResult,
    audio_archive_filename: str,
    recorded_at: datetime,
    source_audio_name: str,
    audio_metadata_dump: Optional[str],
) -> str:
    """Build the Markdown content for the note."""
    audio_rel_path = Path("archive") / audio_archive_filename

    header = _build_markdown(
        note_id=note_id,
        config=config,
        analysis=analysis,
        audio_rel_path=audio_rel_path,
        recorded_at=recorded_at,
        source_audio_name=source_audio_name,
        audio_metadata_dump=audio_metadata_dump,
    )
    
    content_parts = [header]
    if analysis.short_summary:
        content_parts.append(analysis.short_summary.strip())
        content_parts.append("")
        content_parts.append("---")
        content_parts.append("")

    content_parts.append(transcription.text.strip())

    return "\n".join(content_parts)


def _write_note_content(note_path: Path, content: str) -> None:
    """Write note content to file."""
    note_path.write_text(content, encoding="utf-8")


def organize_note(
    config: AppConfig,
    transcription: TranscriptionResult,
    analysis: NoteAnalysis,
    recorded_at: datetime,
    audio_metadata_dump: Optional[str] = None,
    source_audio_path: Optional[Path] = None,
) -> NoteContext:
    note_id = uuid4().hex
    created_at = recorded_at

    archive_source = (source_audio_path or transcription.audio_path).expanduser().resolve()
    
    # Pre-compute paths
    archive_filename = f"{note_id}_{archive_source.name}"
    note_path = _build_note_path(config, analysis, note_id, created_at)
    temp_note_path = note_path.with_suffix(".tmp")
    
    audio_archive_path = None
    
    try:
        # 1. Write note to temporary file
        note_content = _build_note_content(
            note_id=note_id,
            config=config,
            analysis=analysis,
            transcription=transcription,
            audio_archive_filename=archive_filename,
            recorded_at=created_at,
            source_audio_name=archive_source.name,
            audio_metadata_dump=audio_metadata_dump,
        )
        _write_note_content(temp_note_path, note_content)
        
        # 2. Move audio to archive
        config.archive_dir.mkdir(parents=True, exist_ok=True)
        audio_archive_path = _archive_audio_file(
            source_path=archive_source,
            config=config,
            note_id=note_id,
        )
        
        # 3. Atomically rename note from .tmp to .md
        temp_note_path.replace(note_path)
        
    except Exception:
        # Rollback: return audio to original location if it was moved
        if audio_archive_path and audio_archive_path.exists():
            try:
                shutil.move(str(audio_archive_path), str(archive_source))
            except Exception as rollback_exc:
                logger.warning(
                    f"Failed to rollback audio file move: {rollback_exc}. "
                    f"Audio may remain in archive at {audio_archive_path}"
                )
        
        # Clean up temporary note file if it exists
        if temp_note_path.exists():
            try:
                temp_note_path.unlink()
            except Exception:
                pass
        
        raise

    paths = NotePaths(note_path=note_path, audio_archive_path=audio_archive_path)
    return NoteContext(
        id=note_id,
        transcription=transcription,
        analysis=analysis,
        paths=paths,
    )


def cli_organize_dummy(audio_path: str, title: str, category: str) -> None:
    # Simple helper for manual testing
    config = load_config()
    transcription = TranscriptionResult(audio_path=Path(audio_path), text="Dummy text")
    analysis = NoteAnalysis(title=title, category=category)
    ctx = organize_note(
        config,
        transcription,
        analysis,
        recorded_at=datetime.now(),
        source_audio_path=Path(audio_path),
    )
    print(f"Created note at {ctx.paths.note_path}")
