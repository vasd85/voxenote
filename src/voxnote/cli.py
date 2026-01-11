from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import click
from pydantic import ValidationError
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table

from .doctor import run_doctor
from .runtime import RuntimeContext, build_runtime
from .workflow import Workflow, WorkflowEvent

console = Console()


def _runtime_or_exit(ctx: click.Context) -> RuntimeContext:
    """
    Build runtime context from config, or exit with user-friendly error.
    """
    cfg_override: Optional[Path] = ctx.obj.get("config_path")
    try:
        return build_runtime(cfg_override)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        console.print("Run `voxnote init` to create a default config.")
        ctx.exit(1)
        raise  # for type checkers
    except ValidationError as exc:
        console.print("[red]Invalid config.yaml[/red]")
        for err in exc.errors():
            loc = ".".join(str(p) for p in err.get("loc", [])) or "<root>"
            msg = err.get("msg", "Invalid value")
            console.print(f"  - {loc}: {msg}")
        console.print("Fix the config or run `voxnote init --force` to regenerate defaults.")
        ctx.exit(2)
        raise  # for type checkers


class OrderedGroup(click.Group):
    def list_commands(self, ctx: click.Context) -> list[str]:
        """Return commands in the desired order."""
        return ["init", "doctor", "collect", "prepare-vad", "vad-trim", "process", "status"]


@click.group(cls=OrderedGroup)
@click.version_option(package_name="voxnote", prog_name="voxnote")
@click.option(
    "--config",
    "config_path",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Path to config.yaml (defaults to project root).",
)
@click.pass_context
def main(ctx: click.Context, config_path: Optional[Path]) -> None:
    """Transcribe audio notes and organize them into markdown files."""
    logging.basicConfig(
        level=logging.WARNING,
        format="%(levelname)s: %(message)s",
    )
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config_path


@main.command()
@click.option("--force", is_flag=True, help="Overwrite existing config.yaml with defaults")
@click.pass_context
def init(ctx: click.Context, force: bool) -> None:
    """Initialize default config.yaml and required directories."""
    from .config import DEFAULT_CONFIG_PATH, load_config

    cfg_override: Optional[Path] = ctx.obj.get("config_path")
    cfg_path = (cfg_override or DEFAULT_CONFIG_PATH).expanduser().resolve()
    
    if cfg_path.exists() and not force:
        console.print(f"[yellow]Config already exists at {cfg_path}. Use --force to overwrite.[/yellow]")
        return

    # Check for template
    template_path = cfg_path.parent / "config.example.yaml"
    if not template_path.exists():
        console.print(
            f"[red]Template config.example.yaml not found next to {cfg_path}.[/red]\n"
            "Add this file to the project root and retry.\n"
            f"Expected location: {template_path}"
        )
        ctx.exit(1)

    content = template_path.read_text(encoding="utf-8")

    cfg_path.write_text(content, encoding="utf-8")

    # Ensure directories exist
    config = load_config(cfg_path)
    config.input_dir.mkdir(parents=True, exist_ok=True)
    config.output_dir.mkdir(parents=True, exist_ok=True)
    config.archive_dir.mkdir(parents=True, exist_ok=True)

    console.print(f"[green]Config written to {cfg_path}[/green]")


@main.command()
@click.pass_context
def doctor(ctx: click.Context) -> None:
    """Run dependency checks (ffmpeg, mlx-whisper, Ollama, denoise model)."""
    runtime = _runtime_or_exit(ctx)
    with console.status("[bold green]Running diagnostics...[/bold green]"):
        results = run_doctor(runtime)

    table = Table(title="System Diagnostics", show_header=True)
    table.add_column("Check", style="bold")
    table.add_column("Status")
    table.add_column("Info")

    has_failures = False
    for res in results:
        status_style = "green" if res.ok else "red"
        status_icon = "✅" if res.ok else "❌"
        table.add_row(
            res.name,
            f"[{status_style}]{status_icon} {'OK' if res.ok else 'FAIL'}[/{status_style}]",
            res.info
        )
        if not res.ok:
            has_failures = True

    console.print(table)
    if has_failures:
        ctx.exit(1)


