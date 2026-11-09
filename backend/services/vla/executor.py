"""
Carries out one System 2 instruction on the live robot with the System 1 loop.

System 2 (the chat model) calls the ``navigate`` tool with an instruction and a
visible completion condition. ``VLARunner.start`` begins a run; the worker
thread then repeats: grab the current frame, ask System 1 for ``done`` or a
small burst of F/B/L/R tokens, drive the burst, repeat. The run ends when
System 1 reports done, a limit is hit, System 1 keeps failing, or the operator
stops it. ``outcome`` is a one-line summary written for System 2 to read.

A lidar reflex below the model refuses or cuts short forward motion when
something is close in front. The whole run is recorded to an MP4 and a JSON
trace beside it.
"""

import json
import math
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .actions import (
    DEGREES_PER_UNIT,
    METERS_PER_UNIT,
    UNIT_DURATION_SEC,
    Move,
    is_end,
    parse_move,
    velocity_for,
)
from .llm import models
from .recorder import RunRecorder
from .system1 import act

RECORDINGS_DIR = Path(__file__).resolve().parents[3] / "recordings"

MAX_STEPS = 40
MAX_RUN_SEC = 180.0
# System 1 holding still this many frames in a row is treated as giving up
MAX_HOLDS = 4
SYSTEM1_MAX_FAILURES = 3
# How long to wait for the first camera frame before giving up
FRAME_WAIT_SEC = 5.0
# Pause between tokens so the body settles before the next command
SETTLE_SEC = 0.1
# Pause after a burst so the frame the model sees next is not motion-blurred
OBSERVE_SETTLE_SEC = 0.25
# How often a held move checks for stop and obstacles
DRIVE_POLL_SEC = 0.05
HISTORY_LIMIT = 6
# Stop() returns after this long even if the loop is parked in an LLM call
STOP_JOIN_SEC = 2.0

# Lidar reflex: no forward motion when something is this close ahead
OBSTACLE_STOP_M = 0.45
OBSTACLE_HALF_ANGLE = math.radians(25)
LIDAR_STALE_SEC = 2.0


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:40] or "run"


@dataclass
class RunContext:
    """
    Everything scoped to a single run.

    Held by the worker thread so that a run which is stopped while waiting on
    the model can unwind without touching the state of a newer run.
    """

    run_id: str
    task: str
    done_when: str
    recorder: RunRecorder
    # Set for a scripted run: these tokens are driven as-is with no model call.
    tokens: list[str] | None = None
    stop: threading.Event = field(default_factory=threading.Event)
    ended: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)
    steps: list[dict] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    ended_at: float | None = None
    status: str = "running"  # running | done | stopped | failed
    error: str | None = None
    # One line for System 2: how the instruction ended and what was last seen
    outcome: str | None = None
    current_action: str | None = None
    # Set once the recorder has flushed and closed the file; the file exists on
    # disk well before that, so existence alone is not a safe readiness signal.
    video_ready: bool = False

    def sleep(self, seconds: float) -> bool:
        """ Interruptible sleep; False when the run was stopped mid-wait """
        return not self.stop.wait(seconds)


