"""
FastAPI entrypoint for the Go2 WebRTC control stack.

Exposes MJPEG video, YOLOE vision toggles, keyboard teleop, speech, and VLA
task execution.
"""

import io
import json
import logging
import os
import queue
import re
import subprocess
import threading
import time
import wave
from collections import deque
from typing import Literal

import cv2
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, Response, StreamingResponse
from openai import OpenAIError
from pydantic import BaseModel, Field, ValidationError

from unitree_webrtc_connect.webrtc_driver import UnitreeWebRTCConnection, WebRTCConnectionMethod
from backend.services.listener import VoiceListener, transcribe
from backend.services.push_to_talk import PushToTalk
from backend.services.robot import RobotClient
from backend.services.speaker_stream import SAMPLE_RATE as STREAM_RATE, to_stream_pcm
from backend.services.speech import (
    playback_seconds,
    speakable,
    synthesize,
    to_speaker_wav,
    wav_duration,
)
from backend.services.vision import YoloeDetector
from backend.services.world import SemanticWorld
from backend.services.chat.runner import ChatResult, respond as chat_respond
from backend.services.chat.schema import TEXT_TOOLS
from backend.services.vla.actions import (
    ACTION_TOKENS,
    DEGREES_PER_UNIT,
    DIRECTIONS,
    METERS_PER_UNIT,
    MOVE_TOKENS,
    UNIT_DURATION_SEC,
)
from backend.services.vla.executor import MAX_RUN_SEC, MAX_STEPS, VLARunner
from backend.services.vla.prompts import MAX_ACTIONS_PER_STEP
from backend.services.vla.llm import models as vla_models
from backend.services.vla.system1 import act as system1_act

# ----- App setup -----
class _SkipHealthLog(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return (
            "GET /health" not in msg
            and "GET /video/metrics" not in msg
            and "GET /lidar/scan" not in msg
            and "GET /robot/pose" not in msg
            and "GET /robot/state3d" not in msg
            and "GET /lidar/points" not in msg
            and "GET /world/objects" not in msg
            and "GET /vla/status" not in msg  # polled every second by navigate
            # Polled several times a second by the dashboard
            and "POST /teleop/key" not in msg
            and "GET /voice/status" not in msg
        )

logging.getLogger("uvicorn.access").addFilter(_SkipHealthLog())

# backend.services.robot calls logging.basicConfig(level=FATAL) to silence the
# WebRTC stack, which would swallow our own errors too. An explicit level on
# this logger keeps them visible without unmuting everything else.
vla_log = logging.getLogger("vla")
vla_log.setLevel(logging.ERROR)

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----- Globals -----
robot: RobotClient | None = None
vision = YoloeDetector()
vla_runner: VLARunner | None = None
listener: VoiceListener | None = None
ptt: PushToTalk | None = None
world: SemanticWorld | None = None

MAX_CHAT_TOKENS = 8

# "Hey Boxy" voice control: started from the Voice card in the UI, or at
# startup when VOICE_AUTO_LISTEN=1 is set in .env
VOICE_AUTO_LISTEN = os.getenv("VOICE_AUTO_LISTEN", "0").strip().lower() not in ("0", "false", "no", "off")
# Matched on the words after "Hey Boxy"; handled at once, never queued behind a model call
VOICE_STOP = re.compile(r"\b(stop|halt|freeze|stay|cancel|enough|whoa)\b", re.IGNORECASE)
VOICE_HISTORY_TURNS = 6
voice_jobs: queue.Queue[str] = queue.Queue(maxsize=4)
voice_history: deque[dict] = deque(maxlen=VOICE_HISTORY_TURNS)
voice_log: deque[dict] = deque(maxlen=20)
# Voice turns and System 1 reports both edit voice_history; one at a time.
voice_turn_lock = threading.Lock()
# A spoken request may chain navigate -> report -> navigate ...; cap the chain.
MAX_REPORT_CHAIN = 4
# Reports are not spoken for runs the operator ended on purpose.
SILENT_OUTCOMES = ("Stopped by the operator", "Replaced by a new instruction")

# Push-to-talk: what the dog says when the button goes down and comes up
PTT_PROMPT = "I'm listening!"
PTT_THANKS = "Thanks!"
PTT_MAX_SEC = 30.0          # a stuck button is released for you after this
PTT_MIN_SEC = 0.3           # shorter than this is a tap, not speech
PTT_ECHO_WAIT_SEC = 1.5     # longest wait for the prompt's echo tail before recording
# Fixed phrases are synthesized once and replayed, so the prompt plays fast
speech_cache: dict[str, object] = {}
# One megaphone session at a time; later utterances queue behind it
speak_lock = threading.Lock()
MAX_AUDIO_BYTES = 10 * 1024 * 1024
MAX_AUDIO_SEC = 30.0   # megaphone: upload is ~3x real time
MAX_STREAM_SEC = 120.0
MAX_RECORD_SEC = 60.0
RUN_ID_PATTERN = re.compile(r"^[0-9]{8}-[0-9]{6}-[a-z0-9-]+$")

FRAME_STALE_SEC = 2.0
BATTERY_STALE_SEC = 5.0
LIDAR_STALE_SEC = 2.0
STREAM_START_GRACE_SEC = 15.0
MIN_VELOCITY = 0.1
MAX_VELOCITY = 1.0



# ----- Request models -----
class TeleopKeyEvent(BaseModel):
    key: str
    pressed: bool

class VisionLabels(BaseModel):
    labels: list[str]

class VLAPrompt(BaseModel):
    prompt: str = Field(min_length=1)

class VLARunRequest(BaseModel):
    # System 2's instruction for System 1
    prompt: str = Field(min_length=1)
    # What the camera shows once the instruction is complete; System 1 checks it every frame
    done_when: str = ""
    # Drive these moves directly with no model in the loop (the chat's move tool)
    tokens: list[str] | None = Field(default=None, min_length=1, max_length=MAX_CHAT_TOKENS)

class VLATokens(BaseModel):
    tokens: list[str] = Field(min_length=1, max_length=8)

class ListenRequest(BaseModel):
    enabled: bool

class SpeakRequest(BaseModel):
    text: str = Field(min_length=1, max_length=500)

class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)

