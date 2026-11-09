"use client";

import { createContext, type ReactNode, use } from "react";
import { type ServerStatus, useRobotHealth } from "@/hooks/use-robot-health";
import type { Health } from "@/lib/api";

type RobotContextValue = {
  server: ServerStatus;
  health: Health | null;
  /** Backend is up and the WebRTC link to the dog is established. */
  connected: boolean;
  refresh: () => void;
};

const RobotContext = createContext<RobotContextValue | null>(null);

export function RobotProvider({ children }: { children: ReactNode }) {
  const { server, health, refresh } = useRobotHealth();
  const connected = server === "online" && !!health?.connected;
  return (
    <RobotContext value={{ server, health, connected, refresh }}>
      {children}
    </RobotContext>
  );
}

export function useRobot() {
  const ctx = use(RobotContext);
  if (!ctx) throw new Error("useRobot must be used inside <RobotProvider>");
  return ctx;
}
