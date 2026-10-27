from call_recorder_mcp.spans import Span, group_utterances


def test_joins_same_voice_across_short_pauses():
    spans = [Span(0.0, 2.0), Span(3.0, 5.0), Span(6.5, 8.0)]
    assert group_utterances(spans) == [Span(0.0, 8.0)]


def test_splits_on_long_pause():
    spans = [Span(0.0, 2.0), Span(5.0, 6.0)]
    assert group_utterances(spans) == [Span(0.0, 2.0), Span(5.0, 6.0)]


def test_never_mixes_two_voices():
    spans = [Span(0.0, 2.0, 0), Span(2.5, 4.0, 1), Span(4.2, 6.0, 1)]
    assert group_utterances(spans) == [Span(0.0, 2.0, 0), Span(2.5, 6.0, 1)]


def test_caps_utterance_length():
    spans = [Span(0.0, 20.0), Span(21.0, 35.0)]
    assert group_utterances(spans, max_length=28.0) == [Span(0.0, 20.0), Span(21.0, 35.0)]


def test_drops_tiny_utterances():
    assert group_utterances([Span(0.0, 0.1), Span(10.0, 12.0)]) == [Span(10.0, 12.0)]