class ChatRequest(BaseModel):
    messages: list[ChatTurn] = Field(min_length=1, max_length=100)

class VelocitySettings(BaseModel):
    move: float | None = None
    rotate: float | None = None
    strafe: float | None = None


# ----- Video helpers -----
def latest_frame():
    """ Return the newest BGR frame from the robot queue, or None if unavailable """
    if robot is None or robot.frame_queue.empty():
        return None, None
    img = robot.frame_queue.get()
    captured_at = robot.last_frame_at
    while not robot.frame_queue.empty():
        img = robot.frame_queue.get()
        captured_at = robot.last_frame_at
    return img, captured_at

def mjpeg_converter():
    """ Generate multipart JPEG chunks for StreamingResponse (/video/feed) """
    while True:
        img, captured_at = latest_frame()
        if img is None:
            time.sleep(0.01)
            continue

        if vision.active:
            img = vision.predict(img)

        ok, jpeg = cv2.imencode(".jpg", img)
        if not ok:
            continue

        encoded_at = time.time()
        if robot is not None:
            robot.stream_encode_times.append(encoded_at)
            if captured_at is not None:
                robot.last_stream_captured_at = captured_at

        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n\r\n"
            + jpeg.tobytes()
            + b"\r\n"
        )


# ----- Services -----

# --- Lifecycle ---
@app.on_event("startup")
def startup():
    """ Connect to the robot on Local AP and start background services """
    global robot, vla_runner, listener, ptt, world
    conn = UnitreeWebRTCConnection(WebRTCConnectionMethod.LocalAP)
    robot = RobotClient(conn)
    robot.start()
    vla_runner = VLARunner(robot, vision)
    listener = VoiceListener(robot, can_listen=_can_listen, on_command=_on_voice_command)
    ptt = PushToTalk(robot)
    world = SemanticWorld(robot, vision)
    world.start()
    threading.Thread(target=_voice_worker, daemon=True).start()
    if VOICE_AUTO_LISTEN:
        # Safe before the robot connects: the mic hook attaches once it does.
        listener.set_enabled(True)


def _can_listen() -> bool:
    """ Deaf while talking, so it never answers its own voice, and during push-to-talk """
    if ptt is not None and ptt.state != "idle":
        return False
    return robot is not None and not robot.speaking


def _stop_all() -> None:
    """ Halt everything that can drive the legs """
    if vla_runner is not None:
        vla_runner.stop()
    if robot is not None:
        robot.clear_teleop_keys()


def _say(text: str, cache: bool = False) -> bool:
    """ Speak through the robot; raises on synthesis or playback failure """
    if robot is None or not robot.conn.isConnected:
        raise RuntimeError("Robot is not connected")
    text = speakable(text)
    if not text:
        return False
    pcm = speech_cache.get(text) if cache else None
    if pcm is None:
        # Live over the WebRTC audio track: real time, unlike the megaphone upload
        pcm = to_stream_pcm(synthesize(text))
        if cache:
            speech_cache[text] = pcm
    with speak_lock:
        robot.stream_audio(pcm)
    return True


