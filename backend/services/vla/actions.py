"""
Low-level action grammar for the VLA loop.

A step is a short sequence of range-9 tokens: a direction letter followed by a
magnitude 1-9 (``F3``, ``B1``, ``L9``, ``R4``), plus the bare letter ``E`` which
the planner uses to mark a scripted run or calibration burst as finished.

Magnitude scales how long the move is held, not the velocity: the robot always
drives at the speeds configured in settings, so a token is a distance/angle
quantum rather than a throttle.
"""

from typing import Literal, NamedTuple

MIN_MAGNITUDE = 1
MAX_MAGNITUDE = 9
# Seconds a single magnitude unit is held (F9 -> 9 * 0.35 = 3.15 s of driving)
UNIT_DURATION_SEC = 0.35
END_TOKEN = "E"

# Measured on the robot from odometry at the default speeds (move 0.8,
# rotate 0.6): F3 covered 0.599 m, R2 turned 38.2 deg. Both fall short of
# velocity x duration because each burst spends time ramping up, so these are
# empirical rather than derived. They are what the policy is told a magnitude
# buys it, so re-measure with /vla/action if the default speeds change.
METERS_PER_UNIT = 0.2
DEGREES_PER_UNIT = 19

DIRECTIONS: dict[str, str] = {
    "F": "forward",
    "B": "backward",
    "L": "turn left (counter-clockwise, in place)",
    "R": "turn right (clockwise, in place)",
}

MOVE_TOKENS = tuple(
    f"{letter}{magnitude}"
    for letter in DIRECTIONS
    for magnitude in range(MIN_MAGNITUDE, MAX_MAGNITUDE + 1)
)
ACTION_TOKENS = MOVE_TOKENS + (END_TOKEN,)

# Literal[<tuple of str>] is the same type as Literal["F1", "F2", ...]; building
# it from the tuple keeps the grammar defined in exactly one place.
ActionToken = Literal[ACTION_TOKENS]  # type: ignore[valid-type]
# System 1 only moves; finishing a sub-goal is System 2's call.
MoveToken = Literal[MOVE_TOKENS]  # type: ignore[valid-type]


class Move(NamedTuple):
    """ One parsed movement token, resolved to a hold duration """

    token: str
    letter: str
    magnitude: int
    duration: float


def is_end(token: str) -> bool:
    """ True when the token is the end-of-task marker """
    return token.strip().upper() == END_TOKEN


def parse_move(token: str) -> Move | None:
    """ Parse a movement token, or return None for ``E`` and malformed tokens """
    token = token.strip().upper()
    if len(token) != 2:
        return None

    letter, digit = token[0], token[1]
    if letter not in DIRECTIONS or not digit.isdigit():
        return None

    magnitude = int(digit)
    if not MIN_MAGNITUDE <= magnitude <= MAX_MAGNITUDE:
        return None

    return Move(
        token=token,
        letter=letter,
        magnitude=magnitude,
        duration=magnitude * UNIT_DURATION_SEC,
    )


def velocity_for(letter: str, move_vel: float, rotate_vel: float) -> tuple[float, float, float]:
    """ Map a direction letter to a (lx, ly, rx) joystick command """
    return {
        "F": (0.0, move_vel, 0.0),
        "B": (0.0, -move_vel, 0.0),
        "L": (0.0, 0.0, -rotate_vel),
        "R": (0.0, 0.0, rotate_vel),
    }[letter]


def describe_grammar(include_end: bool = False) -> str:
    """ Human/LLM readable description of the token vocabulary """
    lines = [f"- {letter}<n>: {meaning}" for letter, meaning in DIRECTIONS.items()]
    if include_end:
        lines.append(f"- {END_TOKEN}: end of a scripted burst")
    return "\n".join(lines)
