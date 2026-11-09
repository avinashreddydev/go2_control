"""
Hear "Hey Boxy ..." through the robot microphone.

Robot mic frames (48 kHz stereo, 20 ms) arrive on the connection's event loop.
They are downmixed to 16 kHz mono and run through WebRTC VAD in 30 ms windows.
A run of speech followed by a pause becomes one utterance, which a worker
thread transcribes. Utterances that start with the wake phrase are handed to
``on_command`` (the backend acts on them and answers out loud) and also queued
for the chat page to pick up; everything else is dropped.

While the robot is talking, frames are ignored so it does not answer its own
voice. It keeps listening while it moves, so "Hey Boxy, stop" always works.
"""

import io
import logging
import queue
import re
import threading
import time
import wave
from collections import deque
from typing import Callable

import numpy as np
import webrtcvad
from dotenv import load_dotenv
from openai import OpenAI, OpenAIError

load_dotenv(override=True)  # .env wins over a stale key exported in the shell
log = logging.getLogger("vla")

TRANSCRIBE_MODEL = "gpt-4o-mini-transcribe"
# Biases the transcript toward spelling the wake phrase the way we match it
TRANSCRIBE_PROMPT = "Hey Boxy, come here. Hey Boxy, stop. Hey Boxy, what do you see?"

VAD_RATE = 16000
VAD_FRAME = VAD_RATE * 30 // 1000         # 30 ms windows, a size webrtcvad accepts
VAD_AGGRESSIVENESS = 2                    # 0 lenient .. 3 strict about what counts as speech
# The Go2 mic carries a steady ~215 Hz harmonic hum (fan/electronics) that VAD
# alone calls speech 100% of the time, so a window must also be this many times
# louder than the tracked background level.
LOUDNESS_RATIO = 1.6
FLOOR_FALL = 0.05                         # per window: floor drops quickly to quieter rooms
FLOOR_RISE = 0.003                        # and creeps up slowly (~10 s) when the room gets louder
START_WINDOW = 10                         # 300 ms: speech starts when most of it is voiced
START_VOICED = 7
END_SILENCE_SEC = 0.8                     # pause that ends an utterance
PRE_ROLL_SEC = 0.3                        # keep the start of the first word
MIN_UTTERANCE_SEC = 0.5
MAX_UTTERANCE_SEC = 12.0
FOLLOW_UP_SEC = 10.0                      # after a bare "Hey Boxy", the next line needs no wake word
MAX_PENDING = 5

# "Hey Boxy", "hey, boxie", "OK Boxey", "hi bocksy" ... at the start of the line
WAKE = re.compile(
    r"^\W*(?:hey|hi|hello|okay|ok|yo)\W+(?:boxy|boxie|boxey|boxi|bocksy|boxee)\b\W*",
    re.IGNORECASE,
)


def strip_wake(text: str) -> str | None:
    """ The command after the wake phrase, "" for a bare wake, None without it """
    match = WAKE.match(text)
    if match is None:
        return None
    return text[match.end():].strip()


def to_wav(pcm: np.ndarray, rate: int) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(pcm.astype(np.int16).tobytes())
    return buffer.getvalue()


def transcribe(pcm: np.ndarray, rate: int, client: OpenAI | None = None) -> str:
    """ Speech to text for mono int16 audio; "" when nothing intelligible was said """
    client = client or OpenAI()
    result = client.audio.transcriptions.create(
        model=TRANSCRIBE_MODEL,
        file=("speech.wav", to_wav(pcm, rate), "audio/wav"),
        prompt=TRANSCRIBE_PROMPT,
        language="en",
    )
    return result.text.strip()


def to_mono16(frame) -> tuple[np.ndarray, int]:
    """ One robot mic frame (packed, interleaved int16) as mono samples plus its rate """
    samples = frame.to_ndarray().astype(np.int32).reshape(-1)
    channels = len(frame.layout.channels)
    mono = samples.reshape(-1, channels).mean(axis=1) if channels > 1 else samples
    return mono.astype(np.int16), frame.sample_rate or 48000


