"""Who speaks when: sherpa-onnx (pyannote segmentation + CAM++ voice embeddings).

The number of speakers is always given. Counting them automatically was
unreliable on real calls: one person's short replies ("ok", "uh-huh") came out
as several voices, and similar voices merged.
"""

import wave
from pathlib import Path

from . import config
from .spans import Span


def available() -> bool:
    return config.SEGMENTATION_MODEL.exists() and config.EMBEDDING_MODEL.exists()


def speaker_spans(pcm: Path, speakers: int) -> list[Span]:
    """Speech spans labelled with a voice id (0 to speakers - 1)."""
    import numpy as np
    import sherpa_onnx

    diarization = sherpa_onnx.OfflineSpeakerDiarization(
        sherpa_onnx.OfflineSpeakerDiarizationConfig(
            segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
                pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                    model=str(config.SEGMENTATION_MODEL)
                ),
                num_threads=4,
            ),
            embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(config.EMBEDDING_MODEL), num_threads=4),
            clustering=sherpa_onnx.FastClusteringConfig(num_clusters=speakers),
            min_duration_on=0.3,
            min_duration_off=0.5,
        )
    )
    with wave.open(str(pcm)) as source:
        samples = np.frombuffer(source.readframes(source.getnframes()), dtype=np.int16).astype(np.float32) / 32768

    spans: list[Span] = []
    for segment in sorted(diarization.process(samples).sort_by_start_time(), key=lambda s: s.start):
        # Overlapping speech: keep the speaker who started first, so no audio is transcribed twice.
        start = max(segment.start, spans[-1].end) if spans else segment.start
        if segment.end - start >= 0.3:
            spans.append(Span(start, segment.end, segment.speaker))
    return spans
