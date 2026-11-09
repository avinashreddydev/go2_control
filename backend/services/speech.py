"""
Make the robot speak: text-to-speech, then play through the Go2 megaphone.

The upload mirrors ``WebRTCAudioHub.upload_megaphone`` but is reimplemented
here because that module imports pydub, which no longer imports on Python 3.13
(``audioop`` was removed), and it prints every base64 chunk to stdout.
"""

import asyncio
import base64
import json
import logging
import re
import shutil
import subprocess
import tempfile
import wave
from io import BytesIO
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI, OpenAIError

from unitree_webrtc_connect.constants import AUDIO_API

load_dotenv(override=True)  # .env wins over a stale key exported in the shell
log = logging.getLogger("vla")

TTS_MODEL = "gpt-4o-mini-tts"
TTS_VOICE = "ash"
TTS_INSTRUCTIONS = "Speak like a cheerful, excited, friendly robot dog."
AUDIOHUB_TOPIC = "rt/api/audiohub/request"
# Base64 characters per datachannel message, as in the reference uploader
CHUNK_SIZE = 4096
CHUNK_GAP_SEC = 0.1
# Extra time after playback before leaving megaphone mode, so the tail is not cut
PLAYBACK_PAD_SEC = 0.5


# "*wags tail*" style stage directions in chat replies are acted, not read aloud
STAGE_DIRECTION = re.compile(r"\*[^*]*\*")


def speakable(text: str) -> str:
    """ The part of a reply worth saying: stage directions dropped, whitespace tidied """
    return re.sub(r"\s+", " ", STAGE_DIRECTION.sub(" ", text)).strip(" ,")


def _to_speaker_wav(source: str, data: bytes | None = None) -> bytes:
    """ Any audio ffmpeg reads -> 44.1 kHz mono 16-bit WAV, what the speaker accepts """
    # Written to a file, not a pipe: ffmpeg cannot seek back on a pipe to fill in
    # the RIFF sizes, and the header would claim hours of audio.
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "speaker.wav"
        subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-i", source,
             "-ar", "44100", "-ac", "1", "-sample_fmt", "s16", str(out)],
            input=data,
            capture_output=True,
            check=True,
        )
        return out.read_bytes()


def _say_fallback(text: str) -> bytes:
    """ Offline voice via macOS ``say``, used when the TTS API is unavailable """
    if shutil.which("say") is None:
        raise RuntimeError("No TTS available: the OpenAI call failed and `say` is missing")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "speech.aiff"
        subprocess.run(["say", "-o", str(path), text], check=True, capture_output=True)
        return _to_speaker_wav(str(path))


def synthesize(text: str, client: OpenAI | None = None) -> bytes:
    """ Text to speaker-ready WAV; OpenAI TTS first, macOS ``say`` if that fails """
    try:
        client = client or OpenAI()
        response = client.audio.speech.create(
            model=TTS_MODEL,
            voice=TTS_VOICE,
            input=text,
            instructions=TTS_INSTRUCTIONS,
            response_format="wav",
        )
    except OpenAIError:
        log.warning("OpenAI TTS failed; falling back to macOS say", exc_info=True)
        return _say_fallback(text)
    return _to_speaker_wav("pipe:0", response.content)


def to_speaker_wav(data: bytes) -> bytes:
    """ Any uploaded audio (wav, mp3, ogg, ...) to what the speaker plays """
    return _to_speaker_wav("pipe:0", data)


def wav_duration(wav_bytes: bytes) -> float:
    with wave.open(BytesIO(wav_bytes)) as handle:
        return handle.getnframes() / handle.getframerate()


def playback_seconds(wav_bytes: bytes) -> float:
    """ Upload time over the datachannel plus the audio itself """
    chunks = -(-len(base64.b64encode(wav_bytes)) // CHUNK_SIZE)
    return chunks * CHUNK_GAP_SEC + wav_duration(wav_bytes) + PLAYBACK_PAD_SEC


async def play_megaphone(pub_sub, wav_bytes: bytes) -> None:
    """ Stream a WAV to the robot speaker over the datachannel and wait it out """
    async def request(api: str, parameter: dict) -> None:
        await pub_sub.publish_request_new(
            AUDIOHUB_TOPIC,
            {"api_id": AUDIO_API[api], "parameter": json.dumps(parameter, ensure_ascii=True)},
        )

    encoded = base64.b64encode(wav_bytes).decode("ascii")
    chunks = [encoded[i:i + CHUNK_SIZE] for i in range(0, len(encoded), CHUNK_SIZE)]

    await request("ENTER_MEGAPHONE", {})
    try:
        for index, chunk in enumerate(chunks, 1):
            await request("UPLOAD_MEGAPHONE", {
                "current_block_size": len(chunk),
                "block_content": chunk,
                "current_block_index": index,
                "total_block_number": len(chunks),
            })
            await asyncio.sleep(CHUNK_GAP_SEC)
        await asyncio.sleep(wav_duration(wav_bytes) + PLAYBACK_PAD_SEC)
    finally:
        await request("EXIT_MEGAPHONE", {})
