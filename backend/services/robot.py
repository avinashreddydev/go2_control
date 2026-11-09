"""
Robot client: WebRTC connection, camera frames, teleop, and controller I/O.
"""

import asyncio
import contextlib
import math
import threading
import time
from collections import deque
from queue import Queue

import numpy as np
from aiortc import MediaStreamTrack
from unitree_webrtc_connect.webrtc_driver import UnitreeWebRTCConnection
from unitree_webrtc_connect.constants import RTC_TOPIC

from backend.services.speaker_stream import SAMPLE_RATE, attach as attach_speaker
from backend.services.speech import play_megaphone

import logging
logging.basicConfig(level=logging.FATAL)

# Abort stalled WebRTC handshakes so the UI can show failed + retry
CONNECT_TIMEOUT_SEC = 20.0
# After the speaker stops: network delay plus room echo, so the mic does not hear itself
SPEAKING_TAIL_SEC = 0.7
# Seconds to hold a single keypress burst before sending a zero command
MOVE_DURATION = 0.4
# Publish interval for continuous teleop (50 Hz)
MOVE_INTERVAL = 0.02
LIDAR_RANGE_M = 5.0
LIDAR_BINS = 180
LIDAR_MIN_RANGE_M = 0.25

MOVE_VEL = 0.8
ROTATE_VEL = 0.6
STRAFE_VEL = 0.9


def build_action_velocities(move_vel: float, rotate_vel: float, strafe_vel: float) -> dict[str, tuple[float, float, float]]:
    return {
        "move_forward":  (0.0,  move_vel,  0.0),
        "move_backward": (0.0, -move_vel,  0.0),
        "turn_left":     (0.0,  0.0, -rotate_vel),
        "turn_right":    (0.0,  0.0,  rotate_vel),
        "strafe_left":   (-strafe_vel, 0.0, 0.0),
        "strafe_right":  ( strafe_vel, 0.0, 0.0),
    }


ACTION_VELOCITIES = build_action_velocities(MOVE_VEL, ROTATE_VEL, STRAFE_VEL)

TELEOP_KEYS = {
    "w": "move_forward",
    "s": "move_backward",
    "a": "turn_left",
    "d": "turn_right",
    "j": "strafe_left",
    "l": "strafe_right",
}


