"use client";

import type { DynamicToolUIPart, ToolUIPart } from "ai";
import {
  Activity,
  CheckIcon,
  Eye,
  Footprints,
  type LucideIcon,
  Navigation,
  OctagonX,
  Volume2,
  Wrench,
  XIcon,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";

export type AnyToolPart = ToolUIPart | DynamicToolUIPart;

export function toolName(part: AnyToolPart) {
  return part.type === "dynamic-tool"
    ? part.toolName
    : part.type.slice("tool-".length);
}

type Input = Record<string, unknown>;

// How each System 2 tool reads in the transcript, while running and once done.
const TOOLS: Record<
  string,
  {
    icon: LucideIcon;
    running: (i: Input) => string;
    done: (i: Input) => string;
    ask?: string;
  }
> = {
  look: {
    icon: Eye,
    running: () => "Looking through the camera…",
    done: () => "Looked through the camera",
  },
  getStatus: {
    icon: Activity,
    running: () => "Checking status…",
    done: () => "Checked status",
  },
  speak: {
    icon: Volume2,
    running: (i) => `Saying “${i.text ?? ""}”…`,
    done: (i) => `Said “${i.text ?? ""}”`,
  },
  move: {
    icon: Footprints,
    running: (i) => `Moving: ${i.summary ?? tokens(i)}…`,
    done: (i) => `Moved: ${i.summary ?? tokens(i)}`,
    ask: "Move",
  },
  navigate: {
    icon: Navigation,
    running: (i) => `System 1 navigating: ${i.instruction ?? ""}…`,
    done: (i) => `System 1: ${i.instruction ?? ""}`,
    ask: "Navigate",
  },
  stop: {
    icon: OctagonX,
    running: () => "Stopping…",
    done: () => "Stopped all movement",
  },
};

function tokens(i: Input) {
  return ((i.tokens as string[] | undefined) ?? []).join(" ");
}

// System 1's result, as returned by the navigate/move tools.
function outcome(output: unknown) {
  const o = (output ?? {}) as Record<string, unknown>;
  const parts = [String(o.status ?? "")];
  if (typeof o.steps === "number") parts.push(`${o.steps} steps`);
  if (o.outcome) parts.push(String(o.outcome));
  return parts.filter(Boolean).join(" · ");
}

function Row({
  icon: Icon,
  busy,
  children,
  tone = "muted",
}: {
  icon: LucideIcon;
  busy?: boolean;
  children: React.ReactNode;
  tone?: "muted" | "destructive";
}) {
  return (
    <div
      className={
        tone === "destructive"
          ? "flex items-start gap-2 px-1.5 text-destructive text-sm"
          : "flex items-start gap-2 px-1.5 text-muted-foreground text-sm"
      }
    >
      {busy ? (
        <Spinner className="mt-0.5 shrink-0" />
      ) : (
        <Icon className="mt-0.5 size-4 shrink-0" />
      )}
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  );
}

export function ToolPart({
  part,
  live,
  onApproval,
}: {
  part: AnyToolPart;
  /** False once the turn is over: a call still "running" was cut off by Stop. */
  live: boolean;
  onApproval: (id: string, approved: boolean) => void;
}) {
  const name = toolName(part);
  const spec = TOOLS[name] ?? {
    icon: Wrench,
    running: () => `Running ${name}…`,
    done: () => `Ran ${name}`,
  };
  const input = (part.input ?? {}) as Input;

  switch (part.state) {
    case "approval-requested":
      if (part.approval.isAutomatic) break;
      return (
        <div className="flex flex-col gap-3 rounded-2xl border p-3.5 text-sm">
          <div className="flex items-start gap-2">
            <spec.icon className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
            <div className="min-w-0">
              <div className="font-medium">{spec.ask ?? name}?</div>
              <div className="text-muted-foreground">
                {name === "move"
                  ? `${input.summary ?? ""} (${tokens(input)})`
                  : String(input.instruction ?? "")}
              </div>
              {name === "navigate" && input.done_when ? (
                <div className="mt-1 text-muted-foreground text-xs">
                  Done when: {String(input.done_when)}
                </div>
              ) : null}
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Button
              size="sm"
              onClick={() => onApproval(part.approval.id, true)}
            >
              <CheckIcon data-icon="inline-start" />
              Approve
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => onApproval(part.approval.id, false)}
            >
              <XIcon data-icon="inline-start" />
              Deny
            </Button>
            <span className="ml-auto text-muted-foreground text-xs">
              Check the path is clear
            </span>
          </div>
        </div>
      );
    case "output-available":
      return (
        <Row icon={spec.icon}>
          <div className="truncate">{spec.done(input)}</div>
          {(name === "navigate" || name === "move") && (
            <div className="text-xs">{outcome(part.output)}</div>
          )}
        </Row>
      );
    case "output-denied":
      return (
        <Row icon={spec.icon}>
          <span className="line-through">{spec.done(input)}</span> · denied
        </Row>
      );
    case "output-error":
      return (
        <Row icon={spec.icon} tone="destructive">
          {spec.ask ?? spec.done(input)} failed: {part.errorText}
        </Row>
      );
  }

  if (!live) {
    return (
      <Row icon={OctagonX}>
        <span className="line-through">
          {spec.running(input).replace(/…$/, "")}
        </span>{" "}
        · stopped
      </Row>
    );
  }

  return (
    <Row icon={spec.icon} busy>
      <span className="shimmer">{spec.running(input)}</span>
    </Row>
  );
}