@main.command()
@click.option(
    "--source",
    "sources",
    multiple=True,
    type=click.Path(exists=True, file_okay=False, dir_okay=True),
    help="Directory to scan and copy audio files from (can be repeated). If omitted, uses config.yaml sources.",
)
@click.option(
    "--recursive-mode",
    type=click.Choice(["auto", "on", "off"]),
    default="auto",
    show_default=True,
    help="Recursive source scan. auto=use config.yaml per-source setting when available; otherwise on.",
)
@click.pass_context
def collect(ctx: click.Context, sources: tuple[str, ...], recursive_mode: str) -> None:
    """Collect original audio files from sources into input directory."""
    runtime = _runtime_or_exit(ctx)
    workflow = Workflow(runtime)
    
    cli_source_dirs = [Path(s).expanduser().resolve() for s in sources] if sources else []
    
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
        transient=True,
    ) as progress:
        task_id = progress.add_task("Collecting...", total=None)
        
        for event in workflow.collect_files(cli_source_dirs, recursive_mode):
            if event.type == "info":
                progress.console.print(f"[blue]ℹ️ {event.message}[/blue]")
            elif event.type == "plan":
                plan = event.data["plan"] if event.data else []
                progress.console.print("[bold]Sources plan:[/bold]")
                for src in plan:
                     progress.console.print(f"- {src.source_dir} (recursive={src.recursive})")
            elif event.type == "processing":
                progress.update(task_id, description=f"[cyan]{event.message}[/cyan]")
            elif event.type == "skipped":
                progress.console.print(f"[dim]⏭️ {event.message}[/dim]")
            elif event.type == "completed":
                progress.console.print(f"  [green]✓ {event.message}[/green]")
            elif event.type == "warning":
                progress.console.print(f"[yellow]⚠️ {event.message}[/yellow]")
            elif event.type == "error":
                progress.console.print(f"[red]❌ {event.message}[/red]")
            elif event.type == "summary":
                progress.update(task_id, completed=100)
                copied = event.data.get("copied", 0)
                skipped = event.data.get("skipped", 0)
                console.print(f"\n[bold green]Copied: {copied}[/bold green], [dim]Skipped: {skipped}[/dim]")


@main.command("prepare-vad")
@click.option(
    "--file",
    "file_relpath",
    type=str,
    help="Audio file path relative to input/ (e.g. 'note.m4a' or 'subdir/note.m4a'). If omitted, processes all files in input/.",
)
@click.option("--force", is_flag=True, help="Rebuild prepared cache even if it already exists.")
@click.pass_context
def prepare_vad(ctx: click.Context, file_relpath: Optional[str], force: bool) -> None:
    """Prepare audio for VAD (original -> prepared WAV cache)."""
    runtime = _runtime_or_exit(ctx)
    workflow = Workflow(runtime)

    files: Optional[list[Path]]
    if file_relpath:
        p = (runtime.config.input_dir / Path(file_relpath)).expanduser().resolve()
        try:
            p.relative_to(runtime.config.input_dir.expanduser().resolve())
        except ValueError:
            console.print("[red]--file must be a path inside input/[/red]")
            console.print(f"Example: `voxnote prepare-vad --file note.m4a` or `voxnote prepare-vad --file subdir/note.m4a`")
            ctx.exit(2)
        if not p.exists() or not p.is_file():
            console.print(f"[red]File not found in input/: {file_relpath}[/red]")
            console.print(f"Check that the file exists at: {p}")
            console.print(f"Or list files with: `ls {runtime.config.input_dir}`")
            ctx.exit(2)
        files = [p]
    else:
        files = None

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
        transient=True,
    ) as progress:
        task_id = progress.add_task("Preparing...", total=None)

        for event in workflow.prepare_vad_files(files=files, force=force):
            if event.type == "info":
                progress.console.print(f"[blue]ℹ️ {event.message}[/blue]")
            elif event.type == "processing":
                progress.update(task_id, description=f"[cyan]{event.message}[/cyan]")
            elif event.type == "skipped":
                progress.console.print(f"[dim]⏭️ {event.message}[/dim]")
            elif event.type == "completed":
                progress.console.print(f"  [green]✓ {event.message}[/green]")
            elif event.type == "error":
                progress.console.print(f"[red]❌ {event.message}[/red]")
            elif event.type == "summary":
                progress.update(task_id, completed=100)
                prepared = event.data.get("prepared", 0)
                skipped = event.data.get("skipped", 0)
                errors = event.data.get("errors", 0)
                console.print(
                    f"\n[bold green]Prepared: {prepared}[/bold green], "
                    f"[dim]Skipped: {skipped}[/dim], "
                    f"[red]Errors: {errors}[/red]"
                )


