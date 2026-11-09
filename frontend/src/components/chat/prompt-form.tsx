"use client";

import {
  ArrowUpIcon,
  Hand,
  SquareIcon,
  Volume2,
  VolumeX,
  Zap,
} from "lucide-react";
import { useState } from "react";
import {
  InputGroup,
  InputGroupAddon,
  InputGroupButton,
  InputGroupTextarea,
} from "@/components/ui/input-group";

export function PromptForm({
  isBusy,
  speakReplies,
  onSpeakRepliesChange,
  autoApprove,
  onAutoApproveChange,
  onSubmit,
  onStop,
}: {
  isBusy: boolean;
  speakReplies: boolean;
  onSpeakRepliesChange: (value: boolean) => void;
  autoApprove: boolean;
  onAutoApproveChange: (value: boolean) => void;
  onSubmit: (text: string) => void;
  onStop: () => void;
}) {
  const [input, setInput] = useState("");

  const submit = (event?: React.FormEvent) => {
    event?.preventDefault();
    const text = input.trim();
    if (!text || isBusy) return;
    onSubmit(text);
    setInput("");
  };

  return (
    <form onSubmit={submit}>
      <InputGroup>
        <InputGroupTextarea
          placeholder="Talk to Boxy…"
          className="max-h-40 p-3.5"
          value={input}
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={(event) => {
            if (
              event.key === "Enter" &&
              !event.shiftKey &&
              !event.nativeEvent.isComposing
            ) {
              event.preventDefault();
              submit();
            }
          }}
        />
        <InputGroupAddon align="block-end">
          <InputGroupButton
            aria-pressed={speakReplies}
            title={
              speakReplies
                ? "Boxy speaks its replies out loud"
                : "Replies are text only"
            }
            onClick={() => onSpeakRepliesChange(!speakReplies)}
            className={speakReplies ? "text-foreground" : undefined}
          >
            {speakReplies ? <Volume2 /> : <VolumeX />}
            {speakReplies ? "Voice on" : "Voice off"}
          </InputGroupButton>
          <InputGroupButton
            aria-pressed={autoApprove}
            title={
              autoApprove
                ? "Auto: Boxy moves without asking. Click to require approval."
                : "Approval: every move waits for your OK. Click for auto mode."
            }
            onClick={() => onAutoApproveChange(!autoApprove)}
            className={
              autoApprove
                ? "bg-amber-500/15 text-amber-700 hover:bg-amber-500/25 hover:text-amber-700 dark:text-amber-400"
                : undefined
            }
          >
            {autoApprove ? <Zap /> : <Hand />}
            {autoApprove ? "Auto" : "Ask first"}
          </InputGroupButton>
          {isBusy ? (
            <InputGroupButton
              type="button"
              size="icon-sm"
              variant="outline"
              aria-label="Stop"
              className="ml-auto"
              onClick={onStop}
            >
              <SquareIcon />
            </InputGroupButton>
          ) : (
            <InputGroupButton
              type="submit"
              size="icon-sm"
              variant="default"
              aria-label="Send message"
              className="ml-auto"
              disabled={!input.trim()}
            >
              <ArrowUpIcon />
            </InputGroupButton>
          )}
        </InputGroupAddon>
      </InputGroup>
    </form>
  );
}
