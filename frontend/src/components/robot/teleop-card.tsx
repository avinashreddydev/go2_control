"use client";

import {
  ArrowDown,
  ArrowLeftToLine,
  ArrowRightToLine,
  ArrowUp,
  RotateCcw,
  RotateCw,
} from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardAction,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Switch } from "@/components/ui/switch";
import { TELEOP_KEYS, type TeleopKey, useTeleop } from "@/hooks/use-teleop";
import { useRobot } from "./robot-context";

const PAD: (TeleopKey | null)[] = ["a", "w", "d", "j", "s", "l"];
const ICONS: Record<TeleopKey, React.ReactNode> = {
  w: <ArrowUp />,
  s: <ArrowDown />,
  a: <RotateCcw />,
  d: <RotateCw />,
  j: <ArrowLeftToLine />,
  l: <ArrowRightToLine />,
};

export function TeleopCard() {
  const { connected } = useRobot();
  const [enabled, setEnabled] = useState(false);
  const active = enabled && connected;
  const { held, press, release } = useTeleop(active);

  return (
    <Card size="sm">
      <CardHeader>
        <CardTitle>Teleop</CardTitle>
        <CardAction>
          <div className="flex items-center gap-2 text-muted-foreground text-xs">
            <label htmlFor="teleop-keyboard">Keyboard</label>
            <Switch
              id="teleop-keyboard"
              checked={enabled}
              onCheckedChange={setEnabled}
              disabled={!connected}
            />
          </div>
        </CardAction>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <div className="grid grid-cols-3 gap-2">
          {PAD.map((key, i) =>
            key ? (
              <Button
                key={key}
                variant={held.has(key) ? "default" : "outline"}
                disabled={!active}
                title={`${TELEOP_KEYS[key]} (${key.toUpperCase()})`}
                className="h-12 touch-none flex-col gap-0.5"
                onPointerDown={(e) => {
                  e.currentTarget.setPointerCapture(e.pointerId);
                  press(key);
                }}
                onPointerUp={() => release(key)}
                onPointerCancel={() => release(key)}
                onLostPointerCapture={() => release(key)}
              >
                {ICONS[key]}
                <span className="font-mono text-xs">{key.toUpperCase()}</span>
              </Button>
            ) : (
              // biome-ignore lint/suspicious/noArrayIndexKey: static spacer
              <div key={i} />
            ),
          )}
        </div>
        <p className="text-muted-foreground text-xs">
          Hold W/S to walk, A/D to turn, J/L to strafe. Released on blur.
        </p>
      </CardContent>
    </Card>
  );
}
