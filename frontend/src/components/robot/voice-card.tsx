"use client";

import { Ear, Loader2, MessageCircle, Mic, MicOff } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  api,
  BACKEND,
  type PttStatus,
  type VoiceState,
  type VoiceStatus,
} from "@/lib/api";
import { useRobot } from "./robot-context";

// Fast while listening so the level meter and replies feel live.
const POLL_ACTIVE_MS = 400;
const POLL_IDLE_MS = 3000;
const RECORD_SECONDS = 5;
// The listener counts a window as speech at ~1.6x background; 4x fills the bar.
const LEVEL_FULL = 4;

function talkLabel(holding: boolean, ptt: PttStatus | null | undefined) {
  const state = ptt?.state ?? "idle";
  if (state === "recording") {
    return `Speak now, release when done${ptt?.seconds != null ? ` (${ptt.seconds.toFixed(1)}s)` : ""}`;
  }
  if (holding) return "Boxy: “I'm listening!”… start talking after it";
  if (state === "prompting" || state === "thinking") return "Boxy is thinking…";
  return "Hold to talk";
}

const isTalkKey = (key: string) => key === " " || key === "Enter";

const STATE_LABEL: Record<VoiceState, string> = {
  off: "Off",
  waiting: "Waiting for “Hey Boxy”",
  muted: "Muted while talking",
  hearing: "Hearing you…",
  transcribing: "Understanding…",
};

