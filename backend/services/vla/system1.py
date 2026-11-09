"""
System 1: the fast, reactive GPT motor policy.

One camera frame plus System 2's instruction in; either ``done`` or a burst of
at most ``MAX_ACTIONS_PER_STEP`` small movement tokens out. The executor calls
it in a loop until it reports done.
"""

import numpy as np

from .actions import parse_move
from .llm import SYSTEM1_EFFORT, SYSTEM1_FRAME_WIDTH, SYSTEM1_MODEL, encode_frame, structured_call
from .prompts import (
    MAX_ACTIONS_PER_STEP,
    POLICY_SYSTEM_PROMPT,
    SYSTEM1_MAX_MAGNITUDE,
    build_policy_input,
)
from .schema import MotorCommand


def clamp_tokens(tokens: list[str]) -> list[str]:
    """ Enforce the burst length and magnitude cap the prompt asks for """
    clamped = []
    for token in tokens[:MAX_ACTIONS_PER_STEP]:
        move = parse_move(token)
        if move is None:
            continue
        clamped.append(f"{move.letter}{min(move.magnitude, SYSTEM1_MAX_MAGNITUDE)}")
    return clamped


def act(
    frame: np.ndarray,
    instruction: str,
    done_when: str = "",
    history: list[str] | None = None,
    detections: list[str] | None = None,
) -> tuple[MotorCommand, float]:
    """ One loop iteration; returns the command and the call latency """
    command, latency = structured_call(
        model=SYSTEM1_MODEL,
        effort=SYSTEM1_EFFORT,
        instructions=POLICY_SYSTEM_PROMPT,
        text=build_policy_input(instruction, done_when, history or [], detections),
        image_url=encode_frame(frame, SYSTEM1_FRAME_WIDTH),
        schema=MotorCommand,
        name="motor_command",
    )
    command.actions = [] if command.done else clamp_tokens(command.actions)  # type: ignore[assignment]
    return command, latency