@main.command("vad-trim")
@click.option(
    "--file",
    "file_relpath",
    type=str,
    help="Audio file path relative to input/ (e.g. 'note.m4a' or 'subdir/note.m4a'). If omitted, processes all files in input/.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Detect speech segments but do not modify files.",
)
@click.option(
    "--force",
    is_flag=True,
    help="Rebuild trimmed cache even if it already exists.",
)
@click.option(
    "--threshold",
    type=float,
    help="Speech detection threshold (0.0-1.0). Overrides config.yaml value.",
)
@click.option(
    "--min-silence-duration-ms",
    type=int,
    help="Minimum silence duration to split segments (ms). Overrides config.yaml value.",
)
@click.option(
    "--min-speech-duration-ms",
    type=int,
    help="Minimum speech duration to keep segment (ms). Overrides config.yaml value.",
)
@click.option(
    "--speech-pad-ms",
    "speech_pad_ms",
    type=int,
    help="Padding around speech segments (ms). Overrides config.yaml value.",
)
@click.pass_context
def vad_trim(
    ctx: click.Context,
    file_relpath: Optional[str],
    dry_run: bool,
    force: bool,
    threshold: Optional[float],
    min_silence_duration_ms: Optional[int],
    min_speech_duration_ms: Optional[int],
    speech_pad_ms: Optional[int],
) -> None:
    """Remove silence from audio files using Silero VAD."""
    runtime = _runtime_or_exit(ctx)
    workflow = Workflow(runtime)
    
    files: Optional[list[Path]]
    if file_relpath:
        p = (runtime.config.input_dir / Path(file_relpath)).expanduser().resolve()
        try:
            p.relative_to(runtime.config.input_dir.expanduser().resolve())
        except ValueError:
            console.print("[red]--file must be a path inside input/[/red]")
            console.print(f"Example: `voxnote vad-trim --file note.m4a` or `voxnote vad-trim --file subdir/note.m4a`")
            ctx.exit(2)
        if not p.exists() or not p.is_file():
            console.print(f"[red]File not found in input/: {file_relpath}[/red]")
            console.print(f"Check that the file exists at: {p}")
            console.print(f"Or list files with: `ls {runtime.config.input_dir}`")
            ctx.exit(2)
        files = [p]
    else:
        files = None
    
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
        transient=True,
    ) as progress:
        task_id = progress.add_task("Trimming...", total=None)

        for event in workflow.vad_trim_files(
            files=files,
            dry_run=dry_run,
            force=force,
            threshold=threshold,
            min_silence_duration_ms=min_silence_duration_ms,
            min_speech_duration_ms=min_speech_duration_ms,
            speech_pad_ms=speech_pad_ms,
        ):
            if event.type == "info":
                progress.console.print(f"[blue]ℹ️ {event.message}[/blue]")
            elif event.type == "processing":
                 progress.update(task_id, description=f"[cyan]{event.message}[/cyan]")
            elif event.type == "skipped":
                 progress.console.print(f"[dim]{event.message}[/dim]")
            elif event.type == "completed":
                 progress.console.print(f"[green]✓ {event.message}[/green]")
            elif event.type == "error":
                 progress.console.print(f"[red]❌ {event.message}[/red]")
            elif event.type == "summary":
                progress.update(task_id, completed=100)
                stats = event.data
                console.print("\n[bold]VAD Trim Summary[/bold]")
                console.print(f"Processed: {stats.get('processed')}")
                console.print(f"Skipped (cached): {stats.get('skipped_cached')}")
                console.print(f"Skipped (no speech): {stats.get('skipped_no_speech')}")
                console.print(f"Errors: {stats.get('errors')}")


