"""
System 2 turn loop: one operator message in, tool calls out.

Every step must be a tool call (``tool_choice="required"``). Each call runs
through ``run_tool`` and its result is fed back, until the model calls one of
the ``end_tools`` (its answer: speak or reply) or ``MAX_TOOL_ROUNDS`` is hit.
"""

import json
from dataclasses import dataclass, field
from typing import Callable

import numpy as np
from dotenv import load_dotenv
from openai import OpenAI

from ..vla.llm import encode_frame
from .prompts import CHAT_SYSTEM_PROMPT, build_state_block
from .schema import VOICE_TOOLS

load_dotenv(override=True)  # .env wins over a stale key exported in the shell

MODEL = "gpt-5-mini"
# Older turns add cost without changing the next reply much.
HISTORY_LIMIT = 20
MAX_TOOL_ROUNDS = 5
ANSWER_TOOLS = ("speak", "reply")

ToolRunner = Callable[[str, dict], dict]


@dataclass
class ChatResult:
    # What the turn answered with (spoken and/or replied text), "" if nothing
    reply: str
    # Every tool call made this turn: {"name", "arguments", "output"}
    tool_calls: list[dict] = field(default_factory=list)
    # True when the answer was delivered by a speak call already
    spoken: bool = False

    @property
    def intent(self) -> str:
        """ The first action tool used, or "chat" when the model only answered """
        for call in self.tool_calls:
            if call["name"] not in ANSWER_TOOLS:
                return call["name"]
        return "chat"


def respond(
    messages: list[dict],
    state: dict,
    frame: np.ndarray | None = None,
    run_tool: ToolRunner | None = None,
    client: OpenAI | None = None,
    tools: list[dict] | None = None,
    end_tools: tuple[str, ...] = ("speak",),
) -> ChatResult:
    """ Run one turn: tool calls until the model answers through an end tool """
    tools = tools or VOICE_TOOLS
    client = client or OpenAI()
    history = messages[-HISTORY_LIMIT:]

    items: list = [{"role": turn["role"], "content": turn["content"]} for turn in history[:-1]]
    content: list[dict] = [
        {
            "type": "input_text",
            "text": f"{build_state_block({**state, 'has_frame': frame is not None})}\n\n"
            f"{history[-1]['content']}",
        }
    ]
    if frame is not None:
        content.append({"type": "input_image", "image_url": encode_frame(frame)})
    items.append({"role": "user", "content": content})

    calls_made: list[dict] = []
    answers: dict[str, list[str]] = {"speak": [], "reply": []}
    spoken = False
    for _ in range(MAX_TOOL_ROUNDS):
        response = client.responses.create(
            model=MODEL,
            instructions=CHAT_SYSTEM_PROMPT,
            input=items,
            tools=tools,
            tool_choice="required",
        )
        calls = [item for item in response.output if item.type == "function_call"]
        if not calls:  # should not happen with tool_choice=required
            break

        items += response.output  # reasoning + calls must precede their outputs
        ended = False
        for call in calls:
            try:
                arguments = json.loads(call.arguments or "{}")
                output = run_tool(call.name, arguments) if run_tool else {"error": "No tool runner"}
            except Exception as exc:  # the model hears about it instead of crashing the turn
                arguments, output = {}, {"error": f"{type(exc).__name__}: {exc}"}
            calls_made.append({"name": call.name, "arguments": arguments, "output": output})
            items.append({
                "type": "function_call_output",
                "call_id": call.call_id,
                "output": json.dumps(output),
            })
            if call.name in ANSWER_TOOLS and arguments.get("text"):
                answers[call.name].append(str(arguments["text"]))
                spoken = spoken or (call.name == "speak" and "error" not in output)
            ended = ended or call.name in end_tools
        if ended:
            break

    # The chat text if there is one, else what was said out loud
    return ChatResult(" ".join(answers["reply"] or answers["speak"]), calls_made, spoken)