class VoiceListener:
    """ Wake-word listening on the robot mic; the chat page polls ``status`` """

    def __init__(
        self,
        robot,
        can_listen: Callable[[], bool],
        on_command: Callable[[str], None] | None = None,
    ) -> None:
        self.robot = robot
        self.can_listen = can_listen
        # Called on the transcription thread with the text after the wake
        # phrase ("" for a bare "Hey Boxy"); must return quickly.
        self.on_command = on_command
        self.enabled = False
        self._vad = webrtcvad.Vad(VAD_AGGRESSIVENESS)
        self._pending16 = np.zeros(0, dtype=np.int16)
        self._recent: deque[bool] = deque(maxlen=START_WINDOW)
        self._pre_roll: deque[np.ndarray] = deque(maxlen=int(PRE_ROLL_SEC * 1000 / 30))
        self._speech: list[np.ndarray] | None = None
        self._silence_frames = 0
        self._floor: float | None = None
        self.level = 0.0                   # last window loudness / background, for the meter
        self._lock = threading.Lock()
        self._utterances: list[dict] = []
        self._next_id = 1
        self._follow_up_until = 0.0
        self.state = "off"                 # off | waiting | muted | hearing | transcribing
        self.last_ignored: str | None = None
        self._jobs: queue.Queue[np.ndarray] = queue.Queue(maxsize=4)
        self._worker = threading.Thread(target=self._transcribe_loop, daemon=True)
        self._worker.start()

    # ----- Control -----
    def set_enabled(self, enabled: bool) -> None:
        if enabled == self.enabled:
            return
        if enabled:
            self.robot.add_mic_callback(self._on_frame)
        else:
            self.robot.remove_mic_callback(self._on_frame)
            self._reset()
        self.enabled = enabled
        self.state = "waiting" if enabled else "off"

    def status(self, take: bool = True) -> dict:
        """ Current state, plus the heard commands not yet handed out (cleared when taken) """
        with self._lock:
            heard = self._utterances
            if take:
                self._utterances = []
        return {
            "enabled": self.enabled,
            "state": self.state,
            "follow_up": time.time() < self._follow_up_until,
            "last_ignored": self.last_ignored,
            "level": round(self.level, 2),
            "heard": heard,
        }

    # ----- Audio path (runs on the robot's event loop) -----
    def _reset(self) -> None:
        self._pending16 = np.zeros(0, dtype=np.int16)
        self._recent.clear()
        self._pre_roll.clear()
        self._speech = None
        self._silence_frames = 0

    async def _on_frame(self, frame) -> None:
        if not self.enabled:
            return
        if not self.can_listen():
            if self._speech is not None or self._pending16.size:
                self._reset()
            if self.state in ("waiting", "hearing"):
                self.state = "muted"
            return
        if self.state == "muted":
            self.state = "waiting"

        mono, rate = to_mono16(frame)
        mono = mono.astype(np.int32)
        factor = rate // VAD_RATE
        if factor > 1:
            usable = len(mono) - len(mono) % factor
            # Averaging each group of samples doubles as a crude low-pass before decimating
            mono = mono[:usable].reshape(-1, factor).mean(axis=1)
        self._pending16 = np.concatenate([self._pending16, mono.astype(np.int16)])

        while len(self._pending16) >= VAD_FRAME:
            window = self._pending16[:VAD_FRAME]
            self._pending16 = self._pending16[VAD_FRAME:]
            self._step(window)

    def _step(self, window: np.ndarray) -> None:
        rms = float(np.sqrt(np.mean(window.astype(np.float64) ** 2))) + 1.0
        if self._floor is None:
            self._floor = rms
        self.level = rms / self._floor
        loud = self.level >= LOUDNESS_RATIO
        if self._speech is None or not loud:
            # Track the background outside speech; never let a voice drag it up fast
            rate = FLOOR_FALL if rms < self._floor else FLOOR_RISE
            self._floor += (rms - self._floor) * rate
        voiced = loud and self._vad.is_speech(window.tobytes(), VAD_RATE)

        if self._speech is None:
            self._pre_roll.append(window)
            self._recent.append(voiced)
            if sum(self._recent) >= START_VOICED:
                self._speech = list(self._pre_roll)
                self._silence_frames = 0
                self.state = "hearing"
            return

        self._speech.append(window)
        self._silence_frames = 0 if voiced else self._silence_frames + 1
        duration = len(self._speech) * VAD_FRAME / VAD_RATE
        ended = self._silence_frames * VAD_FRAME / VAD_RATE >= END_SILENCE_SEC
        if ended or duration >= MAX_UTTERANCE_SEC:
            clip = np.concatenate(self._speech)
            self._speech = None
            self._recent.clear()
            self._pre_roll.clear()
            self.state = "waiting"
            if duration >= MIN_UTTERANCE_SEC:
                try:
                    self._jobs.put_nowait(clip)
                except queue.Full:
                    log.warning("Voice: transcription backlog full, dropping an utterance")

    # ----- Transcription (worker thread) -----
    def _transcribe_loop(self) -> None:
        client: OpenAI | None = None
        while True:
            clip = self._jobs.get()
            if not self.enabled:
                continue
            self.state = "transcribing"
            try:
                client = client or OpenAI()
                self._handle(transcribe(clip, VAD_RATE, client))
            except OpenAIError:
                log.exception("Voice transcription failed")
            finally:
                if self.state == "transcribing":
                    self.state = "waiting" if self.enabled else "off"

    def _handle(self, text: str) -> None:
        if not text:
            return
        command = strip_wake(text)
        now = time.time()
        if command is None:
            if now < self._follow_up_until:
                command = text
            else:
                self.last_ignored = text
                return
        # A bare "Hey Boxy" opens a short window for the actual request
        self._follow_up_until = now + FOLLOW_UP_SEC if command == "" else 0.0
        with self._lock:
            self._utterances.append({"id": self._next_id, "text": command, "raw": text, "at": now})
            self._next_id += 1
            del self._utterances[:-MAX_PENDING]
        if self.on_command is not None:
            try:
                self.on_command(command)
            except Exception:
                log.exception("Voice: command handler failed")

    def extend_follow_up(self) -> None:
        """ Reopen the no-wake-word window, e.g. after answering a bare "Hey Boxy" """
        self._follow_up_until = time.time() + FOLLOW_UP_SEC
