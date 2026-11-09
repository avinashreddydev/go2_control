"use client";

import { Loader2, Volume2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import { useRobot } from "./robot-context";

export function SpeakCard() {
  const { connected } = useRobot();
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);

  const speak = async (e: React.FormEvent) => {
    e.preventDefault();
    const value = text.trim();
    if (!value) return;
    setBusy(true);
    try {
      await api.speak(value);
      setText("");
    } catch (err) {
      toast.error(`Speak failed: ${(err as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card size="sm">
      <CardHeader>
        <CardTitle>Speaker</CardTitle>
      </CardHeader>
      <CardContent>
        <form onSubmit={speak} className="flex gap-2">
          <Input
            value={text}
            onChange={(e) => setText(e.target.value)}
            maxLength={500}
            placeholder="Say something through the dog…"
            disabled={!connected || busy}
          />
          <Button
            type="submit"
            size="icon"
            disabled={!connected || busy || !text.trim()}
          >
            {busy ? <Loader2 className="animate-spin" /> : <Volume2 />}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
