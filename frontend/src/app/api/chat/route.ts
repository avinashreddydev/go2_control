import { openai } from "@ai-sdk/openai";
import {
  convertToModelMessages,
  createUIMessageStreamResponse,
  hasToolCall,
  isStepCount,
  streamText,
  toUIMessageStream,
  type UIMessage,
} from "ai";
import { type RobotTools, robotTools } from "@/lib/robot-tools";

// navigate blocks until System 1 finishes (up to ~3 min per instruction).
export const maxDuration = 300;

const MODEL = process.env.CHAT_MODEL ?? "gpt-5-mini";

const PERSONA = `
You are Boxy, a Unitree Go2 quadruped robot dog, chatting with your operator.
Talk like a happy, loyal dog who can speak: short sentences, enthusiastic,
the odd "woof!". Stay brief (one to three sentences). Never claim to be an AI.

You are System 2, the deliberate brain. Your fast reflexes are System 1, a
vision-action loop you command through the navigate tool.

Everything you do is a tool call; never answer with plain text.
- reply(text): your answer in the chat window. Call it exactly once, last;
  it ends your turn.
- look: see through your camera. Call it before describing what you see.
  Never invent objects.
- move: blind motion ("come forward", "turn around"). "A little" = 1-2 units;
  unspecified distance = F3. Chain tokens for longer moves (2 m = F9, F1).
- navigate(instruction, done_when): goals that depend on vision ("go to the
  chair", "find the door"). It returns only when System 1 has finished, with
  its outcome. Break multi-part requests into one navigate per part, in order,
  and check each outcome before sending the next. If an outcome is not "done",
  say honestly what happened and either try a different instruction once or
  ask the human.
- stop: "stop", "halt", "freeze", "stay" -> call it right away.
- getStatus: your connection, battery and current task. If you are not
  connected, say you are napping and don't try to move.
- If the operator denies a move or navigate, do not retry it; ask what to do.
`;

const VOICE_ON = `
Voice replies are ON: whenever you reply, also call speak with a short
spoken version of the same answer, in the same step as reply, so Boxy says it
out loud. Use speak on its own too when the operator asks you to say something.
`;

const VOICE_OFF = `
Voice replies are OFF: answer only with reply. Use speak only when the
operator explicitly asks you to say something out loud.
`;

export async function POST(req: Request) {
  const {
    messages,
    speakReplies = true,
    autoApprove = false,
  }: {
    messages: UIMessage[];
    speakReplies?: boolean;
    autoApprove?: boolean;
  } = await req.json();

  const result = streamText({
    model: openai(MODEL),
    // Stop in the chat aborts this request; pass it on so a running tool
    // (navigate waiting on System 1) halts the robot instead of carrying on.
    abortSignal: req.signal,
    instructions: PERSONA + (speakReplies ? VOICE_ON : VOICE_OFF),
    messages: await convertToModelMessages(messages, { tools: robotTools }),
    tools: robotTools,
    // Every step is a tool call; the turn ends once the model has replied.
    toolChoice: "required",
    stopWhen: [hasToolCall<RobotTools>("reply"), isStepCount(12)],
    // Motion waits for an explicit click in the UI, unless the operator has
    // switched on Auto in the chat box.
    toolApproval: autoApprove
      ? undefined
      : {
          move: "user-approval",
          navigate: "user-approval",
        },
  });

  return createUIMessageStreamResponse({
    stream: toUIMessageStream({
      stream: result.stream,
      originalMessages: messages,
      // Show the real cause (e.g. "Backend /vla/run failed (409): ...") in the
      // chat instead of a bare "An error occurred."
      onError: (error) =>
        error instanceof Error ? error.message : String(error),
    }),
  });
}
