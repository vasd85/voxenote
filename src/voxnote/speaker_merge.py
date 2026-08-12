"""Merge a word-level transcript with diarization turns.

Pure logic, no side effects: every word is assigned to the speaker turn it overlaps
most (WhisperX `assign_word_speakers` semantics), words without any overlap fall back
to the nearest turn, and consecutive words of one speaker are merged into blocks.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterator, List, Optional, Sequence

from .models import SpeakerBlock, SpeakerTurn, TranscriptSegment

SPEAKER_LABEL_PREFIX = "Speaker"

_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class _Token:
    text: str
    start: float
    end: float


def _iter_tokens(segments: Sequence[TranscriptSegment]) -> Iterator[_Token]:
    """Yield the units that get a speaker: words when available, else whole segments."""
    for segment in segments:
        words = [word for word in segment.words if word.text.strip()]
        if words:
            for word in words:
                yield _Token(text=word.text, start=word.start, end=word.end)
        elif segment.text.strip():
            yield _Token(text=segment.text, start=segment.start, end=segment.end)


def _overlap(start: float, end: float, other_start: float, other_end: float) -> float:
    return max(0.0, min(end, other_end) - max(start, other_start))


def _distance(start: float, end: float, other_start: float, other_end: float) -> float:
    return max(other_start - end, start - other_end, 0.0)


def _pick_turn(token: _Token, turns: Sequence[SpeakerTurn]) -> SpeakerTurn:
    best: Optional[SpeakerTurn] = None
    best_overlap = 0.0
    for turn in turns:
        overlap = _overlap(token.start, token.end, turn.start, turn.end)
        if overlap > best_overlap:
            best_overlap = overlap
            best = turn
    if best is not None:
        return best
    # No overlap at all (trim splice points, silence gaps): take the closest turn,
    # breaking ties by start time so the result is stable.
    return min(turns, key=lambda turn: (_distance(token.start, token.end, turn.start, turn.end), turn.start))


def join_token_texts(texts: Sequence[str]) -> str:
    """Join whisper word tokens by plain concatenation.

    Whisper tokens carry their own spacing, so concatenating them reproduces the original
    text. Inserting separators instead would break hyphenated words (`Что` + `-то`),
    attached punctuation (`так` + `,`), and languages written without spaces.
    """
    return _WHITESPACE_RE.sub(" ", "".join(texts)).strip()


def assign_speakers(
    segments: Sequence[TranscriptSegment],
    turns: Sequence[SpeakerTurn],
) -> List[SpeakerBlock]:
    """Split the transcript into speaker blocks labeled 1..N in order of first appearance."""
    if not turns:
        return []

    labels: dict[int, int] = {}
    blocks: List[SpeakerBlock] = []

    current_label: Optional[int] = None
    pending: List[str] = []
    block_start = 0.0
    block_end = 0.0

    def flush() -> None:
        if current_label is None:
            return
        text = join_token_texts(pending)
        if text:
            blocks.append(SpeakerBlock(speaker=current_label, text=text, start=block_start, end=block_end))

    for token in _iter_tokens(segments):
        raw_speaker = _pick_turn(token, turns).speaker
        if raw_speaker not in labels:
            labels[raw_speaker] = len(labels) + 1
        label = labels[raw_speaker]

        if label != current_label:
            flush()
            current_label = label
            pending = []
            block_start = token.start
            block_end = token.end

        pending.append(token.text)
        block_end = max(block_end, token.end)

    flush()
    return blocks


def count_speakers(blocks: Sequence[SpeakerBlock]) -> int:
    return len({block.speaker for block in blocks})


def render_labeled_transcript(blocks: Sequence[SpeakerBlock]) -> str:
    """Render blocks as `Speaker N: ...` paragraphs."""
    return "\n\n".join(f"{SPEAKER_LABEL_PREFIX} {block.speaker}: {block.text}" for block in blocks)