def _say_quietly(text: str, cache: bool = False) -> None:
    """ Speak, logging instead of raising: voice replies must not kill the worker """
    try:
        _say(text, cache=cache)
    except Exception:
        vla_log.exception("Voice reply playback failed")


# --- "Hey Boxy" voice control ---
def _on_voice_command(command: str) -> None:
    """ Called by the listener for every line that started with "Hey Boxy" """
    if VOICE_STOP.search(command):
        # Safety first: stop on the listener thread, before any model call.
        _stop_all()
        voice_log.append({"at": time.time(), "heard": command, "intent": "stop", "reply": "Stopping!"})
        threading.Thread(target=_say_quietly, args=("Okay, stopping!",), daemon=True).start()
        return
    try:
        voice_jobs.put_nowait(command)
    except queue.Full:
        vla_log.warning("Voice: command backlog full, dropping %r", command)


def _voice_worker() -> None:
    while True:
        command = voice_jobs.get()
        try:
            _handle_voice(command)
        except Exception:
            vla_log.exception("Voice command failed")
            _say_quietly("Ruff, something went wrong. Try again?")


def _handle_voice(command: str) -> None:
    """ Answer a spoken command with System 2, which may call tools to move """
    if command == "":
        _say_quietly("Woof? I'm listening!")
        if listener is not None:
            listener.extend_follow_up()  # the window ran down while talking
        voice_log.append({"at": time.time(), "heard": "", "intent": "wake", "reply": "Woof? I'm listening!"})
        return

    result = _voice_turn(command, depth=0)
    voice_log.append({
        "at": time.time(),
        "heard": command,
        "intent": result.intent,
        "reply": result.reply,
        "tools": result.tool_calls,
    })
    _ensure_spoken(result)


def _ensure_spoken(result: ChatResult) -> None:
    """ The answer is normally a speak tool call; cover a turn that never made one """
    if not result.spoken:
        _say_quietly(result.reply or "Woof?")


def _voice_turn(text: str, depth: int) -> ChatResult:
    """ One System 2 turn on the shared voice conversation """
    def on_navigate(run_id: str, instruction: str) -> None:
        _report_when_done(run_id, instruction, depth)

    with voice_turn_lock:
        frame = robot.last_frame if robot is not None else None
        voice_history.append({"role": "user", "content": text})
        result = chat_respond(
            list(voice_history),
            _robot_state(frame),
            frame=frame,
            run_tool=lambda name, args: _run_tool(name, args, on_navigate=on_navigate),
        )
        voice_history.append({"role": "assistant", "content": result.reply})
    return result


def _report_when_done(run_id: str, instruction: str, depth: int) -> None:
    """
    Wait for System 1 to finish the instruction, then hand its outcome back to
    System 2, which tells the human and may send the next instruction.
    """
    def watch() -> None:
        snap = vla_runner.wait(run_id)
        if snap["run_id"] != run_id:
            return  # superseded by a newer instruction; that one reports instead
        if snap["outcome"] in SILENT_OUTCOMES or depth + 1 > MAX_REPORT_CHAIN:
            return
        report = (
            f"[System 1 report] Instruction: {instruction}. "
            f"Result: {snap['status']} after {snap['step_count']} steps. {snap['outcome'] or ''}"
        )
        try:
            result = _voice_turn(report, depth + 1)
        except Exception:
            vla_log.exception("System 2 follow-up failed")
            return
        voice_log.append({
            "at": time.time(),
            "heard": report,
            "intent": result.intent,
            "reply": result.reply,
            "tools": result.tool_calls,
        })
        _ensure_spoken(result)

    threading.Thread(target=watch, daemon=True).start()


