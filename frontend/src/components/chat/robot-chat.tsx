"use client";

import { useChat } from "@ai-sdk/react";
import {
  DefaultChatTransport,
  lastAssistantMessageIsCompleteWithApprovalResponses,
  type UIMessage,
} from "ai";
import { ArrowDownIcon, Dog, PlusIcon } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { BatteryLevel } from "@/components/robot/battery-level";
import { useRobot } from "@/components/robot/robot-context";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Bubble, BubbleContent } from "@/components/ui/bubble";
import { Button } from "@/components/ui/button";
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";
import { Message, MessageContent } from "@/components/ui/message";
import { api } from "@/lib/api";
import { PromptForm } from "./prompt-form";
import { type AnyToolPart, ToolPart } from "./tool-part";

const SUGGESTIONS = [
  "What do you see?",
  "Turn around",
  "Walk to the chair",
  "Say hello out loud",
];

// Within this many pixels of the bottom counts as "following" the conversation.
const STICK_PX = 64;

function ChatMessage({
  message,
  live,
  onApproval,
}: {
  message: UIMessage;
  /** This is the message currently being answered. */
  live: boolean;
  onApproval: (id: string, approved: boolean) => void;
}) {
  if (message.role === "user") {
    return (
      <Message align="end">
        <MessageContent>
          <Bubble align="end" variant="muted">
            <BubbleContent>
              {message.parts
                .filter((part) => part.type === "text")
                .map((part) => part.text)
                .join("")}
            </BubbleContent>
          </Bubble>
        </MessageContent>
      </Message>
    );
  }

  return (
    <Message align="start">
      <MessageContent>
        {message.parts.map((part, index) => {
          const key = `${message.id}-${index}`;
          // System 2 answers through the reply tool: show it as the message.
          if (part.type === "tool-reply") {
            const text = (part.input as { text?: string } | undefined)?.text;
            return text ? (
              <p
                key={key}
                className="whitespace-pre-wrap px-1.5 leading-relaxed"
              >
                {text}
              </p>
            ) : null;
          }
          if (part.type === "text") {
            return part.text.trim() ? (
              <p
                key={key}
                className="whitespace-pre-wrap px-1.5 leading-relaxed"
              >
                {part.text}
              </p>
            ) : null;
          }
          if (part.type === "dynamic-tool" || part.type.startsWith("tool-")) {
            return (
              <ToolPart
                key={key}
                part={part as AnyToolPart}
                live={live}
                onApproval={onApproval}
              />
            );
          }
          return null;
        })}
      </MessageContent>
    </Message>
  );
}

/** Keeps the newest message in view unless the reader has scrolled up. */
function useStickToBottom(deps: unknown) {
  const viewportRef = useRef<HTMLDivElement>(null);
  const followingRef = useRef(true);
  const [atBottom, setAtBottom] = useState(true);

  const scrollToBottom = useCallback((smooth = true) => {
    const el = viewportRef.current;
    if (!el) return;
    followingRef.current = true;
    el.scrollTo({ top: el.scrollHeight, behavior: smooth ? "smooth" : "auto" });
  }, []);

  const onScroll = useCallback(() => {
    const el = viewportRef.current;
    if (!el) return;
    const near = el.scrollHeight - el.scrollTop - el.clientHeight < STICK_PX;
    followingRef.current = near;
    setAtBottom(near);
  }, []);

  // biome-ignore lint/correctness/useExhaustiveDependencies: re-run on every message update
  useEffect(() => {
    if (followingRef.current) scrollToBottom(false);
  }, [deps, scrollToBottom]);

  return { viewportRef, atBottom, onScroll, scrollToBottom };
}

