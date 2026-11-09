"""
GPT plumbing for System 1, the fast motor policy.

System 2 is the chat model (frontend ``/api/chat`` and the backend voice chat):
it decides what the robot should do and hands System 1 one instruction via the
``navigate`` tool. System 1 then loops on camera frames until it judges that
instruction complete.

Every setting can be overridden from ``.env`` without touching code.
"""

import base64
import os
import threading
import time
from typing import TypeVar

import cv2
import numpy as np
from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel

load_dotenv(override=True)  # .env wins over a stale key exported in the shell

# Fast, reactive: one frame in, one short action burst out.
SYSTEM1_MODEL = os.getenv("VLA_SYSTEM1_MODEL", "gpt-5-mini")
SYSTEM1_EFFORT = os.getenv("VLA_SYSTEM1_EFFORT", "minimal")

# Frames are downscaled before upload: the policy only needs coarse layout and
# every pixel costs latency. Other callers (chat) pass their own width.
DEFAULT_FRAME_WIDTH = 640
SYSTEM1_FRAME_WIDTH = 448
JPEG_QUALITY = 80

Schema = TypeVar("Schema", bound=BaseModel)

_client: OpenAI | None = None
_client_lock = threading.Lock()


def client() -> OpenAI:
    """ One shared client; it is thread-safe and both systems call concurrently """
    global _client
    with _client_lock:
        if _client is None:
            _client = OpenAI()
        return _client


def models() -> dict:
    """ The configuration a run used, for its log """
    return {"system1": {"model": SYSTEM1_MODEL, "effort": SYSTEM1_EFFORT or None}}


def encode_frame(frame: np.ndarray, max_width: int = DEFAULT_FRAME_WIDTH) -> str:
    """ Downscale a BGR frame and return it as a base64 JPEG data URL """
    height, width = frame.shape[:2]
    if width > max_width:
        scale = max_width / width
        frame = cv2.resize(frame, (max_width, int(height * scale)))

    ok, jpeg = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
    if not ok:
        raise ValueError("Failed to JPEG-encode the camera frame")

    return "data:image/jpeg;base64," + base64.b64encode(jpeg.tobytes()).decode("ascii")


def structured_call(
    *,
    model: str,
    effort: str,
    instructions: str,
    text: str,
    schema: type[Schema],
    name: str,
    image_url: str | None = None,
) -> tuple[Schema, float]:
    """ One structured-output call; returns the parsed result and its latency """
    content: list[dict] = [{"type": "input_text", "text": text}]
    if image_url is not None:
        content.append({"type": "input_image", "image_url": image_url})

    kwargs: dict = {}
    if effort:
        kwargs["reasoning"] = {"effort": effort}

    started = time.time()
    response = client().responses.create(
        model=model,
        instructions=instructions,
        input=[{"role": "user", "content": content}],
        text={
            "format": {
                "type": "json_schema",
                "name": name,
                "strict": True,
                "schema": schema.model_json_schema(),
            }
        },
        **kwargs,
    )
    latency = time.time() - started
    return schema.model_validate_json(response.output_text), latency