def _run_tool(name: str, args: dict, on_navigate=None) -> dict:
    """ Execute one System 2 tool call; the returned dict is what the model reads """
    if name == "stop":
        _stop_all()
        return {"stopped": True}
    if name == "reply":
        return {"shown": True}
    if name == "speak":
        try:
            return {"spoken": _say(str(args.get("text", "")))}
        except Exception as exc:
            vla_log.exception("speak tool failed")
            return {"error": f"Could not speak: {type(exc).__name__}"}
    if vla_runner is None or robot is None or not robot.conn.isConnected:
        return {"error": "Robot is not connected"}

    if name == "move":
        tokens = [t for t in args.get("tokens", []) if t in MOVE_TOKENS][:MAX_CHAT_TOKENS]
        if not tokens:
            return {"error": "No valid movement tokens"}
        robot.clear_teleop_keys()
        run_id = vla_runner.start(args.get("summary") or "move", tokens=tokens)
        return {"status": "started", "run_id": run_id, "tokens": tokens}

    if name == "navigate":
        instruction = str(args.get("instruction", "")).strip()
        if not instruction:
            return {"error": "Empty instruction"}
        if robot.last_frame is None:
            return {"error": "No camera frame, so System 1 cannot see to navigate"}
        robot.clear_teleop_keys()
        run_id = vla_runner.start(instruction, done_when=str(args.get("done_when", "")))
        if on_navigate is not None:
            on_navigate(run_id, instruction)
        return {
            "status": "started",
            "run_id": run_id,
            "note": "System 1 is looping on the camera; a [System 1 report] follows when it finishes.",
        }

    return {"error": f"Unknown tool {name}"}


@app.on_event("shutdown")
def shutdown():
    """ Stop any running task, release teleop keys, and join the robot thread """
    if world:
        world.stop()
    if vla_runner:
        vla_runner.stop()
    if robot:
        robot.clear_teleop_keys()
        robot.stop()

@app.get("/health")
def health():
    connected = robot is not None and robot.conn.isConnected
    has_frames = (
        robot is not None
        and robot.last_frame_at is not None
        and time.time() - robot.last_frame_at < FRAME_STALE_SEC
    )
    battery_soc = None
    if (
        robot is not None
        and robot.battery_soc is not None
        and robot.battery_at is not None
        and time.time() - robot.battery_at < BATTERY_STALE_SEC
    ):
        battery_soc = robot.battery_soc

    if robot is None:
        robot_status = "waiting"
    elif robot.connect_status == "failed":
        robot_status = "failed"
    elif connected:
        robot_status = "connected"
    elif robot.connect_status == "connecting":
        robot_status = "connecting"
    else:
        robot_status = "waiting"

    if not connected:
        stream_status = "waiting"
    elif has_frames:
        stream_status = "active"
    elif robot.last_frame_at is None:
        age = time.time() - (robot.connected_at or 0)
        stream_status = "starting" if age < STREAM_START_GRACE_SEC else "failed"
    elif time.time() - robot.last_frame_at < STREAM_START_GRACE_SEC:
        stream_status = "starting"
    else:
        stream_status = "failed"

    return {
        "connected": connected,
        "has_frames": has_frames,
        "robot_status": robot_status,
        "stream_status": stream_status,
        "vision_active": vision.active,
        "battery_soc": battery_soc,
    }


@app.get("/world/objects")
def world_objects():
    """ Detected people and things, tracked and placed in the world (odometry) frame """
    if world is None:
        return {"enabled": False, "available": vision.available, "objects": []}
    return {
        "enabled": world.enabled,
        "available": world.available,
        "detect_ms": world.last_run_ms,
        "objects": world.snapshot(),
    }


@app.post("/world/toggle")
def world_toggle():
    """ Pause or resume the semantic world loop (it costs one YOLO pass per tick) """
    if world is None:
        return {"enabled": False, "available": vision.available}
    world.enabled = not world.enabled
    return {"enabled": world.enabled, "available": world.available}