export function RobotChat({ actions }: { actions?: React.ReactNode }) {
  const { connected } = useRobot();

  // Read at request time, so a toggle applies to the very next request,
  // including the automatic one sent after a tool approval.
  const [speakReplies, setSpeakReplies] = useState(true);
  const speakRef = useRef(speakReplies);
  speakRef.current = speakReplies;
  // Off on every page load on purpose: the robot never drives unasked by default.
  const [autoApprove, setAutoApprove] = useState(false);
  const autoRef = useRef(autoApprove);
  autoRef.current = autoApprove;

  const {
    messages,
    sendMessage,
    status,
    stop,
    error,
    addToolApprovalResponse,
    setMessages,
  } = useChat({
    transport: new DefaultChatTransport({
      api: "/api/chat",
      body: () => ({
        speakReplies: speakRef.current,
        autoApprove: autoRef.current,
      }),
    }),
    sendAutomaticallyWhen: lastAssistantMessageIsCompleteWithApprovalResponses,
  });

  const isBusy = status === "submitted" || status === "streaming";
  const { viewportRef, atBottom, onScroll, scrollToBottom } =
    useStickToBottom(messages);

  const send = (text: string) => {
    sendMessage({ text });
    scrollToBottom();
  };

  // Stop means stop: cancel the answer AND halt the robot directly, rather
  // than relying on the cancelled request to reach the tool that is driving.
  const stopEverything = () => {
    stop();
    api.stopAll();
  };

  const onApproval = (id: string, approved: boolean) =>
    addToolApprovalResponse({ id, approved });

  // In Auto mode nothing waits for a click: this also approves a request that
  // was already pending when Auto was switched on.
  useEffect(() => {
    if (!autoApprove) return;
    const last = messages.at(-1);
    if (last?.role !== "assistant") return;
    for (const part of last.parts) {
      const tool = part as AnyToolPart;
      if (tool.state === "approval-requested" && !tool.approval.isAutomatic) {
        addToolApprovalResponse({ id: tool.approval.id, approved: true });
      }
    }
  }, [autoApprove, messages, addToolApprovalResponse]);

  // Wipes the conversation. An in-flight answer is cancelled first; a robot
  // run it started keeps going (say "stop" for that).
  const newChat = () => {
    if (isBusy) stop();
    setMessages([]);
  };

  return (
    <section className="flex h-full min-h-0 flex-col bg-background">
      <header className="flex items-center justify-between gap-2 px-6 py-3">
        <div className="flex items-center gap-2 font-medium text-sm">
          Boxy
          <span
            className={
              connected
                ? "size-2 rounded-full bg-emerald-500"
                : "size-2 rounded-full bg-muted-foreground/40"
            }
            title={connected ? "Connected" : "Not connected"}
          />
          <BatteryLevel className="ml-1" />
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="secondary"
            size="sm"
            onClick={newChat}
            disabled={messages.length === 0}
          >
            <PlusIcon data-icon="inline-start" />
            New chat
          </Button>
          {actions}
        </div>
      </header>

      {messages.length === 0 ? (
        <div className="flex min-h-0 flex-1 items-center justify-center p-4">
          <Empty className="p-6">
            <EmptyHeader>
              <EmptyMedia variant="icon">
                <Dog />
              </EmptyMedia>
              <EmptyTitle>What should Boxy do?</EmptyTitle>
              <EmptyDescription>
                Ask what it sees, send it somewhere, or have it say something.
                {autoApprove
                  ? " Auto is on: Boxy moves without asking."
                  : " Moves wait for your approval."}
              </EmptyDescription>
            </EmptyHeader>
            <EmptyContent>
              <div className="flex flex-wrap justify-center gap-2">
                {SUGGESTIONS.map((prompt) => (
                  <Button
                    key={prompt}
                    variant="outline"
                    size="sm"
                    onClick={() => send(prompt)}
                  >
                    {prompt}
                  </Button>
                ))}
              </div>
            </EmptyContent>
          </Empty>
        </div>
      ) : (
        <div className="relative min-h-0 flex-1">
          <div
            ref={viewportRef}
            onScroll={onScroll}
            className="scroll-fade-b h-full overflow-y-auto overscroll-contain"
          >
            <div className="mx-auto flex w-full max-w-2xl flex-col gap-6 px-6 py-6">
              {messages.map((message) => (
                <ChatMessage
                  key={message.id}
                  message={message}
                  live={isBusy && message.id === messages.at(-1)?.id}
                  onApproval={onApproval}
                />
              ))}
              {status === "submitted" && (
                <div className="shimmer px-1.5 text-muted-foreground text-sm">
                  Thinking…
                </div>
              )}
            </div>
          </div>
          <Button
            variant="secondary"
            size="icon-sm"
            aria-label="Scroll to latest"
            onClick={() => scrollToBottom()}
            className={
              atBottom
                ? "pointer-events-none absolute bottom-3 left-1/2 -translate-x-1/2 translate-y-2 scale-95 border-border bg-background opacity-0 transition-all duration-200"
                : "absolute bottom-3 left-1/2 -translate-x-1/2 border-border bg-background opacity-100 transition-all duration-200"
            }
          >
            <ArrowDownIcon />
          </Button>
        </div>
      )}

      <div className="mx-auto flex w-full max-w-2xl flex-col gap-2 px-6 pb-6">
        {error && (
          <Alert variant="destructive">
            <AlertTitle>Request failed</AlertTitle>
            <AlertDescription>{error.message}</AlertDescription>
          </Alert>
        )}
        <PromptForm
          isBusy={isBusy}
          speakReplies={speakReplies}
          autoApprove={autoApprove}
          onAutoApproveChange={setAutoApprove}
          onSpeakRepliesChange={setSpeakReplies}
          onSubmit={send}
          onStop={stopEverything}
        />
      </div>
    </section>
  );
}
