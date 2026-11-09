"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";

// Keys the backend understands (backend/services/robot.py TELEOP_KEYS).
export const TELEOP_KEYS = {
  w: "Forward",
  s: "Backward",
  a: "Turn left",
  d: "Turn right",
  j: "Strafe left",
  l: "Strafe right",
} as const;
export type TeleopKey = keyof typeof TELEOP_KEYS;

const isTeleopKey = (k: string): k is TeleopKey => k in TELEOP_KEYS;

function isTyping(target: EventTarget | null) {
  if (!(target instanceof HTMLElement)) return false;
  return (
    target.isContentEditable ||
    ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)
  );
}

/**
 * Hold-to-drive teleop. Keyboard input is only captured while `enabled`;
 * on-screen buttons call press/release directly. Everything is released on
 * disable, window blur and unmount so the robot never keeps walking.
 */
export function useTeleop(enabled: boolean) {
  const heldRef = useRef(new Set<TeleopKey>());
  const [held, setHeld] = useState<ReadonlySet<TeleopKey>>(new Set());

  const sync = useCallback(() => setHeld(new Set(heldRef.current)), []);

  const press = useCallback(
    (key: TeleopKey) => {
      if (heldRef.current.has(key)) return;
      heldRef.current.add(key);
      sync();
      api.teleopKey(key, true).catch(() => {});
    },
    [sync],
  );

  const release = useCallback(
    (key: TeleopKey) => {
      if (!heldRef.current.delete(key)) return;
      sync();
      api.teleopKey(key, false).catch(() => {});
    },
    [sync],
  );

  const releaseAll = useCallback(() => {
    heldRef.current.clear();
    sync();
    api.teleopRelease().catch(() => {});
  }, [sync]);

  useEffect(() => {
    if (!enabled) return;

    // Some keydown/keyup events carry no `key` (Chrome autofill, IME, some
    // extensions); they are not teleop presses, so ignore them.
    const onDown = (e: KeyboardEvent) => {
      const key = e.key?.toLowerCase();
      if (!key || e.repeat || isTyping(e.target) || !isTeleopKey(key)) return;
      e.preventDefault();
      press(key);
    };
    const onUp = (e: KeyboardEvent) => {
      const key = e.key?.toLowerCase();
      if (key && isTeleopKey(key)) release(key);
    };

    window.addEventListener("keydown", onDown);
    window.addEventListener("keyup", onUp);
    window.addEventListener("blur", releaseAll);
    return () => {
      window.removeEventListener("keydown", onDown);
      window.removeEventListener("keyup", onUp);
      window.removeEventListener("blur", releaseAll);
      releaseAll();
    };
  }, [enabled, press, release, releaseAll]);

  return { held, press, release, releaseAll };
}