@app.get("/lidar/points")
def lidar_points():
    """
    The latest lidar voxel map for the 3D view: raw little-endian float32
    x, y, z triples (metres, world/odom frame, z up). Empty when stale.
    """
    pts = robot.lidar_points if robot is not None else None
    fresh = robot is not None and robot.lidar_at is not None and time.time() - robot.lidar_at < LIDAR_STALE_SEC
    body = pts.astype("<f4").tobytes() if pts is not None and fresh else b""
    return Response(
        content=body,
        media_type="application/octet-stream",
        headers={"Cache-Control": "no-store", "X-Point-Count": str(len(body) // 12)},
    )


@app.get("/robot/state3d")
def robot_state3d():
    """ Full odometry pose (position + quaternion) and leg joint angles for the 3D avatar """
    if robot is None:
        return {"pose": None, "joints": None, "at": time.time()}
    fresh = robot.joints_at is not None and time.time() - robot.joints_at < BATTERY_STALE_SEC
    return {
        "pose": robot.robot_pose,
        "joints": robot.joint_q if fresh else None,
        "at": time.time(),
    }


@app.get("/robot/pose")
def robot_pose():
    """ Odometry pose for the trajectory plot; polled at ~10 Hz by the UI """
    if robot is None:
        return {"pose": None}
    return {"pose": robot.pose_2d(), "at": time.time()}


@app.post("/robot/reconnect")
def robot_reconnect():
    """ Tear down the current peer connection and attempt a fresh one """
    if robot is None:
        return {"ok": False}
    if vla_runner is not None:
        vla_runner.stop()
    robot.reconnect()
    return {"ok": True}


@app.post("/robot/speak")
def robot_speak(body: SpeakRequest):
    """ Say the text out loud through the robot's speaker """
    if robot is None or not robot.conn.isConnected:
        raise HTTPException(status_code=503, detail="Robot is not connected")
    try:
        spoken = _say(body.text)
    except Exception as exc:
        vla_log.exception("Speech failed")
        raise HTTPException(
            status_code=502, detail=f"Speech failed: {type(exc).__name__}"
        ) from exc
    return {"ok": True, "spoken": spoken}


@app.post("/robot/play")
async def robot_play(request: Request, mode: Literal["stream", "megaphone"] = "stream"):
    """
    Play an uploaded audio file (raw request body, any ffmpeg format) on the speaker.

    stream: live over the WebRTC audio track, in real time.
    megaphone: base64 upload over the datachannel, ~3x slower than the audio.
    """
    if robot is None or not robot.conn.isConnected:
        raise HTTPException(status_code=503, detail="Robot is not connected")
    data = await request.body()
    if not data:
        raise HTTPException(status_code=422, detail="Empty audio body")
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="Audio file too large (10 MB max)")
    decode = to_stream_pcm if mode == "stream" else to_speaker_wav
    try:
        audio = await run_in_threadpool(decode, data)
    except subprocess.CalledProcessError as exc:
        raise HTTPException(status_code=422, detail="Could not decode that audio") from exc
    duration = len(audio) / STREAM_RATE if mode == "stream" else wav_duration(audio)
    limit = MAX_STREAM_SEC if mode == "stream" else MAX_AUDIO_SEC
    if duration > limit:
        raise HTTPException(status_code=413, detail=f"Clip is {duration:.0f}s; {limit:.0f}s max for {mode}")

    def play() -> None:
        with speak_lock:
            if mode == "stream":
                robot.stream_audio(audio)
            else:
                robot.speak(audio, timeout=playback_seconds(audio) + 10)

    try:
        await run_in_threadpool(play)
    except Exception as exc:
        vla_log.exception("Speaker playback failed")
        raise HTTPException(
            status_code=502, detail=f"Playback failed: {type(exc).__name__}"
        ) from exc
    return {"ok": True, "mode": mode, "seconds": round(duration, 2)}


@app.get("/robot/record")
def robot_record(seconds: float = 5.0):
    """ Record the robot microphone for a few seconds and return it as WAV """
    if robot is None or not robot.conn.isConnected:
        raise HTTPException(status_code=503, detail="Robot is not connected")
    if not 0.5 <= seconds <= MAX_RECORD_SEC:
        raise HTTPException(status_code=422, detail=f"seconds must be 0.5-{MAX_RECORD_SEC:.0f}")
    try:
        pcm, rate, channels = robot.record_audio(seconds)
    except Exception as exc:
        vla_log.exception("Recording failed")
        raise HTTPException(status_code=502, detail=f"Recording failed: {type(exc).__name__}") from exc
    if not pcm:
        raise HTTPException(status_code=504, detail="No audio arrived from the robot microphone")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(pcm)
    return Response(
        content=buffer.getvalue(),
        media_type="audio/wav",
        headers={"Content-Disposition": f'attachment; filename="boxy-{time.strftime("%Y%m%d-%H%M%S")}.wav"'},
    )


# --- Push-to-talk ---
def _ptt_prompt() -> None:
    """ Button down: say the prompt, wait out its echo, then open the mic """
    _say_quietly(PTT_PROMPT, cache=True)
    deadline = time.time() + PTT_ECHO_WAIT_SEC
    while robot is not None and robot.speaking and time.time() < deadline:
        time.sleep(0.05)
    if not ptt.open_mic():
        return  # released while the dog was still talking
    # Watchdog: a lost pointer-up must not leave the mic open forever.
    deadline = time.time() + PTT_MAX_SEC
    while ptt.state == "recording" and time.time() < deadline:
        time.sleep(0.1)
    if ptt.state == "recording":
        _ptt_release()


def _ptt_release() -> bool:
    """ Button up: close the mic and answer in the background """
    captured = ptt.end()
    if captured is None:
        return False
    threading.Thread(target=_ptt_respond, args=captured, daemon=True).start()
    return True


def _ptt_respond(audio, rate: int) -> None:
    """ Say thanks, transcribe what was held, then answer and act via the chat LLM """
    heard = None
    try:
        # Thanks plays while the clip is transcribed, hiding part of the wait.
        thanks = threading.Thread(target=_say_quietly, args=(PTT_THANKS, True), daemon=True)
        thanks.start()
        if audio is None or len(audio) < PTT_MIN_SEC * rate:
            thanks.join()
            _say_quietly("Hmm, I didn't hear anything. Hold the button while you talk!")
            heard = ""
            return
        try:
            heard = transcribe(audio, rate)
        except OpenAIError:
            vla_log.exception("Push-to-talk transcription failed")
            thanks.join()
            _say_quietly("Ruff, my ears glitched. Try again?")
            return
        thanks.join()
        if not heard:
            _say_quietly("Sorry friend, I didn't catch that.")
            return
        if VOICE_STOP.search(heard):
            _stop_all()
            voice_log.append({"at": time.time(), "heard": heard, "intent": "stop", "reply": "Okay, stopping!"})
            _say_quietly("Okay, stopping!")
            return
        _handle_voice(heard)
    except Exception:
        vla_log.exception("Push-to-talk reply failed")
        _say_quietly("Ruff, something went wrong. Try again?")
    finally:
        ptt.finish(heard)


@app.post("/voice/ptt/start")
def voice_ptt_start():
    """ Button pressed: the dog says it is listening, then records its mic """
    if ptt is None or robot is None or not robot.conn.isConnected:
        raise HTTPException(status_code=503, detail="Robot is not connected")
    if not ptt.begin():
        raise HTTPException(status_code=409, detail="Still answering the last question")
    threading.Thread(target=_ptt_prompt, daemon=True).start()
    return ptt.status()


@app.post("/voice/ptt/stop")
def voice_ptt_stop():
    """ Button released: the dog says thanks and answers what it heard """
    if ptt is None:
        return {"state": "idle", "seconds": None, "last_heard": None, "clip_at": None}
    _ptt_release()
    return ptt.status()


@app.get("/voice/ptt/recording")
def voice_ptt_recording():
    """ The last push-to-talk recording, exactly as the dog's mic heard it """
    if ptt is None or ptt.last_clip is None:
        raise HTTPException(status_code=404, detail="Nothing recorded yet")
    return Response(
        content=ptt.last_clip,
        media_type="audio/wav",
        headers={"Cache-Control": "no-store"},
    )


@app.post("/voice/listen")
def voice_listen(body: ListenRequest):
    """ Turn "Hey Boxy" listening on the robot microphone on or off """
    if listener is None:
        raise HTTPException(status_code=503, detail="Listener not started")
    if body.enabled and (robot is None or not robot.conn.isConnected):
        raise HTTPException(status_code=503, detail="Robot is not connected")
    listener.set_enabled(body.enabled)
    return listener.status(take=False) | {
        "heard": [], "recent": list(voice_log), "ptt": ptt.status() if ptt else None
    }


@app.get("/voice/status")
def voice_status():
    """ Listening state, plus heard commands not yet handed out (each is returned once) """
    if listener is None:
        return {"enabled": False, "state": "off", "follow_up": False, "last_ignored": None,
                "heard": [], "recent": [], "ptt": {"state": "idle", "seconds": None, "last_heard": None, "clip_at": None}}
    return listener.status() | {"recent": list(voice_log), "ptt": ptt.status() if ptt else None}


@app.post("/robot/chat")
def robot_chat(body: ChatRequest):
    """ One System 2 turn: reply in character, calling navigate/move/stop as needed """
    if body.messages[-1].role != "user":
        raise HTTPException(status_code=422, detail="Last message must be from the user")

    try:
        return _robot_chat(body)
    except HTTPException:
        raise
    except Exception as exc:
        # Surface the cause in the chat instead of a bare "Internal Server Error"
        vla_log.exception("Chat endpoint crashed")
        raise HTTPException(
            status_code=500, detail=f"{type(exc).__name__}: {str(exc)[:200]}"
        ) from exc


def _robot_state(frame) -> dict:
    """ What the chat persona is told about itself """
    detections = None
    if frame is not None and vision.active:
        try:
            detections = sorted(vision.detect(frame))
        except Exception:
            detections = None

    activity = None
    if vla_runner is not None and vla_runner.busy:
        snap = vla_runner.snapshot()
        activity = f"running task: {snap['task']}"

    return {
        "connected": robot is not None and robot.conn.isConnected,
        "battery": robot.battery_soc if robot is not None else None,
        "detections": detections,
        "activity": activity,
    }


def _robot_chat(body: ChatRequest) -> dict:
    frame = robot.last_frame if robot is not None else None
    try:
        result = chat_respond(
            [m.model_dump() for m in body.messages],
            _robot_state(frame),
            frame=frame,
            run_tool=_run_tool,
            tools=TEXT_TOOLS,
            end_tools=("reply",),
        )
    except (OpenAIError, ValidationError) as exc:
        vla_log.exception("Chat reply failed")
        raise HTTPException(
            status_code=502, detail=f"Chat failed: {type(exc).__name__}"
        ) from exc
    return {"reply": result.reply, "intent": result.intent, "tool_calls": result.tool_calls}


# --- Video ---
@app.get("/video/feed")
def video_feed():
    """ MJPEG stream of the robot camera """
    return StreamingResponse(
        mjpeg_converter(),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )

@app.get("/video/snapshot")
def video_snapshot():
    """ Single JPEG of the latest camera frame; used by the chat agent's look tool """
    frame = robot.last_frame if robot is not None else None
    if frame is None:
        raise HTTPException(status_code=503, detail="No camera frame available")
    ok, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
    if not ok:
        raise HTTPException(status_code=500, detail="Could not encode frame")
    return Response(content=jpeg.tobytes(), media_type="image/jpeg")

@app.get("/video/metrics")
def video_metrics():
    """ Return MJPEG output rate and capture-to-encode lag for the vision HUD """
    if robot is None:
        return {"stream_fps": 0, "delay_ms": None}
    now = time.time()
    stream_fps = sum(1 for t in robot.stream_encode_times if now - t < 1.0)
    delay_ms = None
    if robot.last_stream_captured_at is not None:
        delay_ms = max(0, int((now - robot.last_stream_captured_at) * 1000))
    return {"stream_fps": stream_fps, "delay_ms": delay_ms}


# --- Vision ---
@app.post("/vision/toggle")
def vision_toggle():
    """ Flip YOLOE overlay on/off for the video feed """
    vision.active = vision.available and not vision.active
    return {"active": vision.active, "available": vision.available}

@app.get("/vision/status")
def vision_status():
    """ Return whether detection overlay is currently enabled """
    return {"active": vision.active, "available": vision.available}

@app.get("/vision/labels")
def vision_labels_get():
    """ Return the class names currently filtered for detection """
    return {"labels": vision.get_labels()}

@app.post("/vision/labels")
def vision_labels_set(body: VisionLabels):
    """ Restrict detection to the given label names (empty list = all classes) """
    vision.set_labels(body.labels)
    return {"labels": vision.get_labels()}


# --- Lidar ---
@app.get("/lidar/scan")
def lidar_scan():
    """ Return the latest lidar ray endpoints in the robot frame (meters) """
    if (
        robot is None
        or robot.lidar_at is None
        or time.time() - robot.lidar_at > LIDAR_STALE_SEC
    ):
        return {"rays": []}
    return {"rays": robot.lidar_rays}


# --- Settings ---
def _clamp_velocity(value: float | None) -> float | None:
    if value is None:
        return None
    return max(MIN_VELOCITY, min(MAX_VELOCITY, value))

@app.get("/settings/velocities")
def settings_velocities_get():
    """ Return current teleop / task movement speeds """
    if robot is None:
        return {"move": 0.8, "rotate": 0.6, "strafe": 0.9}
    return robot.get_velocities()

@app.post("/settings/velocities")
def settings_velocities_set(body: VelocitySettings):
    """ Update one or more movement speeds (0.1–1.0) """
    if robot is None:
        return {"ok": False}
    velocities = robot.set_velocities(
        move=_clamp_velocity(body.move),
        rotate=_clamp_velocity(body.rotate),
        strafe=_clamp_velocity(body.strafe),
    )
    return {"ok": True, **velocities}


# --- Teleop ---
@app.post("/teleop/key")
def teleop_key(event: TeleopKeyEvent):
    """ Hold or release a movement key for continuous driving """
    if robot is None:
        return {"ok": False}
    robot.set_teleop_key(event.key, event.pressed)
    return {"ok": True}

@app.post("/teleop/release")
def teleop_release():
    """ Release all held teleop keys and send a zero-velocity command """
    if robot is None:
        return {"ok": False}
    robot.clear_teleop_keys()
    return {"ok": True}


# --- VLA (prompt -> vision -> action loop) ---
@app.get("/vla/grammar")
def vla_grammar():
    """ Describe the action vocabulary so the UI can document it """
    return {
        "directions": DIRECTIONS,
        "tokens": list(ACTION_TOKENS),
        "unit_duration_sec": UNIT_DURATION_SEC,
        "meters_per_unit": METERS_PER_UNIT,
        "degrees_per_unit": DEGREES_PER_UNIT,
        "max_actions_per_step": MAX_ACTIONS_PER_STEP,
        "max_steps": MAX_STEPS,
        "max_run_sec": MAX_RUN_SEC,
        "models": vla_models(),
    }


@app.post("/vla/run")
def vla_run(body: VLARunRequest):
    """
    Start System 1 on one System 2 instruction; it loops until done_when is seen.

    A new instruction replaces a running one: System 2's latest word wins.
    Poll /vla/status for progress and the final ``outcome``.
    """
    if vla_runner is None:
        raise HTTPException(status_code=503, detail="Robot is not ready")

    prompt = body.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=422, detail="Prompt must not be empty")
    if body.tokens is not None:
        moves = [t for t in body.tokens if t.strip().upper() in MOVE_TOKENS]
        if not moves:
            raise HTTPException(status_code=422, detail="No movement tokens to drive")

    if robot is not None:
        robot.clear_teleop_keys()

    run_id = vla_runner.start(prompt, done_when=body.done_when, tokens=body.tokens)
    return {"ok": True, "run_id": run_id}


