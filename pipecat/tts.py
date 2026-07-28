"""
Text-to-Speech using edge-tts (Microsoft Edge, free, high quality).
Outputs MP3 audio bytes.
"""
import asyncio
import logging
import edge_tts

logger = logging.getLogger("tts")

# Russian voices:
#   ru-RU-DmitryNeural    — male
#   ru-RU-SvetlanaNeural  — female
DEFAULT_VOICE = "ru-RU-SvetlanaNeural"


async def synthesize_text(text: str, voice: str = DEFAULT_VOICE) -> bytes:
    """
    Convert text to speech audio (MP3).
    Returns audio bytes.
    """
    try:
        communicate = edge_tts.Communicate(text, voice)
        audio_chunks = []
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_chunks.append(chunk["data"])

        audio = b"".join(audio_chunks)
        if audio:
            logger.info("TTS: %d bytes for '%s...'", len(audio), text[:50])
        return audio

    except Exception as e:
        logger.error("TTS error: %s", e)
        return b""


def synthesize_text_sync(text: str, voice: str = DEFAULT_VOICE) -> bytes:
    """Synchronous wrapper for synthesize_text."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Already in async context, use a new loop in a thread
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, synthesize_text(text, voice))
                return future.result(timeout=30)
        else:
            return loop.run_until_complete(synthesize_text(text, voice))
    except RuntimeError:
        return asyncio.run(synthesize_text(text, voice))


async def list_voices(language: str = "ru") -> list[dict]:
    """List available voices for a language."""
    voices = await edge_tts.list_voices()
    return [v for v in voices if v["Locale"].startswith(language)]
