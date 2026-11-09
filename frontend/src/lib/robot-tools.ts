import "server-only";

import { tool } from "ai";
import { z } from "zod";

// Server-side: the route handler reaches FastAPI directly, not via the rewrite.
const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8000";

async function backend<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BACKEND_URL}${path}`, {
    cache: "no-store",
    ...init,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* non-JSON body */
    }
    throw new Error(`Backend ${path} failed (${res.status}): ${detail}`);
  }
  return res.json() as Promise<T>;
}

const postJson = <T>(path: string, body?: unknown) =>
  backend<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });

// System 1 runs are bounded server-side (MAX_RUN_SEC = 180); this is slack on top.
const RUN_TIMEOUT_MS = 200_000;
const RUN_POLL_MS = 1000;

// Statuses during which System 1 still holds the robot. "planning" only comes
// from an older backend build, but must not be mistaken for a finished run.
const IN_PROGRESS = new Set(["running", "planning"]);

type RunSnapshot = {
  status: string;
  run_id: string | null;
  step_count: number;
  outcome: string | null;
  error: string | null;
  elapsed: number | null;
  steps: { observation: string }[];
};

function sleep(ms: number, signal?: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    if (signal?.aborted) return reject(new Error("Aborted"));
    const id = setTimeout(resolve, ms);
    signal?.addEventListener(
      "abort",
      () => {
        clearTimeout(id);
        reject(new Error("Aborted"));
      },
      { once: true },
    );
  });
}

/**
 * Start a run and block until System 1 finishes it, so System 2 gets the real
 * outcome as the tool result. Aborting the chat request stops the robot.
 */
async function runToCompletion(
  body: { prompt: string; done_when?: string; tokens?: string[] },
  signal?: AbortSignal,
) {
  const { run_id } = await postJson<{ ok: boolean; run_id: string }>(
    "/vla/run",
    body,
  );
  const halt = () => {
    postJson("/vla/stop").catch(() => {});
  };
  signal?.addEventListener("abort", halt, { once: true });
  try {
    const deadline = Date.now() + RUN_TIMEOUT_MS;
    for (;;) {
      await sleep(RUN_POLL_MS, signal);
      const snap = await backend<RunSnapshot>("/vla/status");
      if (!("outcome" in snap)) {
        halt();
        throw new Error(
          "The backend is running an older build without the System 1 loop. Restart it (./run.sh backend).",
        );
      }
      if (snap.run_id !== run_id) {
        return {
          status: "replaced",
          outcome: "A newer instruction replaced this one",
        };
      }
      const inProgress = IN_PROGRESS.has(snap.status);
      if (!inProgress || Date.now() > deadline) {
        if (inProgress) halt();
        return {
          status: inProgress ? "timed_out" : snap.status,
          outcome: snap.outcome ?? snap.error ?? null,
          steps: snap.step_count,
          seconds: snap.elapsed,
          last_seen: snap.steps.at(-1)?.observation ?? null,
        };
      }
    }
  } finally {
    signal?.removeEventListener("abort", halt);
  }
}

// Same grammar as backend/services/vla/actions.py.
const MoveToken = z
  .string()
  .regex(/^[FBLR][1-9]$/)
  .describe(
    "F/B = forward/back ~0.2 m per unit; L/R = turn ~19° per unit. e.g. F5 ≈ 1 m, R5 ≈ 90° right",
  );

export const robotTools = {
  getStatus: tool({
    description:
      "Read the dog's live state: connection, battery %, video stream, and whether a movement task is running.",
    inputSchema: z.object({}),
    execute: async () => {
      const [health, vla] = await Promise.all([
        backend<Record<string, unknown>>("/health"),
        backend<{ status: string; task?: string | null }>("/vla/status").catch(
          () => null,
        ),
      ]);
      return {
        ...health,
        task_status: vla?.status ?? "unknown",
        task: vla?.task ?? null,
      };
    },
  }),

  look: tool({
    description:
      "Take a photo with the dog's forward-facing camera and look at it.",
    inputSchema: z.object({}),
    execute: async () => {
      const res = await fetch(`${BACKEND_URL}/video/snapshot`, {
        cache: "no-store",
      });
      if (!res.ok) throw new Error(`No camera frame available (${res.status})`);
      return {
        mediaType: "image/jpeg",
        data: Buffer.from(await res.arrayBuffer()).toString("base64"),
      };
    },
    toModelOutput: ({ output }) => ({
      type: "content",
      value: [
        {
          type: "file",
          mediaType: output.mediaType,
          data: { type: "data", data: output.data },
        },
      ],
    }),
  }),

  move: tool({
    description:
      "Drive a short blind movement sequence (no vision). Use for 'come forward', 'turn around', 'back up'.",
    inputSchema: z.object({
      tokens: z.array(MoveToken).min(1).max(8),
      summary: z
        .string()
        .describe(
          "One short sentence describing the move, shown to the operator",
        ),
    }),
    execute: async ({ tokens, summary }, { abortSignal }) =>
      runToCompletion({ prompt: summary, tokens }, abortSignal),
  }),

  navigate: tool({
    description:
      "Hand ONE vision-guided instruction to System 1, the dog's fast motor loop. " +
      "System 1 looks through the camera every step, drives small moves, and keeps looping " +
      "until it sees done_when, then this returns the outcome. Use for go to / approach / " +
      "find / face something. For a multi-part request, call it once per part, in order.",
    inputSchema: z.object({
      instruction: z
        .string()
        .min(1)
        .describe(
          "One clear imperative, e.g. 'turn until the chair is centred, then walk up to it'",
        ),
      done_when: z
        .string()
        .min(1)
        .describe(
          "What the camera shows once it is done, e.g. 'the chair fills the lower half of the frame'",
        ),
    }),
    execute: async ({ instruction, done_when }, { abortSignal }) =>
      runToCompletion({ prompt: instruction, done_when }, abortSignal),
  }),

  stop: tool({
    description: "Immediately stop all movement (task loop, teleop).",
    inputSchema: z.object({}),
    execute: async () => {
      await Promise.allSettled([
        postJson("/vla/stop"),
        postJson("/teleop/release"),
      ]);
      return { stopped: true };
    },
  }),

  reply: tool({
    description:
      "Show your answer to the operator in the chat window. Every turn ends with exactly one reply.",
    inputSchema: z.object({
      text: z.string().min(1).describe("The chat message, in Boxy's voice."),
    }),
    execute: async () => ({ shown: true }),
  }),

  speak: tool({
    description:
      "Say something out loud through the dog's speaker, in Boxy's own voice. One or two short sentences, plain words.",
    inputSchema: z.object({ text: z.string().min(1).max(500) }),
    execute: async ({ text }) =>
      postJson<{ ok: boolean; spoken: boolean }>("/robot/speak", { text }),
  }),
};

export type RobotTools = typeof robotTools;