class RobotClient:
    def __init__(self, conn: UnitreeWebRTCConnection):
        self.conn = conn
        self.frame_queue = Queue()
        self.last_frame = None  # latest frame for vision / VLA
        self.last_frame_at: float | None = None
        self.stream_encode_times: deque[float] = deque(maxlen=120)
        self.last_stream_captured_at: float | None = None

        # Pub/sub handle for RTC datachannel topics
        self.subscriber = None
        self.speaker_track = None
        self.audio_on = False
        self._speaking = 0
        self._mic_callbacks: list = []
        self._speaking_until = 0.0
        self.battery_soc: int | None = None
        self.battery_at: float | None = None
        self.lidar_rays: list[dict[str, float]] = []
        self.lidar_at: float | None = None
        self.robot_pose: dict | None = None
        self._keys_lock = threading.Lock()
        self._pressed_keys: set[str] = set()

        # Continuous velocity override (VLA); takes priority over teleop keys
        self._override_velocity: tuple[float, float, float] | None = None

        self.connect_status = "connecting"
        self.connected_at: float | None = None
        self._teleop_task: asyncio.Task | None = None

        self.move_vel = MOVE_VEL
        self.rotate_vel = ROTATE_VEL
        self.strafe_vel = STRAFE_VEL
        self.action_velocities = build_action_velocities(MOVE_VEL, ROTATE_VEL, STRAFE_VEL)

        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.run_loop, args=(self.loop,))

    def start(self) -> None:
        """ Start the background thread and asyncio loop """
        self.thread.start()

    def stop(self) -> None:
        """ Release teleop keys, stop the event loop, and join the worker thread """
        self.clear_teleop_keys()
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join()

    async def conn_setup(self) -> None:
        """ Connect to the robot, enable video, and start the teleop publish loop """
        self.connect_status = "connecting"
        try:
            await asyncio.wait_for(self.conn.connect(), timeout=CONNECT_TIMEOUT_SEC)
        except Exception:
            self.connect_status = "failed"
            try:
                await self.conn.disconnect()
            except Exception:
                pass
            raise

        self.connect_status = "connected"
        self.connected_at = time.time()

        self.conn.video.switchVideoChannel(True)
        self.conn.video.add_track_callback(self.receive_video_frame)

        self.subscriber = self.conn.datachannel.pub_sub
        self.subscriber.subscribe(RTC_TOPIC["LOW_STATE"], self._on_low_state)
        self._attach_mic_callbacks()
        await self._setup_lidar()
        self._teleop_task = asyncio.create_task(self._teleop_loop())

    async def _connect(self) -> None:
        try:
            await self.conn_setup()
        except Exception:
            pass

    def run_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        asyncio.set_event_loop(loop)
        loop.create_task(self._connect())
        loop.run_forever()

    def reconnect(self) -> None:
        self.connect_status = "connecting"
        asyncio.run_coroutine_threadsafe(self._reconnect(), self.loop)

    async def _reconnect(self) -> None:
        if self._teleop_task is not None:
            self._teleop_task.cancel()
            self._teleop_task = None
        self.subscriber = None
        self.speaker_track = None  # the new peer connection needs its own
        self.audio_on = False
        try:
            await self.conn.disconnect()
        except Exception:
            pass
        await self._connect()

    # ----- Lidar -----
    async def _setup_lidar(self) -> None:
        """ Enable lidar streaming and subscribe to the compressed voxel map """
        self.conn.datachannel.set_decoder("native")
        await self.conn.datachannel.disableTrafficSaving(True)
        self.subscriber.publish_without_callback(RTC_TOPIC["ULIDAR_SWITCH"], "on")
        self.subscriber.subscribe(RTC_TOPIC["ROBOTODOM"], self._on_robot_pose)
        self.subscriber.subscribe(RTC_TOPIC["ULIDAR_ARRAY"], self._on_lidar)

    def _on_robot_pose(self, msg: dict) -> None:
        pose = (msg.get("data") or {}).get("pose")
        if pose:
            self.robot_pose = pose

    @staticmethod
    def _quat_yaw(q: dict) -> float:
        qx, qy, qz, qw = q["x"], q["y"], q["z"], q["w"]
        return math.atan2(2 * (qw * qz + qx * qy), 1 - 2 * (qy * qy + qz * qz))

    def pose_2d(self) -> dict[str, float] | None:
        """ Current world-frame (x, y, yaw) from odometry, or None if unavailable """
        pose = self.robot_pose
        if not pose:
            return None
        return {
            "x": pose["position"]["x"],
            "y": pose["position"]["y"],
            "yaw": self._quat_yaw(pose["orientation"]),
        }

    def _extract_lidar_points(self, outer: dict, decoded: dict) -> np.ndarray | None:
        """ Decode voxel map into world-frame XYZ points """
        if "points" in decoded:
            pts = np.asarray(decoded["points"], dtype=np.float64)
            return pts if len(pts) else None

        face_count = int(decoded.get("face_count", 0))
        positions = decoded.get("positions")
        if face_count == 0 or positions is None:
            return None

        res = float(outer.get("resolution", 0.05))
        origin = np.array(outer.get("origin", [0, 0, 0]), dtype=np.float64)
        pos_buf = np.asarray(positions, dtype=np.uint8)
        pts = np.frombuffer(pos_buf[: face_count * 12].tobytes(), dtype=np.float32).reshape(-1, 3)
        pts = pts * res + origin

        uvs = decoded.get("uvs")
        if uvs is not None:
            uv_buf = np.asarray(uvs, dtype=np.uint8)
            n = min(len(pts), len(uv_buf) // 8)
            if n > 0:
                uv_array = np.frombuffer(uv_buf[: n * 8].tobytes(), dtype=np.float32).reshape(-1, 2)
                pts = pts[:n][np.min(uv_array[:n], axis=1) > 0.1]

        return pts if len(pts) else None

    def _world_to_robot_xy(self, pts: np.ndarray) -> tuple[np.ndarray, np.ndarray] | None:
        """ Transform world-frame points into the robot's 2D body frame """
        if self.robot_pose is None:
            return None

        pose = self.robot_pose
        px = pose["position"]["x"]
        py = pose["position"]["y"]
        pz = pose["position"]["z"]
        yaw = self._quat_yaw(pose["orientation"])

        dx = pts[:, 0] - px
        dy = pts[:, 1] - py
        dz = pts[:, 2] - pz
        mask = (dz > -0.3) & (dz < 1.5)
        dx, dy = dx[mask], dy[mask]
        if len(dx) == 0:
            return None

        cos_y, sin_y = math.cos(-yaw), math.sin(-yaw)
        fwd = dx * cos_y - dy * sin_y
        left = dx * sin_y + dy * cos_y

        return -left, fwd

    def _points_to_rays(self, x: np.ndarray, y: np.ndarray) -> list[dict[str, float]]:
        r = np.hypot(x, y)
        mask = (r >= LIDAR_MIN_RANGE_M) & (r <= LIDAR_RANGE_M)
        x, y, r = x[mask], y[mask], r[mask]
        if len(r) == 0:
            return []

        angles = np.arctan2(x, y)
        bins = (np.floor((angles + math.pi) / (2 * math.pi) * LIDAR_BINS).astype(int)) % LIDAR_BINS

        best_r = np.full(LIDAR_BINS, LIDAR_RANGE_M + 1.0)
        best_x = np.zeros(LIDAR_BINS)
        best_y = np.zeros(LIDAR_BINS)
        for i in range(len(r)):
            b = bins[i]
            if r[i] < best_r[b]:
                best_r[b] = r[i]
                best_x[b] = x[i]
                best_y[b] = y[i]

        return [
            {"x": float(best_x[i]), "y": float(best_y[i])}
            for i in range(LIDAR_BINS)
            if best_r[i] <= LIDAR_RANGE_M
        ]

    def _on_lidar(self, msg: dict) -> None:
        outer = msg.get("data") or {}
        decoded = outer.get("data") or {}
        pts = self._extract_lidar_points(outer, decoded)
        if pts is None:
            return

        robot_xy = self._world_to_robot_xy(pts)
        if robot_xy is None:
            return

        x, y = robot_xy
        self.lidar_rays = self._points_to_rays(x, y)
        self.lidar_at = time.time()

    # ----- Settings -----
    def get_velocities(self) -> dict[str, float]:
        return {
            "move": self.move_vel,
            "rotate": self.rotate_vel,
            "strafe": self.strafe_vel,
        }

    def set_velocities(
        self,
        move: float | None = None,
        rotate: float | None = None,
        strafe: float | None = None,
    ) -> dict[str, float]:
        if move is not None:
            self.move_vel = move
        if rotate is not None:
            self.rotate_vel = rotate
        if strafe is not None:
            self.strafe_vel = strafe
        self.action_velocities = build_action_velocities(
            self.move_vel, self.rotate_vel, self.strafe_vel
        )
        return self.get_velocities()

    # ----- Low state -----
    def _on_low_state(self, msg: dict) -> None:
        data = msg.get("data") or {}
        bms = data.get("bms_state") or {}
        soc = bms.get("soc")
        if isinstance(soc, (int, float)) and 0 <= soc <= 100:
            self.battery_soc = int(soc)
            self.battery_at = time.time()

    # ----- Vision -----
    async def receive_video_frame(self, track: MediaStreamTrack) -> None:
        """ Ingest WebRTC video frames into the queue and last_frame snapshot    """
        while True:
            frame = await track.recv()

            # Convert frame to numpy array
            img = frame.to_ndarray(format="bgr24")
            self.frame_queue.put(img)
            self.last_frame = img
            self.last_frame_at = time.time()

    # ----- Teleop -----
    def set_teleop_key(self, key: str, pressed: bool) -> None:
        """ Track a keyboard key as pressed or released for continuous teleop """
        if key not in TELEOP_KEYS:
            return
        with self._keys_lock:
            if pressed:
                self._pressed_keys.add(key)
            else:
                self._pressed_keys.discard(key)

    def clear_teleop_keys(self) -> None:
        with self._keys_lock:
            self._pressed_keys.clear()
        self.publish_wireless_controller()

    def _get_velocity(self) -> tuple[float, float, float]:
        lx = ly = rx = 0.0
        with self._keys_lock:
            keys = set(self._pressed_keys)
        for key in keys:
            klx, kly, krx = self.action_velocities[TELEOP_KEYS[key]]
            lx += klx
            ly += kly
            rx += krx
        return lx, ly, rx

    async def _teleop_loop(self) -> None:
        """ Publish joystick commands at MOVE_INTERVAL until the client stops """
        while True:
            if self._override_velocity is not None:
                lx, ly, rx = self._override_velocity
                self.publish_wireless_controller(lx=lx, ly=ly, rx=rx)
            else:
                lx, ly, rx = self._get_velocity()
                if lx or ly or rx:
                    self.publish_wireless_controller(lx=lx, ly=ly, rx=rx)
                else:
                    self.publish_wireless_controller()
            await asyncio.sleep(MOVE_INTERVAL)

    # ----- Controller -----
    def set_velocity_command(self, velocity: tuple[float, float, float] | None) -> None:
        """
        Drive continuously at a raw (lx, ly, rx) velocity until cleared.

        The teleop loop publishes it at 50 Hz instead of the keyboard state. Pass None to release control.
        """
        self._override_velocity = velocity

    def publish_wireless_controller(
        self, lx: float = 0.0, ly: float = 0.0, rx: float = 0.0, ry: float = 0.0, keys: int = 0
    ) -> None:
        """Publish a WIRELESS_CONTROLLER datachannel message (virtual gamepad).

        Args:
            lx: Lateral stick (-1 left .. 1 right).
            ly: Forward stick (-1 back .. 1 forward).
            rx: Yaw stick (-1 left .. 1 right).
            ry: Secondary vertical axis (unused by teleop; defaults to 0).
            keys: Bitmask of virtual button flags (unused by teleop; defaults to 0).
        """
        if self.subscriber is None:
            return
        if threading.current_thread() is not self.thread:
            self.loop.call_soon_threadsafe(
                lambda lx=lx, ly=ly, rx=rx, ry=ry, keys=keys: self._publish_wireless_controller(
                    lx, ly, rx, ry, keys
                )
            )
            return
        self._publish_wireless_controller(lx, ly, rx, ry, keys)

    def _publish_wireless_controller(
        self, lx: float, ly: float, rx: float, ry: float, keys: int
    ) -> None:
        self.subscriber.publish_without_callback(
            RTC_TOPIC["WIRELESS_CONTROLLER"],
            {"lx": lx, "ly": ly, "rx": rx, "ry": ry, "keys": keys},
        )

    async def _drive_burst(self, lx: float, ly: float, rx: float) -> None:
        """ Hold fixed joystick values for MOVE_DURATION, then send zero """
        t = 0.0
        while t < MOVE_DURATION:
            self.publish_wireless_controller(lx=lx, ly=ly, rx=rx)
            await asyncio.sleep(MOVE_INTERVAL)
            t += MOVE_INTERVAL
        self.publish_wireless_controller()

    def speak(self, wav_bytes: bytes, timeout: float = 60.0) -> None:
        """ Play a WAV through the robot speaker; blocks until playback ends """
        if self.subscriber is None:
            raise RuntimeError("Robot is not connected")
        with self._talking():
            future = asyncio.run_coroutine_threadsafe(
                play_megaphone(self.subscriber, wav_bytes), self.loop
            )
            future.result(timeout=timeout)

    def _ensure_audio(self) -> None:
        # Switched on at first use, so a session that never talks or listens costs nothing.
        if not self.audio_on:
            self.conn.audio.switchAudioChannel(True)
            self.audio_on = True

    async def _stream_audio(self, pcm) -> None:
        self._ensure_audio()
        if self.speaker_track is None:
            self.speaker_track = attach_speaker(self.conn.pc)
        await self.speaker_track.play(pcm)

    async def _record_audio(self, seconds: float) -> tuple[bytes, int, int]:
        self._ensure_audio()
        frames = []

        async def collect(frame) -> None:
            frames.append(frame)

        self.conn.audio.add_track_callback(collect)
        try:
            await asyncio.sleep(seconds)
        finally:
            self.conn.audio.track_callbacks.remove(collect)
        if not frames:
            return b"", 48000, 1
        first = frames[0]
        channels = len(first.layout.channels)
        # Packed s16 frames come back as one row of interleaved samples.
        pcm = b"".join(f.to_ndarray().astype("int16").tobytes() for f in frames)
        return pcm, first.sample_rate, channels

    def record_audio(self, seconds: float) -> tuple[bytes, int, int]:
        """ Record the robot microphone; returns (s16 interleaved PCM, sample rate, channels) """
        if self.subscriber is None:
            raise RuntimeError("Robot is not connected")
        future = asyncio.run_coroutine_threadsafe(self._record_audio(seconds), self.loop)
        return future.result(timeout=seconds + 10)

    def stream_audio(self, pcm) -> None:
        """ Play 48 kHz mono int16 samples live over the WebRTC audio track; blocks until sent """
        if self.subscriber is None:
            raise RuntimeError("Robot is not connected")
        with self._talking():
            future = asyncio.run_coroutine_threadsafe(self._stream_audio(pcm), self.loop)
            future.result(timeout=len(pcm) / SAMPLE_RATE + 10)

    @contextlib.contextmanager
    def _talking(self):
        """ Mark the speaker busy, plus a tail for network delay and room echo """
        self._speaking += 1
        try:
            yield
        finally:
            self._speaking -= 1
            self._speaking_until = time.time() + SPEAKING_TAIL_SEC

    @property
    def speaking(self) -> bool:
        return self._speaking > 0 or time.time() < self._speaking_until

    def add_mic_callback(self, callback) -> None:
        """ Feed robot microphone frames to an async callback on the robot loop """
        def add() -> None:
            if callback not in self._mic_callbacks:
                self._mic_callbacks.append(callback)
            if self.subscriber is not None:
                self._attach_mic_callbacks()
        self.loop.call_soon_threadsafe(add)

    def _attach_mic_callbacks(self) -> None:
        # Also run after a reconnect: the new connection has a fresh audio channel.
        if not self._mic_callbacks:
            return
        self._ensure_audio()
        for callback in self._mic_callbacks:
            if callback not in self.conn.audio.track_callbacks:
                self.conn.audio.track_callbacks.append(callback)

    def remove_mic_callback(self, callback) -> None:
        def remove() -> None:
            if callback in self._mic_callbacks:
                self._mic_callbacks.remove(callback)
            if callback in self.conn.audio.track_callbacks:
                self.conn.audio.track_callbacks.remove(callback)
        self.loop.call_soon_threadsafe(remove)

    def controller(self, key: str) -> None:
        """ Fire a short movement burst for a teleop key """
        lx, ly, rx = self.action_velocities[TELEOP_KEYS[key]]
        asyncio.run_coroutine_threadsafe(
            self._drive_burst(lx, ly, rx),
            self.loop,
        )
