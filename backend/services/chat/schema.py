"""
Tool definitions System 2 can call (OpenAI Responses API function tools).

Everything System 2 does is a tool call: acting (navigate, move, stop) and
answering (speak out loud, reply in the chat). A turn ends at its answer tool.
"""

from ..vla.actions import MOVE_TOKENS

MAX_MOVE_TOKENS = 8

NAVIGATE_TOOL = {
    "type": "function",
    "name": "navigate",
    "description": (
        "Hand one instruction to System 1, the robot's vision-action loop. System 1 "
        "looks through the camera every step, drives small moves, and keeps looping "
        "until it sees done_when satisfied. Use for anything that depends on what the "
        "robot sees: go to, approach, find, look for, face, follow, avoid."
    ),
    "strict": True,
    "parameters": {
        "type": "object",
        "properties": {
            "instruction": {
                "type": "string",
                "description": (
                    "One clear imperative for System 1, e.g. 'turn until the chair is "
                    "centred, then walk up to it'. One goal per call."
                ),
            },
            "done_when": {
                "type": "string",
                "description": (
                    "What the camera frame looks like when the instruction is complete, "
                    "e.g. 'the chair is centred and fills the lower half of the frame'."
                ),
            },
        },
        "required": ["instruction", "done_when"],
        "additionalProperties": False,
    },
}

MOVE_TOOL = {
    "type": "function",
    "name": "move",
    "description": (
        "Drive a short blind movement sequence with no camera in the loop: "
        "'come forward', 'back up', 'turn around', 'spin'. F/B move ~0.2 m per unit, "
        "L/R turn ~19 degrees per unit (F5 ~ 1 m, R5 ~ quarter turn right, L9 ~ about face)."
    ),
    "strict": True,
    "parameters": {
        "type": "object",
        "properties": {
            "tokens": {
                "type": "array",
                "items": {"type": "string", "enum": list(MOVE_TOKENS)},
                "description": f"Moves in order, at most {MAX_MOVE_TOKENS}. Chain for longer moves (2 m = F9, F1).",
            },
            "summary": {"type": "string", "description": "One short sentence describing the move."},
        },
        "required": ["tokens", "summary"],
        "additionalProperties": False,
    },
}

STOP_TOOL = {
    "type": "function",
    "name": "stop",
    "description": "Immediately stop all movement, including a running navigate instruction.",
    "strict": True,
    "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
}

SPEAK_TOOL = {
    "type": "function",
    "name": "speak",
    "description": (
        "Say something out loud through the robot's speaker, in Boxy's voice. This is how "
        "you answer a human who is talking to you by voice. One or two short sentences, "
        "plain words only (no lists, markdown, or emoji)."
    ),
    "strict": True,
    "parameters": {
        "type": "object",
        "properties": {"text": {"type": "string", "description": "Exactly what Boxy says."}},
        "required": ["text"],
        "additionalProperties": False,
    },
}

REPLY_TOOL = {
    "type": "function",
    "name": "reply",
    "description": "Show a text reply in the chat window. Ends your turn.",
    "strict": True,
    "parameters": {
        "type": "object",
        "properties": {"text": {"type": "string", "description": "The chat message."}},
        "required": ["text"],
        "additionalProperties": False,
    },
}

ACTION_TOOLS = [NAVIGATE_TOOL, MOVE_TOOL, STOP_TOOL]
# Voice: every answer is spoken, so speak ends the turn.
VOICE_TOOLS = [*ACTION_TOOLS, SPEAK_TOOL]
# Text chat over the backend API: reply ends the turn; speak is optional.
TEXT_TOOLS = [*ACTION_TOOLS, SPEAK_TOOL, REPLY_TOOL]
