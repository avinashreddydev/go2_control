"""
Semantic world model: YOLO detections placed as tracked objects in 3D.

A background loop (a few Hz) runs the detector on the latest camera frame and
turns every box into a position in the odometry (world) frame:

- direction: the box's pixel columns through a pinhole model of the front
  camera, rotated by the robot's heading;
- distance: the lidar voxels that fall inside the box (seen from the camera),
  nearest quarter, i.e. the object's front surface. With no lidar support, the
  box's bottom edge on a flat floor, from the camera height (ground plane).

Detections are then tracked in world coordinates: matched to the nearest
existing object of the same label, smoothed, given a stable id, and forgotten
after FORGET_SEC unseen. The 3D view draws what ``snapshot`` returns.

Camera numbers are best guesses for the Go2 front camera; set CAMERA_HFOV_DEG
and CAMERA_PITCH_DEG in .env once calibrated.
"""

import logging
import math
import os
import threading
import time
from dataclasses import dataclass, field

import numpy as np

log = logging.getLogger("vla")

PERIOD_SEC = 0.25
# Front camera (URDF front_camera_joint, base frame) and its optics.
CAMERA_FORWARD_M = 0.327
CAMERA_UP_M = 0.043
CAMERA_HFOV_DEG = float(os.getenv("CAMERA_HFOV_DEG", "120"))
CAMERA_PITCH_DEG = float(os.getenv("CAMERA_PITCH_DEG", "0"))  # + = tilted down
STAND_HEIGHT_M = 0.32  # base above the floor when standing
LIDAR_STALE_SEC = 2.0

MIN_RANGE_M = 0.25
MAX_RANGE_M = 12.0
# Use the middle of a box (trims background spilling in at its edges)
BOX_CORE = 0.6
MIN_LIDAR_HITS = 3
# Lidar sizing: voxels this far behind the front surface still belong to the
# object; enough of them, and their 3D spread gives its real width and height.
OBJECT_DEPTH_M = 0.8
MIN_SIZE_HITS = 6
MAX_OBJECT_HEIGHT_M = 2.5
# Lidar points are voxel centres: a measured span is one voxel short.
VOXEL_M = 0.05

# Tracking
MATCH_M = 0.8
SMOOTHING = 0.4  # weight of a new measurement
FORGET_SEC = 2.0
MIN_HITS = 2  # seen this many times before it is shown

# Surfaces and scene words, not objects. The prompt-free model names them too.
IGNORE = {
    "floor", "wall", "ceiling", "room", "ground", "indoor", "interior", "building",
    "floor mat", "carpet", "rug", "tile", "wood floor", "hardwood", "office", "hallway",
    "corridor", "living room", "bedroom", "kitchen", "classroom", "window", "light",
    "lamp light", "shadow", "sky", "darkness", "night", "blur",
}
# A box this much of the frame is background, not an object.
MAX_FRAME_FRACTION = 0.6


@dataclass
class Track:
    id: int
    label: str
    x: float
    y: float
    width: float
    height: float
    distance: float
    confidence: float
    source: str  # "lidar" | "ground": how the distance was measured
    size_source: str = "camera"  # "lidar" | "camera": how the size was measured
    hits: int = 1
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)


