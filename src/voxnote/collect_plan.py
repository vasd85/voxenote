from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Sequence

from .models import AppConfig

RecursiveReason = Literal["cli", "config", "default"]


@dataclass(frozen=True)
class CollectSourcePlan:
    source_dir: Path
    recursive: bool
    reason: RecursiveReason


def build_collect_source_plan(
    config: AppConfig,
    *,
    cli_sources: Sequence[Path],
    recursive_mode: Literal["auto", "on", "off"],
) -> list[CollectSourcePlan]:
    source_dirs = (
        [p.expanduser().resolve() for p in cli_sources]
        if cli_sources
        else [s.path.expanduser().resolve() for s in config.sources]
    )

    override: bool | None
    if recursive_mode == "on":
        override = True
    elif recursive_mode == "off":
        override = False
    else:
        override = None

    config_by_path = {s.path.expanduser().resolve(): s for s in config.sources}

    plan: list[CollectSourcePlan] = []
    for source_dir in source_dirs:
        cfg_src = config_by_path.get(source_dir)
        if override is not None:
            plan.append(CollectSourcePlan(source_dir=source_dir, recursive=override, reason="cli"))
        elif cfg_src is not None:
            plan.append(
                CollectSourcePlan(
                    source_dir=source_dir, recursive=bool(cfg_src.recursive), reason="config"
                )
            )
        else:
            plan.append(
                CollectSourcePlan(
                    source_dir=source_dir,
                    recursive=bool(config.collect.recursive_default),
                    reason="default",
                )
            )

    return plan
