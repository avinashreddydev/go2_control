"use client";

import { OctagonX, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import { useRobot } from "./robot-context";

function Row({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-center justify-between text-sm">
      <span className="text-muted-foreground">{label}</span>
      {children}
    </div>
  );
}

function statusVariant(status: string | undefined) {
  if (status === "connected" || status === "active" || status === "online")
    return "default";
  if (status === "failed" || status === "offline") return "destructive";
  return "secondary";
}

export function StatusCard() {
  const { server, health, refresh } = useRobot();
  const battery = health?.battery_soc ?? null;

  const reconnect = async () => {
    try {
      await api.reconnect();
      toast("Reconnecting to the robot…");
    } catch (e) {
      toast.error(`Reconnect failed: ${(e as Error).message}`);
    }
    refresh();
  };

  const emergencyStop = async () => {
    await api.stopAll();
    toast("Stop sent");
  };

  return (
    <Card size="sm">
      <CardHeader>
        <CardTitle>Status</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-2">
        <Row label="Backend">
          <Badge variant={statusVariant(server)}>{server}</Badge>
        </Row>
        <Row label="Robot">
          <Badge variant={statusVariant(health?.robot_status)}>
            {health?.robot_status ?? "--"}
          </Badge>
        </Row>
        <Row label="Video">
          <Badge variant={statusVariant(health?.stream_status)}>
            {health?.stream_status ?? "--"}
          </Badge>
        </Row>
        <Row label="Battery">
          <span className="font-mono">
            {battery === null ? "--" : `${battery}%`}
          </span>
        </Row>
        <div className="mt-2 flex gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={reconnect}
            disabled={server !== "online"}
            className="flex-1"
          >
            <RefreshCw /> Reconnect
          </Button>
          <Button
            variant="destructive"
            size="sm"
            onClick={emergencyStop}
            disabled={server !== "online"}
            className="flex-1"
          >
            <OctagonX /> Stop
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
