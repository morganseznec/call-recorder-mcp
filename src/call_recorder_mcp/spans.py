"""Speech spans and how they are grouped into utterances."""

from typing import NamedTuple


class Span(NamedTuple):
    """A stretch of speech, in seconds. ``voice`` tells speakers apart (0 when unknown)."""

    start: float
    end: float
    voice: int = 0


def group_utterances(
    spans: list[Span], max_gap: float = 2.0, max_length: float = 28.0, min_length: float = 0.3
) -> list[Span]:
    """Joins spans of the same voice separated by short pauses.

    Each utterance is then transcribed on its own: Whisper reads 30 s windows, so
    fewer, longer utterances (up to ``max_length``) are faster, while splitting
    at every change of voice keeps one speaker per utterance.
    """
    groups: list[Span] = []
    for span in spans:
        last = groups[-1] if groups else None
        if (
            last is not None
            and last.voice == span.voice
            and span.start - last.end < max_gap
            and span.end - last.start <= max_length
        ):
            groups[-1] = last._replace(end=span.end)
        else:
            groups.append(span)
    return [group for group in groups if group.end - group.start >= min_length]
