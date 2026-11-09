"use client";

import { PanelRight, XIcon } from "lucide-react";
import { useEffect, useState } from "react";
import { RobotChat } from "@/components/chat/robot-chat";
import { BatteryLevel } from "@/components/robot/battery-level";
import { CameraPanel } from "@/components/robot/camera-panel";
import { SettingsCard } from "@/components/robot/settings-card";
import { SpeakCard } from "@/components/robot/speak-card";
import { StatusCard } from "@/components/robot/status-card";
import { TeleopCard } from "@/components/robot/teleop-card";
import { VoiceCard } from "@/components/robot/voice-card";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const SIDEBAR_KEY = "boxy:controls-open";

/**
 * Full-page chat, centered, with the robot controls (camera, status, teleop,
 * voice, speaker, settings) in a sidebar that slides in from the right. On
 * wide screens the sidebar sits beside the chat; on narrow ones it overlays.
 */
export function AppShell() {
  const [open, setOpen] = useState(true);

  // Remember the choice per browser; storage can be unavailable.
  useEffect(() => {
    try {
      const saved = localStorage.getItem(SIDEBAR_KEY);
      if (saved !== null) setOpen(saved === "1");
    } catch {}
  }, []);

  const toggle = (next: boolean) => {
    setOpen(next);
    try {
      localStorage.setItem(SIDEBAR_KEY, next ? "1" : "0");
    } catch {}
  };

  return (
    <div className="flex h-dvh overflow-hidden">
      <main className="min-w-0 flex-1">
        <RobotChat
          actions={
            <Button
              variant={open ? "secondary" : "ghost"}
              size="sm"
              aria-expanded={open}
              aria-controls="robot-controls"
              onClick={() => toggle(!open)}
            >
              <PanelRight data-icon="inline-start" />
              Controls
            </Button>
          }
        />
      </main>

      {open && (
        <button
          type="button"
          aria-label="Close controls"
          className="fixed inset-0 z-30 bg-black/30 lg:hidden"
          onClick={() => toggle(false)}
        />
      )}

      <aside
        id="robot-controls"
        aria-label="Robot controls"
        className={cn(
          "fixed inset-y-0 right-0 z-40 w-[360px] max-w-[90vw] overflow-hidden border-l bg-background transition-[translate,width] duration-300 ease-out lg:static lg:z-auto lg:max-w-none",
          open
            ? "translate-x-0 lg:w-[360px]"
            : "translate-x-full lg:w-0 lg:translate-x-0 lg:border-l-0",
        )}
      >
        {/* Fixed inner width, so content doesn't reflow while the panel slides. */}
        <div className="flex h-full w-[360px] max-w-[90vw] flex-col">
          <div className="flex items-center justify-between px-4 py-3">
            <span className="font-medium text-sm">Robot controls</span>
            <BatteryLevel className="mr-auto ml-3" />
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="Close controls"
              onClick={() => toggle(false)}
            >
              <XIcon />
            </Button>
          </div>
          {/* shrink-0 on every card: cards clip their overflow, so in a flex
              column they would otherwise squash to fit and never scroll. */}
          <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto overscroll-contain px-4 pb-4 *:shrink-0">
            <CameraPanel />
            <StatusCard />
            <TeleopCard />
            <VoiceCard />
            <SpeakCard />
            <SettingsCard />
          </div>
        </div>
      </aside>
    </div>
  );
}