@main.command()
@click.option(
    "--file",
    "file_relpath",
    type=str,
    help="Audio file path relative to input/ (e.g. 'note.m4a' or 'subdir/note.m4a'). If omitted, processes all files in input/.",
)
@click.option(
    "--force",
    "force_reprocess",
    is_flag=True,
    help="Reprocess files even if they have already been successfully processed.",
)
@click.option(
    "--show-metadata",
    is_flag=True,
    help="Print audio metadata for each file (compact JSON).",
)
@click.pass_context
def process(
    ctx: click.Context,
    file_relpath: Optional[str],
    force_reprocess: bool,
    show_metadata: bool,
) -> None:
    """Process audio files from input directory or a single file."""
    runtime = _runtime_or_exit(ctx)
    workflow = Workflow(runtime)
    
    files: Optional[list[Path]]
    if file_relpath:
        p = (runtime.config.input_dir / Path(file_relpath)).expanduser().resolve()
        try:
            p.relative_to(runtime.config.input_dir.expanduser().resolve())
        except ValueError:
            console.print("[red]--file must be a path inside input/[/red]")
            console.print(f"Example: `voxnote process --file note.m4a` or `voxnote process --file subdir/note.m4a`")
            ctx.exit(2)
        if not p.exists() or not p.is_file():
            console.print(f"[red]File not found in input/: {file_relpath}[/red]")
            console.print(f"Check that the file exists at: {p}")
            console.print(f"Or list files with: `ls {runtime.config.input_dir}`")
            ctx.exit(2)
        files = [p]
    else:
        files = None
    
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    ) as progress:
        task_id = progress.add_task("Processing...", total=None)
        
        for event in workflow.process_files(
            files=files,
            force_reprocess=force_reprocess
        ):
            if event.type == "info":
                progress.console.print(f"[blue]ℹ️ {event.message}[/blue]")
            elif event.type == "skipped":
                progress.console.print(f"[yellow]⏭️ {event.message}[/yellow]")
            elif event.type == "processing":
                progress.update(task_id, description=f"[bold cyan]{event.message}[/bold cyan]")
            elif event.type == "metadata":
                if show_metadata and event.data:
                    from .audio_metadata import format_audio_metadata_for_console
                    meta_dump = format_audio_metadata_for_console(event.data["meta"])
                    progress.console.print(Panel(meta_dump, title="Audio Metadata", border_style="dim"))
            elif event.type == "transcribed":
                progress.console.print("  [green]✓ Transcription complete[/green]")
            elif event.type == "analyzed":
                progress.console.print("  [green]✓ Analysis complete[/green]")
            elif event.type == "completed":
                note_path = event.data.get("note_path") if event.data else None
                msg = f"[bold green]✅ {event.message}[/bold green]"
                if note_path:
                    msg += f"\n  [dim]Note: {note_path}[/dim]"
                progress.console.print(msg)
            elif event.type == "error":
                progress.console.print(f"[bold red]❌ {event.message}[/bold red]")
                if event.data and event.data.get("saved_transcription"):
                    progress.console.print("  [dim]Transcription saved for retry.[/dim]")
            elif event.type == "summary":
                progress.update(task_id, completed=100)
                processed = event.data.get("processed", 0)
                skipped = event.data.get("skipped", 0)
                failed = event.data.get("failed", 0)
                
                table = Table(title="Processing Summary", show_header=True)
                table.add_column("Status", style="bold")
                table.add_column("Count")
                
                table.add_row("[green]Processed[/green]", str(processed))
                table.add_row("[yellow]Skipped[/yellow]", str(skipped))
                table.add_row("[red]Failed[/red]", str(failed))
                
                progress.console.print(table)


@main.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """Show simple status about pending and processed notes."""
    runtime = _runtime_or_exit(ctx)
    config = runtime.config

    pending = 0
    for entry in config.input_dir.iterdir():
        if entry.is_file() and entry.suffix.lower().lstrip(".") in config.processing.supported_formats:
            pending += 1

    notes_count = 0
    if config.output_dir.exists():
        for path in config.output_dir.rglob("*.md"):
            if path.is_file():
                notes_count += 1

    table = Table(title="Project Status", show_header=False, box=None)
    table.add_row("Pending audio files", f"[bold cyan]{pending}[/bold cyan]")
    table.add_row("Notes created", f"[bold green]{notes_count}[/bold green]")
    
    console.print(Panel(table, title="Voxnote Status", expand=False))