@app.post("/vla/plan")
def vla_plan(body: VLAPrompt):
    """ Run one System 1 inference against the current frame without moving """
    if robot is None or robot.last_frame is None:
        raise HTTPException(status_code=503, detail="No camera frame available")

    prompt = body.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=422, detail="Prompt must not be empty")

    try:
        command, latency = system1_act(frame=robot.last_frame, instruction=prompt)
    except (OpenAIError, ValidationError) as exc:
        # Without the type the caller cannot tell an expired key from a bad
        # schema; the message itself may carry request details, so it is logged
        # rather than returned.
        vla_log.exception("VLA inference failed")
        raise HTTPException(
            status_code=502, detail=f"VLA inference failed: {type(exc).__name__}"
        ) from exc
    return {**command.model_dump(), "latency": round(latency, 2)}


@app.post("/vla/action")
def vla_action(body: VLATokens):
    """ Drive raw action tokens with no model in the loop (calibration) """
    if vla_runner is None:
        raise HTTPException(status_code=503, detail="Robot is not ready")

    unknown = [t for t in body.tokens if t.strip().upper() not in ACTION_TOKENS]
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown action tokens: {unknown}")

    if robot is not None:
        robot.clear_teleop_keys()

    try:
        executed = vla_runner.drive_tokens(body.tokens)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True, "executed": executed}