export function VoiceCard() {
  const { connected } = useRobot();
  const [status, setStatus] = useState<VoiceStatus | null>(null);
  const [toggling, setToggling] = useState(false);
  const [recording, setRecording] = useState(false);
  const [clipUrl, setClipUrl] = useState<string | null>(null);
  const clipRef = useRef<string | null>(null);
  const [holding, setHolding] = useState(false);
  const holdingRef = useRef(false);
  // Resolves true once the backend accepted the press, false if it refused.
  const startRef = useRef<Promise<boolean> | null>(null);

  const enabled = status?.enabled ?? false;
  const ptt = status?.ptt;
  const talkBusy = holding || (ptt?.state ?? "idle") !== "idle";

  const refresh = useCallback(async () => {
    try {
      setStatus(await api.voiceStatus());
    } catch {
      /* backend offline; the status card already says so */
    }
  }, []);

  useEffect(() => {
    refresh();
    const fast = enabled || talkBusy;
    const id = setInterval(refresh, fast ? POLL_ACTIVE_MS : POLL_IDLE_MS);
    return () => clearInterval(id);
  }, [refresh, enabled, talkBusy]);

  const pressTalk = () => {
    if (holdingRef.current) return;
    holdingRef.current = true;
    setHolding(true);
    startRef.current = api
      .pttStart()
      .then((next) => {
        setStatus((prev) => (prev ? { ...prev, ptt: next } : prev));
        return true;
      })
      .catch((err: Error) => {
        toast.error(`Talk failed: ${err.message}`);
        holdingRef.current = false;
        setHolding(false);
        return false;
      });
  };

  const releaseTalk = async () => {
    if (!holdingRef.current) return;
    holdingRef.current = false;
    setHolding(false);
    // A quick tap can release before the press request has landed.
    if (!(await startRef.current)) return;
    try {
      const next = await api.pttStop();
      setStatus((prev) => (prev ? { ...prev, ptt: next } : prev));
    } catch (err) {
      toast.error(`Talk failed: ${(err as Error).message}`);
    }
  };

  // Release the last recording's object URL when it is replaced or unmounted.
  useEffect(
    () => () => {
      if (clipRef.current) URL.revokeObjectURL(clipRef.current);
    },
    [],
  );

  const toggleListening = async () => {
    setToggling(true);
    try {
      setStatus(await api.voiceListen(!enabled));
    } catch (err) {
      toast.error(`Listening failed: ${(err as Error).message}`);
    } finally {
      setToggling(false);
    }
  };

  const hearMic = async () => {
    setRecording(true);
    try {
      const blob = await api.recordMic(RECORD_SECONDS);
      if (clipRef.current) URL.revokeObjectURL(clipRef.current);
      const url = URL.createObjectURL(blob);
      clipRef.current = url;
      setClipUrl(url);
    } catch (err) {
      toast.error(`Recording failed: ${(err as Error).message}`);
    } finally {
      setRecording(false);
    }
  };

  const level = Math.min(1, (status?.level ?? 0) / LEVEL_FULL);
  const recent = [...(status?.recent ?? [])].reverse().slice(0, 4);

  return (
    <Card size="sm">
      <CardHeader className="flex flex-row items-center justify-between gap-2">
        <CardTitle>Voice</CardTitle>
        <Badge variant={enabled ? "default" : "secondary"}>
          {status?.follow_up
            ? "Listening — no wake word needed"
            : STATE_LABEL[status?.state ?? "off"]}
        </Badge>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <Button
          size="lg"
          className="h-11 w-full touch-none"
          variant={
            holding || ptt?.state === "recording" ? "destructive" : "default"
          }
          disabled={!connected || (!holding && talkBusy)}
          aria-pressed={holding}
          onPointerDown={(e) => {
            if (e.button !== 0) return;
            e.currentTarget.setPointerCapture(e.pointerId);
            pressTalk();
          }}
          onPointerUp={releaseTalk}
          onPointerCancel={releaseTalk}
          onLostPointerCapture={releaseTalk}
          onKeyDown={(e) => {
            if (!isTalkKey(e.key)) return;
            e.preventDefault();
            if (!e.repeat) pressTalk();
          }}
          onKeyUp={(e) => {
            if (!isTalkKey(e.key)) return;
            e.preventDefault();
            releaseTalk();
          }}
          onBlur={releaseTalk}
          onContextMenu={(e) => e.preventDefault()}
        >
          {ptt?.state === "thinking" && !holding ? (
            <Loader2 className="animate-spin" />
          ) : (
            <MessageCircle />
          )}
          {talkLabel(holding, ptt)}
        </Button>

        {ptt?.clip_at != null && (
          <div className="flex flex-col gap-1">
            <span className="truncate text-muted-foreground text-xs">
              Your last recording
              {ptt.last_heard ? `: “${ptt.last_heard}”` : ""}
            </span>
            {/* biome-ignore lint/a11y/useMediaCaption: the transcript is shown just above */}
            <audio
              key={ptt.clip_at}
              src={`${BACKEND}/voice/ptt/recording?t=${ptt.clip_at}`}
              controls
              preload="auto"
              className="w-full"
            />
          </div>
        )}

        <div className="flex flex-wrap gap-2">
          <Button
            onClick={toggleListening}
            disabled={toggling || (!connected && !enabled)}
            variant={enabled ? "destructive" : "default"}
          >
            {toggling ? (
              <Loader2 className="animate-spin" />
            ) : enabled ? (
              <MicOff />
            ) : (
              <Mic />
            )}
            {enabled ? "Stop “Hey Boxy”" : "Listen for “Hey Boxy”"}
          </Button>
          <Button
            variant="outline"
            onClick={hearMic}
            disabled={!connected || recording}
          >
            {recording ? <Loader2 className="animate-spin" /> : <Ear />}
            {recording
              ? `Recording ${RECORD_SECONDS}s…`
              : `Hear the mic (${RECORD_SECONDS}s)`}
          </Button>
        </div>

        {enabled && (
          <div
            className="h-1.5 w-full overflow-hidden rounded-full bg-muted"
            title="Microphone level"
            aria-hidden="true"
          >
            <div
              className="h-full rounded-full bg-primary transition-[width] duration-150"
              style={{ width: `${level * 100}%` }}
            />
          </div>
        )}

        {clipUrl && (
          // biome-ignore lint/a11y/useMediaCaption: raw microphone check, there is no transcript to caption
          <audio src={clipUrl} controls autoPlay className="w-full" />
        )}

        <p className="text-muted-foreground text-xs">
          {enabled
            ? "Say “Hey Boxy, …” near the dog. “Hey Boxy, stop” halts it at once."
            : "Hold the big button (or Space) and talk to the dog; release to get an answer."}
        </p>

        {recent.length > 0 && (
          <ul className="flex flex-col gap-2 text-sm">
            {recent.map((event) => (
              <li
                key={`${event.at}-${event.heard}`}
                className="rounded-md border p-2"
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate">
                    <span className="text-muted-foreground">You: </span>
                    {event.heard || "Hey Boxy"}
                  </span>
                  <Badge variant="outline">{event.intent}</Badge>
                </div>
                <div className="text-muted-foreground">Boxy: {event.reply}</div>
              </li>
            ))}
          </ul>
        )}

        {enabled && status?.last_ignored && (
          <p className="truncate text-muted-foreground text-xs">
            Ignored (no wake word): “{status.last_ignored}”
          </p>
        )}
      </CardContent>
    </Card>
  );
}
