"""
Push-to-talk capture from the robot microphone.

The UI holds a button: ``begin`` marks the session, the backend has the dog
say "I'm listening!", then ``open_mic`` starts collecting mic frames (only
after the dog has finished talking, so its own voice is not in the clip).
Releasing the button calls ``end``, which hands back everything heard.

State: idle -> prompting -> recording -> thinking -> idle. A release during
``prompting`` skips recording and ends with no audio.
"""

import threading
import time

import numpy as np

from backend.services.listener import to_mono16, to_wav


class PushToTalk:
    def __init__(self, robot) -> None:
        self.robot = robot
        self.state = "idle"  # idle | prompting | recording | thinking
        self._lock = threading.Lock()
        self._chunks: list[np.ndarray] = []
        self._rate = 48000
        self._recording_since: float | None = None
        self.last_heard: str | None = None
        # The most recent held recording as WAV, for playback in the UI
        self.last_clip: bytes | None = None
        self.last_clip_at: float | None = None

    def status(self) -> dict:
        seconds = None
        if self.state == "recording" and self._recording_since is not None:
            seconds = round(time.time() - self._recording_since, 1)
        return {
            "state": self.state,
            "seconds": seconds,
            "last_heard": self.last_heard,
            "clip_at": self.last_clip_at,
        }

    def begin(self) -> bool:
        """ Claim the session; False when one is already in progress """
        with self._lock:
            if self.state != "idle":
                return False
            self.state = "prompting"
            self._chunks = []
            self._recording_since = None
            return True

    def open_mic(self) -> bool:
        """ Start capturing; False when the button was released during the prompt """
        with self._lock:
            if self.state != "prompting":
                return False
            self.state = "recording"
            self._recording_since = time.time()
        self.robot.add_mic_callback(self._on_frame)
        return True

    def end(self) -> tuple[np.ndarray | None, int] | None:
        """
        Stop capturing and return (audio, rate), audio None when nothing was
        recorded. Returns None when there was no session to end, so a duplicate
        release (button up plus watchdog) is processed only once.
        """
        with self._lock:
            if self.state not in ("prompting", "recording"):
                return None
            was_recording = self.state == "recording"
            self.state = "thinking"
            chunks, self._chunks = self._chunks, []
            self._recording_since = None
        if was_recording:
            self.robot.remove_mic_callback(self._on_frame)
        audio = np.concatenate(chunks) if chunks else None
        if audio is not None:
            self.last_clip = to_wav(audio, self._rate)
            self.last_clip_at = time.time()
        return audio, self._rate

    def finish(self, heard: str | None = None) -> None:
        with self._lock:
            self.state = "idle"
            if heard is not None:
                self.last_heard = heard

    async def _on_frame(self, frame) -> None:
        if self.state != "recording":
            return
        mono, rate = to_mono16(frame)
        with self._lock:
            if self.state == "recording":
                self._rate = rate
                self._chunks.append(mono)