@app.post("/vla/stop")
def vla_stop():
    """ Interrupt the loop, halt the robot, and finalize the recording """
    if vla_runner is None:
        return {"ok": False}
    vla_runner.stop()
    return {"ok": True, "status": vla_runner.status}


@app.get("/vla/status")
def vla_status():
    """ Full run state: status, per-step reasoning, actions, and video link """
    if vla_runner is None:
        return {"status": "idle", "plan": [], "plan_index": 0, "steps": [], "step_count": 0}
    return vla_runner.snapshot()


@app.get("/vla/recording/{run_id}")
def vla_recording(run_id: str):
    """ Serve the MP4 recorded during a run """
    if vla_runner is None or not RUN_ID_PATTERN.match(run_id):
        raise HTTPException(status_code=404, detail="Recording not found")

    path = vla_runner.video_path(run_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Recording not found")
    return FileResponse(path, media_type="video/mp4", filename=path.name)


@app.get("/vla/log/{run_id}")
def vla_run_log(run_id: str):
    """ Serve the saved inference trace for a run """
    if vla_runner is None or not RUN_ID_PATTERN.match(run_id):
        raise HTTPException(status_code=404, detail="Log not found")

    path = vla_runner.log_path(run_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Log not found")
    return FileResponse(path, media_type="application/json", filename=path.name)


@app.get("/vla/runs")
def vla_runs():
    """ List past runs newest first, for replay and offline analysis """
    if vla_runner is None:
        return {"runs": []}

    runs = []
    for path in sorted(vla_runner.recordings_dir.glob("*.json"), reverse=True):
        try:
            with path.open() as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            continue
        run_id = data.get("run_id", path.stem)
        runs.append({
            "run_id": run_id,
            "task": data.get("task"),
            "status": data.get("status"),
            "steps": len(data.get("steps", [])),
            "elapsed": data.get("elapsed"),
            "started_at": data.get("started_at"),
            "log_url": f"/vla/log/{run_id}",
            "video_url": (
                f"/vla/recording/{run_id}"
                if vla_runner.video_path(run_id).exists()
                else None
            ),
        })
    return {"runs": runs}
