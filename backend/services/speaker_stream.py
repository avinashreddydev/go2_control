"""
Live audio to the robot speaker over the WebRTC audio track.

The connection already negotiates a sendrecv audio transceiver
(``WebRTCAudioChannel``) but never sends on it. Attaching this track with
``sender.replaceTrack`` streams PCM in real time: no megaphone mode and no
base64 upload, which runs about three times slower than the audio itself.
"""

import asyncio
import fractions
import subprocess
import time

import numpy as np
from aiortc import MediaStreamTrack
from av import AudioFrame

SAMPLE_RATE = 48000                     # Opus native rate; no resampling in aiortc
FRAME_SAMPLES = SAMPLE_RATE // 50       # 20 ms frames
TIME_BASE = fractions.Fraction(1, SAMPLE_RATE)


def to_stream_pcm(data: bytes) -> np.ndarray:
    """ Any audio ffmpeg reads -> 48 kHz mono int16 samples """
    out = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-i", "pipe:0",
         "-f", "s16le", "-ar", str(SAMPLE_RATE), "-ac", "1", "pipe:1"],
        input=data,
        capture_output=True,
        check=True,
    ).stdout
    return np.frombuffer(out, dtype=np.int16)


class SpeakerTrack(MediaStreamTrack):
    """ Outgoing audio: queued clips when there are any, silence otherwise """

    kind = "audio"

    def __init__(self) -> None:
        super().__init__()
        self._pcm = np.zeros(0, dtype=np.int16)
        self._done: asyncio.Future | None = None
        self._start: float | None = None
        self._timestamp = 0

    def play(self, pcm: np.ndarray) -> asyncio.Future:
        """ Start a clip (call on the connection's loop); resolves when it has been sent """
        if self._done is not None and not self._done.done():
            self._done.cancel()
        self._pcm = pcm
        self._done = asyncio.get_running_loop().create_future()
        return self._done

    async def recv(self) -> AudioFrame:
        # Pace frames in real time, the way aiortc's AudioStreamTrack does.
        if self._start is None:
            self._start = time.time()
        else:
            self._timestamp += FRAME_SAMPLES
            wait = self._start + self._timestamp / SAMPLE_RATE - time.time()
            if wait > 0:
                await asyncio.sleep(wait)

        chunk = self._pcm[:FRAME_SAMPLES]
        self._pcm = self._pcm[FRAME_SAMPLES:]
        if len(chunk) < FRAME_SAMPLES:
            chunk = np.concatenate([chunk, np.zeros(FRAME_SAMPLES - len(chunk), dtype=np.int16)])
            if self._done is not None and not self._done.done():
                self._done.set_result(None)

        frame = AudioFrame.from_ndarray(chunk.reshape(1, -1), format="s16", layout="mono")
        frame.sample_rate = SAMPLE_RATE
        frame.pts = self._timestamp
        frame.time_base = TIME_BASE
        return frame


def attach(pc) -> SpeakerTrack:
    """ Put a SpeakerTrack on the connection's existing audio transceiver """
    transceiver = next(t for t in pc.getTransceivers() if t.kind == "audio")
    track = SpeakerTrack()
    transceiver.sender.replaceTrack(track)
    return track
