"""
Terminal interface to System 1, bypassing the chat (System 2).

Give it one instruction; it connects to the robot, then loops System 1 on the
live camera frame, executing its small F/B/L/R bursts, until System 1 reports
the instruction done. The whole run is recorded to recordings/<run-id>.mp4.

    python scripts/vla_prompt.py "walk up to the chair" --done-when "the chair fills the frame"
    python scripts/vla_prompt.py            # interactive, one instruction per line

Owns its own WebRTC connection, so run it with the FastAPI backend stopped.
"""

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from backend.services.robot import RobotClient  # noqa: E402
from backend.services.vla.executor import VLARunner  # noqa: E402
from unitree_webrtc_connect.webrtc_driver import (  # noqa: E402
    UnitreeWebRTCConnection,
    WebRTCConnectionMethod,
)

CONNECT_TIMEOUT_SEC = 30.0
POLL_SEC = 0.25


def wait_for_camera(robot: RobotClient, timeout: float = CONNECT_TIMEOUT_SEC) -> bool:
    """ Block until the first camera frame arrives """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if robot.last_frame is not None:
            return True
        if robot.connect_status == "failed":
            return False
        time.sleep(0.2)
    return False


def print_step(step: dict) -> None:
    executed = ",".join(step["executed"]) or "-"
    print(f"\n  step {step['index']}: {executed}{'  [done]' if step['done'] else ''}")
    print(f"    saw:   {step['observation']}")
    if step["blocked"]:
        print(f"    blocked by obstacle: {','.join(step['blocked'])}")
    if step["actions"] != step["executed"]:
        print(f"    asked: {','.join(step['actions'])}")


def run_task(runner: VLARunner, task: str, done_when: str = "") -> None:
    """ Run one instruction and stream System 1's steps to stdout """
    run_id = runner.start(task, done_when=done_when)
    print(f"\nrun {run_id}")

    printed = 0
    try:
        while True:
            snapshot = runner.snapshot()
            for step in snapshot["steps"][printed:]:
                print_step(step)
            printed = len(snapshot["steps"])
            if snapshot["status"] != "running":
                break
            time.sleep(POLL_SEC)
    except KeyboardInterrupt:
        print("\n  interrupted, stopping the robot...")
        runner.stop()

    snapshot = runner.snapshot()
    for step in snapshot["steps"][printed:]:
        print_step(step)

    print(f"\n  {snapshot['status']} after {snapshot['step_count']} steps in {snapshot['elapsed']}s")
    print(f"  outcome: {snapshot['outcome']}")
    if snapshot["error"]:
        print(f"  error: {snapshot['error']}")

    # The recorder flushes just after the loop ends; wait for it to close.
    deadline = time.time() + 10
    while not snapshot["video_ready"] and time.time() < deadline:
        time.sleep(POLL_SEC)
        snapshot = runner.snapshot()
    print(f"  video: {runner.video_path(run_id)}" if snapshot["video_ready"]
          else "  video: not recorded")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a high-level task on the Go2 robot.")
    parser.add_argument("task", nargs="?", help="Instruction for System 1; omit for interactive mode")
    parser.add_argument("--done-when", default="", help="What the camera shows once it is done")
    args = parser.parse_args()

    conn = UnitreeWebRTCConnection(WebRTCConnectionMethod.LocalAP)
    robot = RobotClient(conn)
    robot.start()

    try:
        print("connecting to the robot...")
        if not wait_for_camera(robot):
            print("no camera frames; is the robot reachable?", file=sys.stderr)
            return 1
        print("camera live.")

        runner = VLARunner(robot)

        if args.task:
            run_task(runner, args.task, args.done_when)
            return 0

        while True:
            try:
                task = input("\ntask> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return 0
            if task in ("", "quit", "exit"):
                return 0
            run_task(runner, task)
    finally:
        robot.clear_teleop_keys()
        robot.stop()


if __name__ == "__main__":
    raise SystemExit(main())
