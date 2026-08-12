from __future__ import annotations

from voxnote.models import SpeakerTurn, TranscriptSegment, TranscriptWord
from voxnote.speaker_merge import (
    assign_speakers,
    count_speakers,
    join_token_texts,
    render_labeled_transcript,
)


def _segment(text: str, start: float, end: float, words: list[tuple[str, float, float]] | None = None):
    return TranscriptSegment(
        text=text,
        start=start,
        end=end,
        words=[TranscriptWord(text=w, start=s, end=e) for w, s, e in (words or [])],
    )


def test_words_go_to_the_turn_they_overlap_most() -> None:
    segments = [
        _segment(
            " hello there friend",
            0.0,
            3.0,
            [(" hello", 0.0, 0.9), (" there", 1.1, 1.9), (" friend", 2.1, 3.0)],
        )
    ]
    turns = [SpeakerTurn(start=0.0, end=2.0, speaker=0), SpeakerTurn(start=2.0, end=4.0, speaker=1)]

    blocks = assign_speakers(segments, turns)

    assert [(block.speaker, block.text) for block in blocks] == [
        (1, "hello there"),
        (2, "friend"),
    ]


def test_word_straddling_a_boundary_goes_to_the_turn_it_overlaps_most() -> None:
    # " two" spans the 2.0 boundary: 0.2 s in the first turn, 0.4 s in the second one.
    segments = [_segment(" one two", 0.0, 3.0, [(" one", 0.0, 1.0), (" two", 1.8, 2.4)])]
    turns = [SpeakerTurn(start=0.0, end=2.0, speaker=0), SpeakerTurn(start=2.0, end=4.0, speaker=1)]

    blocks = assign_speakers(segments, turns)

    assert [(block.speaker, block.text) for block in blocks] == [(1, "one"), (2, "two")]


def test_word_straddling_a_boundary_stays_with_the_larger_earlier_overlap() -> None:
    # Mirror image of the case above: 0.4 s in the first turn against 0.2 s in the second one,
    # so picking the first turn with *any* overlap and picking the largest one differ again.
    segments = [_segment(" one two", 0.0, 3.0, [(" one", 0.0, 1.0), (" two", 1.6, 2.2)])]
    turns = [SpeakerTurn(start=0.0, end=2.0, speaker=0), SpeakerTurn(start=2.0, end=4.0, speaker=1)]

    blocks = assign_speakers(segments, turns)

    assert [(block.speaker, block.text) for block in blocks] == [(1, "one two")]


def test_long_word_goes_to_its_max_overlap_turn_not_the_one_holding_its_midpoint() -> None:
    # " Hello" spans a brief interjection: 0.9 s inside the first turn against 0.1 s inside the
    # second, while its midpoint (1.0) sits in the second one. Adjacent turns cannot tell the two
    # rules apart, so the interjection is what pins "greatest overlap" over "midpoint containment".
    segments = [_segment(" Hello there", 0.0, 2.5, [(" Hello", 0.0, 2.0), (" there", 2.0, 2.5)])]
    turns = [SpeakerTurn(start=0.0, end=0.9, speaker=0), SpeakerTurn(start=0.95, end=1.05, speaker=1)]

    blocks = assign_speakers(segments, turns)

    assert [(block.speaker, block.text) for block in blocks] == [(1, "Hello"), (2, "there")]


def test_equal_overlap_is_broken_deterministically_by_turn_order() -> None:
    # 0.5 s in each turn: the tie must resolve to the first turn, not to the last one seen.
    segments = [_segment(" one two", 0.0, 3.0, [(" one", 0.0, 1.0), (" two", 1.5, 2.5)])]
    turns = [SpeakerTurn(start=0.0, end=2.0, speaker=0), SpeakerTurn(start=2.0, end=4.0, speaker=1)]

    blocks = assign_speakers(segments, turns)

    assert [(block.speaker, block.text) for block in blocks] == [(1, "one two")]


def test_nearest_turn_ties_are_broken_by_start_time() -> None:
    # " gap" sits in silence exactly 0.5 s from both turns; the earlier turn wins the tie.
    # The turns are deliberately out of start order, so list position cannot decide it.
    segments = [_segment(" first gap", 0.0, 3.0, [(" first", 0.2, 0.8), (" gap", 1.5, 2.5)])]
    turns = [SpeakerTurn(start=3.0, end=4.0, speaker=6), SpeakerTurn(start=0.0, end=1.0, speaker=5)]

    blocks = assign_speakers(segments, turns)

    assert [(block.speaker, block.text) for block in blocks] == [(1, "first gap")]


