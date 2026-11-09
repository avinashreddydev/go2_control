"use client";

import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { api, type Velocities, type VisionStatus } from "@/lib/api";
import { useRobot } from "./robot-context";

const FIELDS: { key: keyof Velocities; label: string }[] = [
  { key: "move", label: "Walk speed" },
  { key: "rotate", label: "Turn speed" },
  { key: "strafe", label: "Strafe speed" },
];

export function SettingsCard() {
  const { server, health } = useRobot();
  const online = server === "online";
  const [velocities, setVelocities] = useState<Velocities | null>(null);
  const [vision, setVision] = useState<VisionStatus | null>(null);
  const saveTimer = useRef<ReturnType<typeof setTimeout>>(undefined);

  useEffect(() => {
    if (!online) return;
    api.velocities().then(setVelocities, () => {});
    api.visionStatus().then(setVision, () => {});
  }, [online]);

  const updateVelocity = (key: keyof Velocities, value: number) => {
    setVelocities((v) => (v ? { ...v, [key]: value } : v));
    // Debounce: a drag fires many changes, the backend only needs the last.
    clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(() => {
      api.setVelocities({ [key]: value }).catch((e) => toast.error(e.message));
    }, 200);
  };

  const toggleVision = async () => {
    try {
      setVision(await api.visionToggle());
    } catch (e) {
      toast.error((e as Error).message);
    }
  };

  const visionOn = vision?.active ?? health?.vision_active ?? false;

  return (
    <Card size="sm">
      <CardHeader>
        <CardTitle>Settings</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {FIELDS.map(({ key, label }) => (
          <div key={key} className="flex flex-col gap-2">
            <div className="flex justify-between text-sm">
              <span className="text-muted-foreground">{label}</span>
              <span className="font-mono">
                {velocities ? velocities[key].toFixed(1) : "--"}
              </span>
            </div>
            <Slider
              min={0.1}
              max={1}
              step={0.1}
              value={velocities ? velocities[key] : 0.1}
              disabled={!online || !velocities}
              onValueChange={(v) =>
                updateVelocity(key, Array.isArray(v) ? v[0] : v)
              }
            />
          </div>
        ))}
        <div className="flex items-center justify-between text-sm">
          <label htmlFor="vision-toggle" className="text-muted-foreground">
            YOLOE detection
            {vision && !vision.available && " (not installed)"}
          </label>
          <Switch
            id="vision-toggle"
            checked={visionOn}
            onCheckedChange={toggleVision}
            disabled={!online || (vision !== null && !vision.available)}
          />
        </div>
      </CardContent>
    </Card>
  );
}
