"use client";

import { PanelLeft, PanelRight, XIcon } from "lucide-react";
import { useEffect, useState } from "react";
import { AvatarPanel } from "@/components/avatar/avatar-panel";
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

type Side = "left" | "right";

/** Open/closed state remembered per browser; storage can be unavailable. */
function usePanel(key: string, initial: boolean) {
  const [open, setOpen] = useState(initial);
  useEffect(() => {
    try {
      const saved = localStorage.getItem(key);
      if (saved !== null) setOpen(saved === "1");
    } catch {}
  }, [key]);
  const set = (next: boolean) => {
    setOpen(next);
    try {
      localStorage.setItem(key, next ? "1" : "0");
    } catch {}
  };
  return [open, set] as const;
}

/**
 * A sidebar that slides in from one edge. Beside the chat on wide screens,
 * over it (with a backdrop) on narrow ones.
 */
function SidePanel({
  id,
  side,
  open,
  onClose,
  title,
  extra,
  width = 360,
  children,
}: {
  id: string;
  side: Side;
  open: boolean;
  onClose: () => void;
  title: string;
  extra?: React.ReactNode;
  width?: number;
  children: React.ReactNode;
}) {
  const left = side === "left";
  return (
    <>
      {open && (
        <button
          type="button"
          aria-label={`Close ${title.toLowerCase()}`}
          className="fixed inset-0 z-30 bg-black/30 lg:hidden"
          onClick={onClose}
        />
      )}
      <aside
        id={id}
        aria-label={title}
        style={{ "--panel-w": `${width}px` } as React.CSSProperties}
        className={cn(
          "fixed inset-y-0 z-40 w-(--panel-w) max-w-[90vw] overflow-hidden bg-background transition-[translate,width] duration-300 ease-out lg:static lg:z-auto lg:max-w-none",
          left ? "left-0 order-first border-r" : "right-0 order-last border-l",
          open
            ? "translate-x-0 lg:w-(--panel-w)"
            : cn(
                "lg:w-0 lg:translate-x-0",
                left
                  ? "-translate-x-full lg:border-r-0"
                  : "translate-x-full lg:border-l-0",
              ),
        )}
      >
        {/* Fixed inner width, so content doesn't reflow while the panel slides. */}
        <div className="flex h-full w-(--panel-w) max-w-[90vw] flex-col">
          <div className="flex items-center gap-3 px-4 py-3">
            <span className="font-medium text-sm">{title}</span>
            {extra}
            <Button
              variant="ghost"
              size="icon-sm"
              className="ml-auto"
              aria-label={`Close ${title.toLowerCase()}`}
              onClick={onClose}
            >
              <XIcon />
            </Button>
          </div>
          {/* shrink-0 on every card: cards clip their overflow, so in a flex
              column they would otherwise squash to fit and never scroll. */}
          <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto overscroll-contain px-4 pb-4 *:shrink-0">
            {children}
          </div>
        </div>
      </aside>
    </>
  );
}

function PanelToggle({
  open,
  onToggle,
  controls,
  icon,
  label,
}: {
  open: boolean;
  onToggle: () => void;
  controls: string;
  icon: React.ReactNode;
  label: string;
}) {
  return (
    <Button
      variant={open ? "secondary" : "ghost"}
      size="sm"
      aria-expanded={open}
      aria-controls={controls}
      onClick={onToggle}
    >
      {icon}
      {label}
    </Button>
  );
}

/**
 * Full-page chat in the middle; the robot's 3D avatar in a left sidebar and
 * the robot controls (camera, status, teleop, voice, speaker, settings) in a
 * right one. Each sidebar opens and closes on its own.
 */
export function AppShell() {
  const [avatarOpen, setAvatarOpen] = usePanel("boxy:avatar-open", true);
  const [controlsOpen, setControlsOpen] = usePanel("boxy:controls-open", true);

  return (
    <div className="flex h-dvh overflow-hidden">
      <SidePanel
        id="robot-avatar"
        side="left"
        width={480}
        open={avatarOpen}
        onClose={() => setAvatarOpen(false)}
        title="Boxy"
        extra={<BatteryLevel />}
      >
        <AvatarPanel viewClassName="aspect-[4/5]" />
      </SidePanel>

      <main className="min-w-0 flex-1">
        <RobotChat
          actions={
            <>
              <PanelToggle
                open={avatarOpen}
                onToggle={() => setAvatarOpen(!avatarOpen)}
                controls="robot-avatar"
                icon={<PanelLeft data-icon="inline-start" />}
                label="Avatar"
              />
              <PanelToggle
                open={controlsOpen}
                onToggle={() => setControlsOpen(!controlsOpen)}
                controls="robot-controls"
                icon={<PanelRight data-icon="inline-start" />}
                label="Controls"
              />
            </>
          }
        />
      </main>

      <SidePanel
        id="robot-controls"
        side="right"
        open={controlsOpen}
        onClose={() => setControlsOpen(false)}
        title="Robot controls"
        extra={<BatteryLevel />}
      >
        <CameraPanel />
        <StatusCard />
        <TeleopCard />
        <VoiceCard />
        <SpeakCard />
        <SettingsCard />
      </SidePanel>
    </div>
  );
}