class VLARunner:
    """ Owns one run at a time and exposes a snapshot for the UI and System 2 """

    def __init__(self, robot, vision=None, recordings_dir: Path = RECORDINGS_DIR):
        self.robot = robot
        self.vision = vision
        self.recordings_dir = Path(recordings_dir)

        self._ctx: RunContext | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    # ----- Lifecycle -----
    def start(self, task: str, done_when: str = "", tokens: list[str] | None = None) -> str:
        """
        Stop any previous run and begin a new one; returns the run id.

        ``task`` is System 2's instruction and ``done_when`` the visible
        condition System 1 checks every frame. Given ``tokens`` instead, the
        run skips the model and drives exactly those moves, recorded and
        stoppable like any other run.
        """
        self.stop(reason="Replaced by a new instruction")

        if tokens is not None:
            tokens = [t.strip().upper() for t in tokens if parse_move(t) is not None]

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        run_id = f"{timestamp}-{_slugify(task)}"
        recorder = RunRecorder(self.robot, self.video_path(run_id))
        ctx = RunContext(
            run_id=run_id,
            task=task,
            done_when=done_when.strip(),
            recorder=recorder,
            tokens=tokens,
        )

        recorder.set_hud([f"S2: {task[:60]}", "S1 step 0 | starting"])
        recorder.start()

        with self._lock:
            self._ctx = ctx
        self._thread = threading.Thread(target=self._run, args=(ctx,), daemon=True)
        self._thread.start()
        return run_id

    def stop(self, reason: str = "Stopped by the operator") -> None:
        """ Interrupt the loop and halt the robot immediately """
        with self._lock:
            ctx = self._ctx
        if ctx is None:
            return

        if ctx.status == "running":
            ctx.outcome = ctx.outcome or reason
        ctx.stop.set()
        self._halt()
        # A short join is enough for a loop that is driving or sleeping. A loop
        # parked in an LLM call finishes it and then unwinds on its own; its
        # stop flag keeps it from issuing any further movement.
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=STOP_JOIN_SEC)
        if ctx.status == "running":
            ctx.status = "stopped"
            ctx.ended_at = time.time()

    def wait(self, run_id: str, timeout: float | None = None) -> dict:
        """ Block until the given run ends (or the timeout passes); its snapshot """
        with self._lock:
            ctx = self._ctx
        if ctx is not None and ctx.run_id == run_id:
            ctx.ended.wait(timeout)
        return self.snapshot()

    def video_path(self, run_id: str) -> Path:
        return self.recordings_dir / f"{run_id}.mp4"

    def log_path(self, run_id: str) -> Path:
        return self.recordings_dir / f"{run_id}.json"

    def _write_log(self, ctx: RunContext) -> None:
        """
        Persist the full inference trace beside the video.

        Rewritten after every step rather than once at the end, so a crashed or
        killed run still leaves everything it had decided up to that point.
        """
        with ctx.lock:
            payload = {
                "run_id": ctx.run_id,
                "task": ctx.task,
                "done_when": ctx.done_when,
                "architecture": "chat-system2 / gpt-system1",
                "models": models(),
                "status": ctx.status,
                "outcome": ctx.outcome,
                "error": ctx.error,
                "started_at": ctx.started_at,
                "ended_at": ctx.ended_at,
                "elapsed": round((ctx.ended_at or time.time()) - ctx.started_at, 2),
                "calibration": {
                    "meters_per_unit": METERS_PER_UNIT,
                    "degrees_per_unit": DEGREES_PER_UNIT,
                    "unit_duration_sec": UNIT_DURATION_SEC,
                },
                "video": f"{ctx.run_id}.mp4",
                "steps": [dict(step) for step in ctx.steps],
            }
        try:
            path = self.log_path(ctx.run_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            with tmp.open("w") as handle:
                json.dump(payload, handle, indent=2)
            tmp.replace(path)  # atomic, so a reader never sees half a file
        except OSError:
            pass  # a logging failure must not abort the run

    def drive_tokens(self, tokens: list[str]) -> list[dict]:
        """
        Execute raw action tokens without the model.

        Used to calibrate what a magnitude actually covers on the floor, and to
        smoke-test the motion path before spending an inference. Blocks for the
        length of the burst.
        """
        if self.busy:
            raise RuntimeError("A VLA run is in progress")

        executed = []
        for token in tokens:
            if is_end(token):
                break
            move = parse_move(token)
            if move is None:
                continue

            before = self._pose()
            velocity = velocity_for(move.letter, self.robot.move_vel, self.robot.rotate_vel)
            self.robot.set_velocity_command(velocity)
            try:
                time.sleep(move.duration)
            finally:
                self.robot.set_velocity_command(None)
            time.sleep(SETTLE_SEC)  # let odometry catch up before measuring

            executed.append({
                "token": move.token,
                "duration": round(move.duration, 2),
                **self._displacement(before, self._pose()),
            })
        return executed

    def _pose(self) -> tuple[float, float, float] | None:
        """ Current (x, y, yaw) from robot odometry, or None when unavailable """
        pose = getattr(self.robot, "robot_pose", None)
        if not pose:
            return None
        position, orientation = pose["position"], pose["orientation"]
        qx, qy, qz, qw = orientation["x"], orientation["y"], orientation["z"], orientation["w"]
        yaw = math.atan2(2 * (qw * qz + qx * qy), 1 - 2 * (qy * qy + qz * qz))
        return position["x"], position["y"], yaw

    @staticmethod
    def _displacement(before, after) -> dict:
        """ Measured translation and rotation between two odometry samples """
        if before is None or after is None:
            return {"distance_m": None, "turn_deg": None}
        turn = math.degrees(math.atan2(math.sin(after[2] - before[2]), math.cos(after[2] - before[2])))
        return {
            "distance_m": round(math.hypot(after[0] - before[0], after[1] - before[1]), 3),
            "turn_deg": round(turn, 1),
        }

    @property
    def status(self) -> str:
        ctx = self._ctx
        return ctx.status if ctx else "idle"

    @property
    def busy(self) -> bool:
        """ True while a run holds the robot """
        return self.status == "running"

    def snapshot(self) -> dict:
        """ Serializable state for the UI and for System 2's tool result """
        ctx = self._ctx
        if ctx is None:
            return {
                "status": "idle",
                "run_id": None,
                "task": "",
                "done_when": "",
                "models": models(),
                "steps": [],
                "step_count": 0,
                "current_action": None,
                "outcome": None,
                "error": None,
                "video_ready": False,
                "video_url": None,
                "elapsed": None,
            }

        with ctx.lock:
            steps = [dict(step) for step in ctx.steps]

        video_ready = ctx.video_ready and self.video_path(ctx.run_id).exists()
        return {
            "status": ctx.status,
            "run_id": ctx.run_id,
            "task": ctx.task,
            "done_when": ctx.done_when,
            "models": models(),
            "steps": steps,
            "step_count": len(steps),
            "current_action": ctx.current_action,
            "outcome": ctx.outcome,
            "error": ctx.error,
            "video_ready": video_ready,
            "video_url": f"/vla/recording/{ctx.run_id}" if video_ready else None,
            "log_url": f"/vla/log/{ctx.run_id}" if self.log_path(ctx.run_id).exists() else None,
            "elapsed": round((ctx.ended_at or time.time()) - ctx.started_at, 1),
        }

    # ----- Loop -----
    def _run(self, ctx: RunContext) -> None:
        try:
            if ctx.tokens is not None:
                self._drive_scripted(ctx)
            else:
                self._loop(ctx)
        except Exception as exc:  # surfaced in the UI rather than killing the thread
            ctx.status = "failed"
            ctx.error = f"{type(exc).__name__}: {exc}"
            ctx.outcome = f"Failed: {ctx.error}"
        finally:
            if not ctx.stop.is_set():
                self._halt()
            ctx.current_action = None
            if ctx.status == "running":
                ctx.status = "stopped"
            ctx.ended_at = time.time()
            ctx.ended.set()  # System 2 is waiting on this; the video can trail
            ctx.recorder.set_hud(
                [f"S2: {ctx.task[:60]}", f"{ctx.status} after {len(ctx.steps)} steps"]
            )
            time.sleep(0.5)  # hold the final frame so the outcome is visible
            ctx.recorder.stop()
            ctx.video_ready = ctx.recorder.frames_written > 0
            self._write_log(ctx)  # final status, error, and ended_at

    def _finish(self, ctx: RunContext, status: str, outcome: str, error: str | None = None) -> None:
        ctx.status = status
        ctx.outcome = outcome
        ctx.error = error

    def _loop(self, ctx: RunContext) -> None:
        """ Repeat System 1 until it reports the instruction complete """
        history: list[str] = []
        failures = 0
        holds = 0
        last_observation = ""

        while not ctx.stop.is_set():
            if len(ctx.steps) >= MAX_STEPS:
                self._finish(ctx, "stopped", f"Gave up after {MAX_STEPS} steps; last saw: {last_observation}",
                             f"Reached the {MAX_STEPS}-step limit")
                return
            if time.time() - ctx.started_at > MAX_RUN_SEC:
                self._finish(ctx, "stopped", f"Ran out of time; last saw: {last_observation}",
                             "Run exceeded the time limit")
                return

            frame = self._wait_for_frame(ctx)
            if frame is None:
                if not ctx.stop.is_set():
                    self._finish(ctx, "failed", "No camera frame available", "No camera frame available")
                return

            step_index = len(ctx.steps) + 1
            self._set_hud(ctx, step_index, "thinking")
            try:
                command, latency = act(
                    frame=frame,
                    instruction=ctx.task,
                    done_when=ctx.done_when,
                    history=history[-HISTORY_LIMIT:],
                    detections=self._detections(frame),
                )
            except Exception as exc:
                failures += 1
                if failures >= SYSTEM1_MAX_FAILURES:
                    self._finish(ctx, "failed", f"System 1 failed ({type(exc).__name__})",
                                 f"System 1 failed {failures} times in a row ({type(exc).__name__})")
                    return
                ctx.sleep(0.5)
                continue
            failures = 0
            if ctx.stop.is_set():
                return

            last_observation = command.observation
            record = {
                "index": step_index,
                "at": time.time(),
                "latency": round(latency, 2),
                "observation": command.observation,
                "done": command.done,
                "actions": list(command.actions),
                "executed": [],
                "measured": [],
                "blocked": [],
            }
            with ctx.lock:
                ctx.steps.append(record)

            if command.done:
                self._write_log(ctx)
                self._finish(ctx, "done", f"Completed; System 1 sees: {command.observation}")
                return

            if command.actions:
                holds = 0
                self._execute(ctx, record, step_index)
            else:
                holds += 1
                if holds >= MAX_HOLDS:
                    self._finish(ctx, "stopped", f"System 1 stopped making progress; last saw: {command.observation}",
                                 "System 1 held still too long")
                    return
                ctx.sleep(OBSERVE_SETTLE_SEC)

            self._write_log(ctx)
            history.append(self._describe_step(record))

    def _drive_scripted(self, ctx: RunContext) -> None:
        """ Drive a fixed token list as a single step, with no model in the loop """
        record = {
            "index": 1,
            "at": time.time(),
            "observation": "",
            "done": False,
            "actions": list(ctx.tokens or []),
            "executed": [],
            "measured": [],
            "blocked": [],
        }
        with ctx.lock:
            ctx.steps.append(record)
        self._write_log(ctx)

        self._execute(ctx, record, 1)
        if ctx.stop.is_set():
            return
        record["done"] = True
        blocked = f" (blocked: {','.join(record['blocked'])})" if record["blocked"] else ""
        self._finish(ctx, "done", f"Drove {','.join(record['executed']) or 'nothing'}{blocked}")

    def _execute(self, ctx: RunContext, record: dict, step_index: int) -> None:
        """ Drive one burst token by token """
        for token in record["actions"]:
            if ctx.stop.is_set():
                return
            move = parse_move(token)
            if move is None:
                continue
            if move.letter == "F" and self._obstacle_ahead():
                record["blocked"].append(move.token)
                continue

            ctx.current_action = move.token
            self._set_hud(ctx, step_index, f"{move.token} ({move.duration:.1f}s)")
            before = self._pose()
            outcome = self._drive(ctx, move)
            record["executed"].append(move.token)
            ctx.current_action = None
            ctx.sleep(SETTLE_SEC)
            # Measured after the settle so odometry has caught up with the coast.
            record["measured"].append({
                "token": move.token,
                "duration": round(move.duration, 2),
                "outcome": outcome,
                **self._displacement(before, self._pose()),
            })
            if outcome == "blocked":
                record["blocked"].append(move.token)

        ctx.sleep(OBSERVE_SETTLE_SEC)

    def _drive(self, ctx: RunContext, move: Move) -> str:
        """ Hold one move, cutting it short on stop or an obstacle ahead """
        velocity = velocity_for(move.letter, self.robot.move_vel, self.robot.rotate_vel)
        deadline = time.time() + move.duration
        self.robot.set_velocity_command(velocity)
        try:
            while time.time() < deadline:
                if ctx.stop.is_set():
                    return "stopped"
                if move.letter == "F" and self._obstacle_ahead():
                    return "blocked"
                time.sleep(DRIVE_POLL_SEC)
            return "completed"
        finally:
            self.robot.set_velocity_command(None)

    # ----- Helpers -----
    @staticmethod
    def _describe_step(step: dict) -> str:
        executed = ",".join(step["executed"]) or "held still"
        blocked = f" (blocked by obstacle: {','.join(step['blocked'])})" if step["blocked"] else ""
        return f"step {step['index']}: {executed} | saw {step['observation']}{blocked}"

    def _obstacle_ahead(self) -> bool:
        """ Lidar reflex: something within OBSTACLE_STOP_M in the forward cone """
        lidar_at = getattr(self.robot, "lidar_at", None)
        if lidar_at is None or time.time() - lidar_at > LIDAR_STALE_SEC:
            return False

        cos_ahead = math.cos(OBSTACLE_HALF_ANGLE)
        for ray in getattr(self.robot, "lidar_rays", []):
            x, y = ray["x"], ray["y"]
            r = math.hypot(x, y)
            if r == 0 or r > OBSTACLE_STOP_M or y <= 0:
                continue
            if y / r >= cos_ahead:
                return True
        return False

    def _halt(self) -> None:
        try:
            self.robot.set_velocity_command(None)
        except Exception:
            pass

    def _wait_for_frame(self, ctx: RunContext):
        deadline = time.time() + FRAME_WAIT_SEC
        while time.time() < deadline:
            frame = self.robot.last_frame
            if frame is not None:
                return frame
            if not ctx.sleep(0.1):
                return None
        return None

    def _detections(self, frame) -> list[str] | None:
        """ Optional YOLOE label hints; never fatal to the run """
        if frame is None or self.vision is None or not getattr(self.vision, "active", False):
            return None
        try:
            return sorted(self.vision.detect(frame))
        except Exception:
            return None

    def _set_hud(self, ctx: RunContext, step_index: int, note: str) -> None:
        ctx.recorder.set_hud([f"S2: {ctx.task[:60]}", f"S1 step {step_index} | {note}"])