class SemanticWorld:
    def __init__(self, robot, detector) -> None:
        self.robot = robot
        self.detector = detector
        self.enabled = True
        self._tracks: list[Track] = []
        self._next_id = 1
        self._lock = threading.Lock()
        self.last_run_ms: float | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    @property
    def available(self) -> bool:
        return bool(getattr(self.detector, "available", False))

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def snapshot(self) -> list[dict]:
        """ Objects confirmed enough to draw, in the world (odometry) frame """
        with self._lock:
            return [
                {
                    "id": t.id,
                    "label": t.label,
                    "x": round(t.x, 3),
                    "y": round(t.y, 3),
                    "width": round(t.width, 2),
                    "height": round(t.height, 2),
                    "distance": round(t.distance, 2),
                    "confidence": round(t.confidence, 2),
                    "source": t.source,
                    "size_source": t.size_source,
                    "age": round(time.time() - t.last_seen, 2),
                }
                for t in self._tracks
                if t.hits >= MIN_HITS
            ]

    # ----- Loop -----
    def _loop(self) -> None:
        while not self._stop.wait(PERIOD_SEC):
            if not (self.enabled and self.available):
                continue
            try:
                self._step()
            except Exception:
                log.exception("Semantic world step failed")

    def _step(self) -> None:
        frame = self.robot.last_frame
        pose = self.robot.robot_pose
        if frame is None or pose is None:
            self._expire(time.time())
            return

        started = time.time()
        detections = self.detector.boxes(frame)
        self.last_run_ms = round((time.time() - started) * 1000, 1)

        placed = [p for p in (self._place(d, frame.shape, pose) for d in detections) if p]
        self._update_tracks(placed, time.time())

    # ----- Geometry -----
    def _place(self, det: dict, shape, pose: dict) -> dict | None:
        """ One detection box -> world position, size, and how it was measured """
        label = det["label"]
        if label.lower() in IGNORE:
            return None
        height_px, width_px = shape[:2]
        x1, y1, x2, y2 = det["box"]
        if (x2 - x1) * (y2 - y1) > MAX_FRAME_FRACTION * width_px * height_px:
            return None

        # Pinhole camera: focal length from the horizontal field of view.
        fx = (width_px / 2) / math.tan(math.radians(CAMERA_HFOV_DEG) / 2)
        cx, cy = width_px / 2, height_px / 2
        pitch = math.radians(CAMERA_PITCH_DEG)

        def bearing(u: float) -> float:  # left of the optical axis is positive
            return -math.atan((u - cx) / fx)

        def elevation(v: float) -> float:  # above the horizon is positive
            return -math.atan((v - cy) / fx) - pitch

        trim = (1 - BOX_CORE) / 2 * (x2 - x1)
        b_left, b_right = bearing(x1 + trim), bearing(x2 - trim)
        b_center = bearing((x1 + x2) / 2)
        e_top, e_bottom = elevation(y1), elevation(y2)

        px, py, pz = (pose["position"][k] for k in ("x", "y", "z"))
        yaw = self.robot._quat_yaw(pose["orientation"])
        cam_x = px + CAMERA_FORWARD_M * math.cos(yaw)
        cam_y = py + CAMERA_FORWARD_M * math.sin(yaw)
        cam_z = pz + CAMERA_UP_M

        measured = self._lidar_measure(
            cam_x, cam_y, cam_z, yaw, pz,
            core=(b_right, b_left), full=(bearing(x2), bearing(x1)),
            center=b_center, elevation=(e_bottom, e_top),
        )
        if measured is not None:
            distance, lidar_size = measured
            source = "lidar"
        else:
            distance, lidar_size = self._ground_distance(e_bottom, pz), None
            source = "ground"
        if distance is None:
            return None

        # Size: from the lidar points' real 3D extent when there are enough
        # of them (independent of the camera calibration, and not fooled by a
        # box cut off at the frame edge); else from the box's angular size.
        heading = yaw + b_center
        x = cam_x + distance * math.cos(heading)
        y = cam_y + distance * math.sin(heading)
        if lidar_size is not None:
            width, height, (x, y) = lidar_size
            size_source = "lidar"
        else:
            width = distance * abs(bearing(x1) - bearing(x2))
            height = distance * abs(e_top - e_bottom)
            size_source = "camera"

        return {
            "label": label,
            "confidence": det["confidence"],
            "x": x,
            "y": y,
            "width": max(0.05, width),
            "height": max(0.05, height),
            "distance": distance,
            "source": source,
            "size_source": size_source,
        }

    def _lidar_measure(self, cam_x, cam_y, cam_z, yaw, base_z, *, core, full, center, elevation):
        """
        Distance to the object, plus its real (width, height) when the lidar
        sees enough of it. None when the lidar has nothing inside the box.

        Distance: near quartile of the voxels in the box's core (its front
        surface). Size: voxels anywhere across the box, from that surface to
        OBJECT_DEPTH_M behind it, at any height above the floor (so an object
        taller than the frame is still measured whole).
        """
        pts = getattr(self.robot, "lidar_points", None)
        at = getattr(self.robot, "lidar_at", None)
        if pts is None or at is None or time.time() - at > LIDAR_STALE_SEC or not len(pts):
            return None

        dx = pts[:, 0] - cam_x
        dy = pts[:, 1] - cam_y
        rng = np.hypot(dx, dy)
        bearing = np.arctan2(dy, dx) - yaw
        bearing = np.arctan2(np.sin(bearing), np.cos(bearing))  # wrap to [-pi, pi]
        elev = np.arctan2(pts[:, 2] - cam_z, rng)
        in_range = (rng > MIN_RANGE_M) & (rng < MAX_RANGE_M)

        e_min, e_max = elevation
        in_core = (
            in_range
            & (bearing >= core[0]) & (bearing <= core[1])
            & (elev >= e_min) & (elev <= e_max)
        )
        if int(in_core.sum()) < MIN_LIDAR_HITS:
            return None
        distance = float(np.percentile(rng[in_core], 25))

        floor = (base_z if base_z > 0.1 else STAND_HEIGHT_M) - STAND_HEIGHT_M
        above = pts[:, 2] - floor
        body = (
            in_range
            & (bearing >= full[0]) & (bearing <= full[1])
            & (rng >= distance - 0.15) & (rng <= distance + OBJECT_DEPTH_M)
            & (above > 0.05) & (above < MAX_OBJECT_HEIGHT_M)
        )
        if int(body.sum()) < MIN_SIZE_HITS:
            return distance, None

        # Width: spread across the line of sight. Height: top of the object
        # above the floor (objects stand on it). Percentiles shrug off strays;
        # a voxel is added back since the points are voxel centres.
        lateral = rng[body] * np.sin(bearing[body] - center)
        width = float(np.percentile(lateral, 95) - np.percentile(lateral, 5)) + VOXEL_M
        height = float(np.percentile(above[body], 95)) + VOXEL_M / 2
        # Where the object actually is: the middle of its points, not its front.
        middle = (float(np.median(pts[body, 0])), float(np.median(pts[body, 1])))
        return distance, (width, height, middle)

    @staticmethod
    def _ground_distance(e_bottom: float, base_z: float) -> float | None:
        """ Where the box's bottom edge meets a flat floor """
        camera_height = (base_z if base_z > 0.1 else STAND_HEIGHT_M) + CAMERA_UP_M
        down = -e_bottom
        if down < math.radians(1):
            return None  # at or above the horizon: no floor contact in view
        distance = camera_height / math.tan(down)
        return distance if MIN_RANGE_M <= distance <= MAX_RANGE_M else None

    # ----- Tracking -----
    def _update_tracks(self, placed: list[dict], now: float) -> None:
        with self._lock:
            unmatched = list(self._tracks)
            # Closest pairs first, so two chairs don't swap identities.
            pairs = sorted(
                (
                    (math.hypot(t.x - p["x"], t.y - p["y"]), i, t)
                    for i, p in enumerate(placed)
                    for t in self._tracks
                    if t.label == p["label"]
                ),
                key=lambda pair: pair[0],
            )
            used: set[int] = set()
            for dist, i, track in pairs:
                if dist > MATCH_M or i in used or track not in unmatched:
                    continue
                self._merge(track, placed[i], now)
                used.add(i)
                unmatched.remove(track)

            for i, p in enumerate(placed):
                if i not in used:
                    self._tracks.append(Track(
                        id=self._next_id, label=p["label"], x=p["x"], y=p["y"],
                        width=p["width"], height=p["height"], distance=p["distance"],
                        confidence=p["confidence"], source=p["source"],
                        size_source=p["size_source"],
                        first_seen=now, last_seen=now,
                    ))
                    self._next_id += 1
        self._expire(now)

    @staticmethod
    def _merge(track: Track, p: dict, now: float) -> None:
        a = SMOOTHING
        for key in ("x", "y", "width", "height", "distance", "confidence"):
            setattr(track, key, (1 - a) * getattr(track, key) + a * p[key])
        track.source = p["source"]
        track.size_source = p["size_source"]
        track.hits += 1
        track.last_seen = now

    def _expire(self, now: float) -> None:
        with self._lock:
            self._tracks = [t for t in self._tracks if now - t.last_seen <= FORGET_SEC]
