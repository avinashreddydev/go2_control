"""
Structured output for System 1: one camera frame in, one ``MotorCommand`` out.
"""

from pydantic import Field

from ..llm.base import StrictBaseModel
from .actions import MoveToken


class MotorCommand(StrictBaseModel):
    """ One fast perception-to-action step """

    observation: str = Field(description="A few words on where the target is in the frame.")
    done: bool = Field(
        description="True only when the instruction's completion condition is met in this frame.",
    )
    actions: list[MoveToken] = Field(
        description="Movement tokens to execute now. Empty when done, or to hold still for a frame.",
    )


MOTOR_COMMAND_SCHEMA_SAMPLE = """
The output must have this shape:
{
  "observation": "chair left of centre, far",
  "done": false,
  "actions": ["L1", "F2"]
}

Every entry in "actions" must be a direction letter F, B, L or R followed by a
single digit.
"""
