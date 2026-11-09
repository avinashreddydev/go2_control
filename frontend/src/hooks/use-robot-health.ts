"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, type Health } from "@/lib/api";

export type ServerStatus = "starting" | "online" | "offline";

const POLL_ONLINE_MS = 2000;
const POLL_STARTING_MS = 1500;
const STARTUP_GRACE_MS = 30_000;

/** Polls /health; pauses while the tab is hidden. */
export function useRobotHealth() {
  const [server, setServer] = useState<ServerStatus>("starting");
  const [health, setHealth] = useState<Health | null>(null);
  const pollRef = useRef<() => void>(() => {});

  useEffect(() => {
    let timeout: ReturnType<typeof setTimeout>;
    let cancelled = false;
    let wasOnline = false;
    const mountedAt = Date.now();

    const poll = async () => {
      clearTimeout(timeout);
      try {
        const data = await api.health();
        if (cancelled) return;
        wasOnline = true;
        setServer("online");
        setHealth(data);
      } catch {
        if (cancelled) return;
        setServer(
          !wasOnline && Date.now() - mountedAt < STARTUP_GRACE_MS
            ? "starting"
            : "offline",
        );
        setHealth(null);
      }
      if (!cancelled && document.visibilityState === "visible") {
        timeout = setTimeout(
          poll,
          wasOnline ? POLL_ONLINE_MS : POLL_STARTING_MS,
        );
      }
    };
    pollRef.current = poll;

    const onVisibility = () => {
      if (document.visibilityState === "visible") poll();
      else clearTimeout(timeout);
    };

    poll();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      cancelled = true;
      clearTimeout(timeout);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, []);

  const refresh = useCallback(() => pollRef.current(), []);

  return { server, health, refresh };
}
