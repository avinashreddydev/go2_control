"""
Records the robot camera to an MP4 for the duration of a VLA run.

The recorder polls ``robot.last_frame`` rather than draining ``frame_queue`` so
it never competes with the MJPEG stream for frames. It samples at a fixed rate
and re-writes the latest frame when the camera is slower, which keeps video
time aligned with wall-clock run time.
"""

import threading
import time
from pathlib import Path

import cv2

FPS = 15
# Codecs are tried in order; the first one OpenCV can actually open wins.
CODECS = ("avc1", "mp4v")

HUD_FONT = cv2.FONT_HERSHEY_SIMPLEX
HUD_SCALE = 0.5
HUD_COLOR = (255, 255, 255)
HUD_SHADOW = (0, 0, 0)


class RunRecorder:
    """ Writes an annotated MP4 of a single VLA run """

    def __init__(self, robot, path: Path, fps: int = FPS):
        self.robot = robot
        self.path = Path(path)
        self.fps = fps
        self.frames_written = 0

        self._writer: cv2.VideoWriter | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._started_at: float | None = None
        self._hud: list[str] = []

    # ----- Lifecycle -----
    def start(self) -> None:
        """ Begin capturing in a background thread """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._stop.clear()
        self._started_at = time.time()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """ Stop capturing and finalize the file """
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        self._thread = None
        if self._writer is not None:
            self._writer.release()
            self._writer = None

    def set_hud(self, lines: list[str]) -> None:
        """ Replace the overlay text burned into subsequent frames """
        with self._lock:
            self._hud = [line for line in lines if line]

    # ----- Internals -----
    def _open_writer(self, frame) -> cv2.VideoWriter | None:
        height, width = frame.shape[:2]
        for codec in CODECS:
            writer = cv2.VideoWriter(
                str(self.path), cv2.VideoWriter_fourcc(*codec), self.fps, (width, height)
            )
            if writer.isOpened():
                return writer
            writer.release()
        return None

    def _draw_hud(self, frame):
        with self._lock:
            lines = list(self._hud)
        if self._started_at is not None:
            lines.append(f"t+{time.time() - self._started_at:5.1f}s")

        annotated = frame.copy()
        for index, line in enumerate(lines):
            origin = (10, 22 + index * 20)
            cv2.putText(annotated, line, origin, HUD_FONT, HUD_SCALE, HUD_SHADOW, 3, cv2.LINE_AA)
            cv2.putText(annotated, line, origin, HUD_FONT, HUD_SCALE, HUD_COLOR, 1, cv2.LINE_AA)
        return annotated

    def _run(self) -> None:
        interval = 1.0 / self.fps
        next_frame_at = time.time()

        while not self._stop.is_set():
            frame = self.robot.last_frame
            if frame is None:
                time.sleep(interval)
                next_frame_at = time.time()
                continue

            if self._writer is None:
                self._writer = self._open_writer(frame)
                if self._writer is None:
                    return  # no usable codec; run continues without video

            self._writer.write(self._draw_hud(frame))
            self.frames_written += 1

            next_frame_at += interval
            time.sleep(max(0.0, next_frame_at - time.time()))
