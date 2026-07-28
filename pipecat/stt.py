"""
Speech-to-Text using faster-whisper (local, free).
"""
import io
import logging
import numpy as np
from faster_whisper import WhisperModel

logger = logging.getLogger("stt")

# Load model once (small model = fast, good enough for phone audio)
_model = None

def _get_model():
    global _model
    if _model is None:
        logger.info("Loading Whisper model (small)...")
        _model = WhisperModel("small", device="cpu", compute_type="int8")
        logger.info("Whisper model loaded")
    return _model


def transcribe_audio(audio_bytes: bytes, language: str = "ru") -> str:
    """
    Transcribe audio bytes to text.
    Accepts raw PCM 16-bit 16kHz mono audio.
    Returns transcribed text.
    """
    try:
        model = _get_model()

        # Convert bytes to numpy array (16-bit PCM)
        audio_np = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0

        segments, info = model.transcribe(
            audio_np,
            language=language,
            beam_size=5,
            vad_filter=True,
        )

        text = " ".join(segment.text for segment in segments).strip()
        if text:
            logger.info("STT: %s", text[:100])
        return text

    except Exception as e:
        logger.error("STT error: %s", e)
        return ""


def transcribe_file(file_path: str, language: str = "ru") -> str:
    """Transcribe an audio file to text."""
    try:
        model = _get_model()
        segments, info = model.transcribe(file_path, language=language, beam_size=5, vad_filter=True)
        text = " ".join(segment.text for segment in segments).strip()
        return text
    except Exception as e:
        logger.error("STT file error: %s", e)
        return ""