def test_word_without_overlap_falls_back_to_nearest_turn() -> None:
    # The word sits in a gap between turns, closer to the second one.
    segments = [_segment(" word", 0.0, 5.0, [(" word", 4.0, 4.2)])]
    turns = [SpeakerTurn(start=0.0, end=1.0, speaker=0), SpeakerTurn(start=4.5, end=6.0, speaker=1)]

    blocks = assign_speakers(segments, turns)

    assert [(block.speaker, block.text) for block in blocks] == [(1, "word")]
    # Speaker 1 here is the engine's cluster 1: it is the first (and only) one to appear.


def test_labels_are_renumbered_from_non_contiguous_engine_ids() -> None:
    segments = [
        _segment(" a b", 0.0, 2.0, [(" a", 0.0, 0.5), (" b", 1.2, 1.8)]),
        _segment(" c", 2.0, 3.0, [(" c", 2.1, 2.9)]),
    ]
    turns = [
        SpeakerTurn(start=0.0, end=1.0, speaker=3),
        SpeakerTurn(start=1.0, end=2.0, speaker=0),
        SpeakerTurn(start=2.0, end=3.0, speaker=3),
    ]

    blocks = assign_speakers(segments, turns)

    assert [(block.speaker, block.text) for block in blocks] == [(1, "a"), (2, "b"), (1, "c")]
    assert count_speakers(blocks) == 2


def test_consecutive_same_speaker_words_merge_into_one_block() -> None:
    segments = [
        _segment(" one", 0.0, 1.0, [(" one", 0.0, 1.0)]),
        _segment(" two", 1.0, 2.0, [(" two", 1.0, 2.0)]),
    ]
    turns = [SpeakerTurn(start=0.0, end=5.0, speaker=7)]

    blocks = assign_speakers(segments, turns)

    assert len(blocks) == 1
    assert blocks[0].speaker == 1
    assert blocks[0].text == "one two"
    assert (blocks[0].start, blocks[0].end) == (0.0, 2.0)


def test_segment_without_words_is_assigned_as_a_whole() -> None:
    segments = [_segment("no word timings here", 0.0, 1.0)]
    turns = [SpeakerTurn(start=0.0, end=1.0, speaker=2)]

    blocks = assign_speakers(segments, turns)

    assert [(block.speaker, block.text) for block in blocks] == [(1, "no word timings here")]


def test_no_turns_yields_no_blocks() -> None:
    segments = [_segment(" hi", 0.0, 1.0, [(" hi", 0.0, 1.0)])]

    assert assign_speakers(segments, []) == []
    assert count_speakers([]) == 0


def test_blank_words_are_ignored() -> None:
    segments = [_segment(" hi", 0.0, 1.0, [("   ", 0.0, 0.1), (" hi", 0.2, 1.0)])]
    turns = [SpeakerTurn(start=0.0, end=1.0, speaker=0)]

    blocks = assign_speakers(segments, turns)

    assert [(block.speaker, block.text) for block in blocks] == [(1, "hi")]


def test_join_token_texts_preserves_whisper_spacing() -> None:
    assert join_token_texts([" Hello", " world."]) == "Hello world."
    assert join_token_texts([" Hello", "  ", " world."]) == "Hello world."
    assert join_token_texts([]) == ""


def test_join_token_texts_does_not_split_words_or_punctuation() -> None:
    # mlx-whisper emits these continuation tokens without a leading space.
    assert join_token_texts(["Что", "-то", " пошло", " не", " так", ","]) == "Что-то пошло не так,"
    assert join_token_texts(["A", " well", "-known", " bug", ";", " 50", "%"]) == "A well-known bug; 50%"
    # Languages written without spaces must not gain any.
    assert join_token_texts(["今", "天", "天", "气"]) == "今天天气"


def test_render_labeled_transcript_uses_blank_line_separated_blocks() -> None:
    segments = [
        _segment(" a", 0.0, 1.0, [(" a", 0.0, 1.0)]),
        _segment(" b", 1.0, 2.0, [(" b", 1.0, 2.0)]),
    ]
    turns = [SpeakerTurn(start=0.0, end=1.0, speaker=0), SpeakerTurn(start=1.0, end=2.0, speaker=1)]

    rendered = render_labeled_transcript(assign_speakers(segments, turns))

    assert rendered == "Speaker 1: a\n\nSpeaker 2: b"
