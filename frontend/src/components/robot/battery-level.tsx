"use client";

import {
  Battery,
  BatteryFull,
  BatteryLow,
  BatteryMedium,
  BatteryWarning,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useRobot } from "./robot-context";

// Below this the dog should be put on charge soon.
const LOW = 20;

/** The robot's battery percentage with a matching icon; "--" when unknown. */
export function BatteryLevel({ className }: { className?: string }) {
  const { health } = useRobot();
  const soc = health?.battery_soc ?? null;

  const Icon =
    soc === null
      ? Battery
      : soc <= LOW
        ? BatteryWarning
        : soc < 50
          ? BatteryLow
          : soc < 85
            ? BatteryMedium
            : BatteryFull;

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 font-normal text-sm tabular-nums",
        soc !== null && soc <= LOW
          ? "text-destructive"
          : "text-muted-foreground",
        className,
      )}
      title={soc === null ? "Battery unknown" : `Battery ${soc}%`}
    >
      <Icon className="size-4" />
      {soc === null ? "--" : `${soc}%`}
    </span>
  );
}
