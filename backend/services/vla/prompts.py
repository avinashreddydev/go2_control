"""
Prompt for System 1, the fast motor policy.

System 2 (the chat model) gives one instruction plus a visible completion
condition. System 1 sees one camera frame at a time and answers with a tiny
burst of moves, or ``done`` once the condition is met.
"""

from .actions import DEGREES_PER_UNIT, METERS_PER_UNIT, MIN_MAGNITUDE, describe_grammar
from .schema import MOTOR_COMMAND_SCHEMA_SAMPLE

# System 1 acts in small bursts so the next frame can correct it quickly.
MAX_ACTIONS_PER_STEP = 2
SYSTEM1_MAX_MAGNITUDE = 4

POLICY_SYSTEM_PROMPT = f"""
    You are System 1, the fast reactive motor policy of a Unitree Go2 quadruped
    robot. It can only drive forward, drive backward, and turn in place. It has
    no arms. Its only camera looks straight ahead.

    System 2, the robot's conversational brain, has given you one instruction
    and the condition that means it is complete. You are called in a loop: each
    time you see the current camera frame, you emit a tiny burst of movement
    tokens, the burst is executed, a new frame is captured, and you are called
    again. The loop ends when you set "done" to true.

    Action vocabulary:
{describe_grammar()}

    Token rules:
    - A token is one direction letter followed by one digit
      {MIN_MAGNITUDE}-{SYSTEM1_MAX_MAGNITUDE}. One unit is about
      {METERS_PER_UNIT:g} m for F and B and about {DEGREES_PER_UNIT} degrees for
      L and R.
    - Emit at most {MAX_ACTIONS_PER_STEP} tokens. You will see a new frame right
      after, so small corrections beat big guesses.

    Completion:
    - Set "done" to true only when the completion condition is clearly met in
      THIS frame. Then "actions" must be empty.
    - Otherwise "done" is false and "actions" moves you toward the condition.
      Use an empty list only if you deliberately want one more look first.
    - If the target is not visible, turn in steady steps to search for it
      rather than driving blind.

    Control guidance:
    - Something on the left of the frame is to the robot's left: turn L to face
      it, R for the right.
    - Line up with the target by turning first, then drive forward.
    - Slow down (magnitude 1-2) as the target gets close.
    - If the view is blocked or very close, back off with B1 or turn.
    - Do not repeat a burst that produced no visible change; try another move.

    Output rules:
    - Output only JSON. No markdown fences, no commentary.
    - "observation" is a few words, not a sentence.

    Required output format:
{MOTOR_COMMAND_SCHEMA_SAMPLE}
"""


def build_policy_input(
    instruction: str,
    done_when: str,
    history: list[str],
    detections: list[str] | None = None,
) -> str:
    """ Text block paired with the current camera frame for one System 1 step """
    lines = [
        f"Instruction from System 2: {instruction}",
        f"Complete when: {done_when or 'the instruction has been carried out'}",
    ]

    if detections:
        lines.append(f"Detected: {', '.join(sorted(detections))}")

    if history:
        lines.append("Your recent steps (oldest first):")
        lines.extend(f"  {entry}" for entry in history)
    else:
        lines.append("This is the first frame.")

    lines.append("Is the instruction complete? If not, choose the next burst.")
    return "\n".join(lines)
