from __future__ import annotations

from pathlib import Path
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


class PathsConfig(BaseModel):
    input: Path
    output: Path
    archive: Path


class TranscriptionConfig(BaseModel):
    model: str = Field(..., description="Whisper model identifier")
    language: Literal["auto", "en", "ru"] = "auto"
    whisper_timeout_s: float = Field(default=3600, ge=1, description="Timeout for mlx_whisper subprocess (seconds)")


class LLMConfig(BaseModel):
    model: str = Field(..., description="Ollama model name")
    base_url: str = "http://localhost:11434"
    debug: bool = False
    stream: bool = True
    chat_timeout_s: float = Field(default=120, ge=1, description="HTTP read timeout for Ollama /api/chat")
    tokenize_timeout_s: float = Field(default=60, ge=1, description="HTTP read timeout for Ollama /api/tokenize")
    max_retries: int = Field(default=2, ge=0, le=10, description="Retries for transient Ollama HTTP failures")
    retry_backoff_s: float = Field(default=2.0, ge=0.0, description="Base backoff (seconds) between Ollama retries")


class ProcessingConfig(BaseModel):
    supported_formats: List[str] = Field(default_factory=lambda: ["m4a", "mp3", "wav", "ogg", "flac"])
    ffmpeg_prepare_timeout_s: float = Field(default=3600, ge=1, description="Timeout for ffmpeg audio preparation subprocess (seconds)")
    ffmpeg_trim_timeout_s: float = Field(default=3600, ge=1, description="Timeout for ffmpeg audio trimming subprocess (seconds)")

    @field_validator("supported_formats", mode="before")
    @classmethod
    def _normalize_ext(cls, v: list[str] | str) -> list[str]:
        if isinstance(v, list):
            return [str(item).lower().lstrip(".") for item in v]
        if isinstance(v, str):
            return [v.lower().lstrip(".")]
        return v


class VADConfig(BaseModel):
    threshold: float = Field(default=0.5, description="Speech detection threshold (0.0-1.0)")
    neg_threshold: float = Field(default=0.35, description="Non-speech detection threshold (0.0-1.0)")
    min_silence_duration_ms: int = Field(default=500, description="Minimum silence duration to split segments (ms)")
    min_speech_duration_ms: int = Field(default=250, description="Minimum speech duration to keep segment (ms)")
    speech_pad_ms: int = Field(default=100, description="Padding around speech segments (ms)")


class AudioSourceConfig(BaseModel):
    path: Path
    recursive: bool = False


class CollectConfig(BaseModel):
    recursive_default: bool = Field(
        default=True,
        description="Default recursion for collect sources not present in config.yaml sources list",
    )


class PromptsConfig(BaseModel):
    system_prompt: str = Field(..., description="System prompt for the LLM analysis")


class AppConfig(BaseModel):
    paths: PathsConfig
    transcription: TranscriptionConfig
    llm: LLMConfig
    processing: ProcessingConfig = Field(default_factory=ProcessingConfig)
    vad: VADConfig = Field(default_factory=VADConfig)
    collect: CollectConfig = Field(default_factory=CollectConfig)
    sources: List[AudioSourceConfig] = Field(default_factory=list)
    prompts: PromptsConfig

    @property
    def input_dir(self) -> Path:
        return self.paths.input

    @property
    def output_dir(self) -> Path:
        return self.paths.output

    @property
    def archive_dir(self) -> Path:
        return self.paths.archive


class TranscriptionResult(BaseModel):
    audio_path: Path
    text: str


class NoteAnalysis(BaseModel):
    title: str
    category: str
    short_summary: Optional[str] = None


class NotePaths(BaseModel):
    note_path: Path
    audio_archive_path: Path


class NoteContext(BaseModel):
    id: str
    transcription: TranscriptionResult
    analysis: NoteAnalysis
    paths: NotePaths
